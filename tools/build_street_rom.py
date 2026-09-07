#!/usr/bin/env python3
"""ASCII16 Screen5 street-walker ROM — MSX2+ / V9958, single page (SP2=0).

World size from street_world_meta.json (scale-locked town+harbour map).
VRAM = 256x256 circular buffer (one Scr5 page).

V scroll: R#23 + one full 128-byte edge line from ROM (circular H layout).
H scroll: USE_HW_HSCROLL selects path:
  True  — R#26/R#27 from camx (Yamaha polarity) + edge column strip from ROM
  False — R#26/R#27=0 + full-page REFILL_H (safe fallback)

V9958 Scr5 (G4) H-scroll, SP2=0, MSK=1 (verified vs Yamaha V9958 manual):
  Display origin = ((R26<<3) - R27) & 255
  Want origin == (camx & 255), so:
    R27 = (-camx) & 7
    R26 = ((camx + 7) >> 3) & 0x1F   # H08 unused/ignored when SP2=0
  Flip R27_NEGATE only together with matching R26 mode after a HW test.
  Circular invariant: VRAM[(camx+i)&255] = world[camx+i]
  Strip (cam right +d): fill VRAM[ocamx&255 ..] from world[ocamx+256], width d
  Strip (cam left  -d): fill VRAM[camx&255  ..] from world[camx], width d

R#18 is NOT H-scroll (adjust only). Written once to 0 at init; never used for scroll.
FILL_LINE uses OUTI+NOP+JR (not OTIR). No dual-page SP2. R#25 MSK=1 SP2=0.
No player sprite — GTSTCK moves camera only. Sprites disabled (R#8 SPD).
"""
from pathlib import Path
import json
import math
from music_player import emit_music, MUS_TEMPO, SONG_LEN, expected_opening_fm_writes

ROOT = Path("/workspace/medemblik-msx2")
world = (ROOT / "data" / "street_world.bin").read_bytes()
meta = json.load(open(ROOT / "data" / "street_world_meta.json"))

WORLD_W, WORLD_H = meta["world"]
VIEW_W, VIEW_H = meta["view"]
BPL = meta["bpl"]
BPL_STOR = meta.get("bpl_stor", BPL)
sx0, sy0 = meta["spawn"]
assert len(world) == WORLD_H * BPL
assert VIEW_W == 256 and VIEW_H == 212
assert WORLD_W % 2 == 0 and BPL == WORLD_W // 2
assert BPL_STOR >= BPL
assert BPL_STOR & (BPL_STOR - 1) == 0
assert 16384 % BPL_STOR == 0

BANK_SIZE = 16384
LINES_PER_BANK = BANK_SIZE // BPL_STOR
assert LINES_PER_BANK >= 1 and (LINES_PER_BANK & (LINES_PER_BANK - 1)) == 0
MAP_BANKS = (WORLD_H + LINES_PER_BANK - 1) // LINES_PER_BANK
_need = 1 + MAP_BANKS
TOTAL_BANKS = 64 if _need <= 64 else 128 if _need <= 128 else 256
assert MAP_BANKS + 1 <= TOTAL_BANKS
assert MAP_BANKS < 255

MAP_H = 212
INIT_LINES = 256
PAGE_BPL = 128

SPD = 2

# HW H-scroll: R#26/R#27 + circular edge strip. Prior CRT failure was wrong
# R27 polarity (R27=camx&7) vs strip math that assumed origin==camx.
# Yamaha formula restored; set False to fall back to REFILL_H.
USE_HW_HSCROLL = True

# R27 polarity for WR_HSCROLL (must match R26 mode below):
#   True  — Yamaha: R27=(-camx)&7, R26=((camx+7)>>3)&0x1F  → origin=camx
#   False — direct: R27=camx&7,   R26=(camx>>3)&0x1F       → origin wrong (old bug)
R27_NEGATE = True


class Asm:
    def __init__(self, org=0x4000):
        self.org = self.pc = org
        self.buf = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, *bs):
        for b in bs:
            self.buf.append(b & 0xFF)
            self.pc += 1

    def label(self, n):
        assert n not in self.labels, n
        self.labels[n] = self.pc

    def word(self, n):
        self.fixups.append((len(self.buf), n, False))
        self.emit(0, 0)

    def jr(self, n, c=None):
        op = {None: 0x18, "z": 0x28, "nz": 0x20, "c": 0x38, "nc": 0x30}[c]
        self.emit(op, 0)
        self.fixups.append((len(self.buf) - 1, n, True))

    def djnz(self, n):
        self.emit(0x10, 0)
        self.fixups.append((len(self.buf) - 1, n, True))

    def jp(self, n, c=None):
        op = {
            None: 0xC3, "z": 0xCA, "nz": 0xC2, "c": 0xDA, "nc": 0xD2,
            "m": 0xFA, "p": 0xF2,
        }[c]
        self.emit(op)
        self.word(n)

    def call(self, n):
        self.emit(0xCD)
        self.word(n)

    def call_abs(self, addr):
        self.emit(0xCD, addr & 0xFF, (addr >> 8) & 0xFF)

    def resolve(self):
        for pos, n, rel in self.fixups:
            assert n in self.labels, f"undefined label {n!r}"
            t = self.labels[n]
            if rel:
                off = t - (self.org + pos + 1)
                assert -128 <= off <= 127, f"jr out of range: {n} off={off}"
                self.buf[pos] = off & 0xFF
            else:
                self.buf[pos] = t & 0xFF
                self.buf[pos + 1] = (t >> 8) & 0xFF
        return bytes(self.buf)


CHGMOD, GTSTCK = 0x005F, 0x00D5
CHPUT, CLS, POSIT, SNSMAT = 0x00A2, 0x00C3, 0x00C6, 0x0141

# Work RAM
CAMX, CAMY = 0xE000, 0xE002
MAP_R23 = 0xE008
OCAMX, OCAMY = 0xE00A, 0xE00C
WY = 0xE016
VL = 0xE018
LINEC = 0xE019
SCRI = 0xE01C
# strip fill
BOFF = 0xE01E  # byte: VRAM X byte offset 0..127
NBYT = 0xE01F  # byte: strip width in bytes
SOFF = 0xE020  # word: ROM source X byte = world_x/2 for strip
# Music player (title + map); cheap per-frame tick
MUS_FLAGS = 0xE030  # bit0=OPLL; bit1=playing; bit2=external FM-PAC (7FF6)
MUS_SLOT = 0xE031   # detected OPLL slot id
MUS_DIV = 0xE032    # frame divider countdown (tempo)
MUS_POS = 0xE033    # song event index 0..SONG_LEN-1
MUS_WAIT = 0xE034   # beats left in current duration event
MUS_LAST0 = 0xE035  # last note per FM/PSG ch (skip re-key if unchanged)
MUS_LAST1 = 0xE036
MUS_LAST2 = 0xE037
EXPTBL = 0xFCC1
RDSLT, WRSLT = 0x000C, 0x0014

camx0 = max(0, min(WORLD_W - VIEW_W, sx0 - VIEW_W // 2)) & ~1
camy0 = max(0, min(WORLD_H - MAP_H, sy0 - MAP_H // 2))

MAX_CAMX = WORLD_W - VIEW_W
MAX_CAMY = WORLD_H - MAP_H

a = Asm(0x4000)

a.emit(0x41, 0x42)
a.word("INIT")
a.emit(*([0] * 12))

# -------------------- INIT --------------------
# Boot: Screen 0 title → SPACE starts map; ESC → soft exit (JP 0000h).
a.label("INIT")
a.emit(0xF3)                              # DI
a.emit(0xAF)
a.emit(0x32, 0x00, 0x60)                  # ASCII16 page4000 = bank0
a.emit(0x3E, 1)
a.emit(0x32, 0x00, 0x70)                  # page8000 = bank1
a.call("MUSIC_INIT")                      # detect MSX-MUSIC / PSG fallback; start loop

# --- Title screen (Scr0 text; no sprites) ---
a.emit(0xAF)                              # A=0 Screen 0
a.call_abs(CHGMOD)
a.call_abs(CLS)
a.call("DRAW_TITLE")
a.label("TITLE_WAIT")
a.call("VWAIT")                           # keep music tempo stable on title
a.call("MUSIC_TICK")
# Escape (matrix row 7, bit2=0 when pressed) → soft exit
a.emit(0x3E, 7)
a.call_abs(SNSMAT)
a.emit(0xE6, 0x04)
a.jp("SOFT_EXIT", "z")
# Space (row 8, bit0=0 when pressed) → start map
a.emit(0x3E, 8)
a.call_abs(SNSMAT)
a.emit(0xE6, 0x01)
a.jr("TITLE_WAIT", "nz")

# --- Enter map viewer (unchanged scroll engine path) ---
a.label("START_MAP")
a.emit(0xF3)                              # DI (BIOS may have EI)
a.emit(0x3E, 5)                           # Screen 5
a.call_abs(CHGMOD)
a.call("SET_PAL")
a.call("DIS_SPRITES")                     # R#8 SPD=1 — no sprite garbage

for val, addr in ((camx0, CAMX), (camy0, CAMY)):
    a.emit(0x21, val & 0xFF, (val >> 8) & 0xFF)
    a.emit(0x22, addr & 0xFF, (addr >> 8) & 0xFF)

a.emit(0xAF)
a.emit(0x32, MAP_R23 & 0xFF, MAP_R23 >> 8)
a.call("WR_R18_ZERO")
a.call("SET_SINGLE")                      # R#25 MSK=1 SP2=0; R#2 page0
a.call("WR_R23")
a.call("WR_HSCROLL")
a.call("INIT_BLIT")
a.emit(0xFB)                              # EI

a.label("MAIN")
a.call("VWAIT")
# Escape during map → soft exit (same as title)
a.emit(0x3E, 7)
a.call_abs(SNSMAT)
a.emit(0xE6, 0x04)
a.jp("SOFT_EXIT", "z")
# Snapshot old cam, then input, THEN program scroll regs for NEW camx/camy
# before edge fills — so entering columns sit on the revealed side, not the
# still-visible leaving edge (prior order corrupted the left strip for 1 frame).
a.emit(0x2A, CAMX & 0xFF, CAMX >> 8)
a.emit(0x22, OCAMX & 0xFF, OCAMX >> 8)
a.emit(0x2A, CAMY & 0xFF, CAMY >> 8)
a.emit(0x22, OCAMY & 0xFF, OCAMY >> 8)
a.call("INPUT")
a.call("WR_R23")
a.call("WR_HSCROLL")                      # R#26/27 (or 0) for current camx
a.call("SCROLL_V")
if USE_HW_HSCROLL:
    # SCROLL_H no-ops when camx==ocamx (pure vertical must not touch H strips).
    a.call("SCROLL_H")                    # edge strip only if camx changed
else:
    a.call("REFILL_H")
a.call("MUSIC_TICK")                      # after scroll — must stay cheap
a.jp("MAIN")

# Soft return to BASIC / BIOS warm entry (cartridge-safe).
a.label("SOFT_EXIT")
a.emit(0xF3)                              # DI
a.call("MUSIC_MUTE")                      # silence FM + PSG before warm restart
a.emit(0xC3, 0x00, 0x00)                  # JP 0000h

# -------------------- Title helpers (Scr0) --------------------
a.label("DRAW_TITLE")
# POSIT: H=row+1, L=col+1. Scr0 width 40.
# "Medemblik" (9) centered → col 16 → L=16, row 8 → H=9
a.emit(0x21, 16, 9)
a.call_abs(POSIT)
a.emit(0x21)
a.word("STR_TITLE")
a.call("PRINT_Z")
# prompt row 12 (H=13), col 10 (L=10): "SPACE / DRUK Spatie"
a.emit(0x21, 10, 13)
a.call_abs(POSIT)
a.emit(0x21)
a.word("STR_PROMPT")
a.call("PRINT_Z")
a.emit(0xC9)

a.label("PRINT_Z")
# HL → NUL-terminated ASCII; CHPUT each char
a.label("PZ_LP")
a.emit(0x7E)                              # A=(HL)
a.emit(0xB7)
a.jr("PZ_DONE", "z")
a.emit(0xE5)
a.call_abs(CHPUT)
a.emit(0xE1)
a.emit(0x23)
a.jr("PZ_LP")
a.label("PZ_DONE")
a.emit(0xC9)

a.label("STR_TITLE")
a.emit(*list(b"Medemblik\x00"))
a.label("STR_PROMPT")
a.emit(*list(b"SPACE / DRUK Spatie\x00"))

# -------------------- VDP helpers --------------------
a.label("SETWR2")
a.emit(0x7C)
a.emit(0xE6, 0xC0)
a.emit(0x07)
a.emit(0x07)
a.emit(0xD3, 0x99)
a.emit(0x3E, 0x8E)
a.emit(0xD3, 0x99)
a.emit(0x7D)
a.emit(0xD3, 0x99)
a.emit(0x7C)
a.emit(0xE6, 0x3F)
a.emit(0xF6, 0x40)
a.emit(0xD3, 0x99)
a.emit(0xC9)

a.label("WR_R23")
a.emit(0x3A, MAP_R23 & 0xFF, MAP_R23 >> 8)
a.emit(0xD3, 0x99)
a.emit(0x3E, 0x97)
a.emit(0xD3, 0x99)
a.emit(0xC9)

a.label("WR_R18_ZERO")
a.emit(0xAF)
a.emit(0xD3, 0x99)
a.emit(0x3E, 0x92)
a.emit(0xD3, 0x99)
a.emit(0xC9)

a.label("SET_SINGLE")
a.emit(0x3E, 0x02)                        # MSK=1 SP2=0
a.emit(0xD3, 0x99)
a.emit(0x3E, 0x99)
a.emit(0xD3, 0x99)
a.emit(0x3E, 0x1F)                        # R#2 page0
a.emit(0xD3, 0x99)
a.emit(0x3E, 0x82)
a.emit(0xD3, 0x99)
a.emit(0xC9)

a.label("DIS_SPRITES")
# R#8: keep VR (bit3), set SPD (bit1) → sprites off.
# Do NOT poke SAT at 7600h — overlaps Scr5 bitmap page.
a.emit(0x3E, 0x0A)                        # VR|SPD
a.emit(0xD3, 0x99)
a.emit(0x3E, 0x88)                        # R#8
a.emit(0xD3, 0x99)
a.emit(0xC9)

a.label("WR_HSCROLL")
if USE_HW_HSCROLL:
    # Scr5/G4 SP2=0. origin=((R26<<3)-R27)&255 must equal camx&255.
    # R#18 unused for scroll (adjust only; left 0 by WR_R18_ZERO).
    a.emit(0x2A, CAMX & 0xFF, CAMX >> 8)  # HL = camx
    if R27_NEGATE:
        # R27 = (-camx) & 7
        a.emit(0x7D)                      # A = camx low
        a.emit(0xED, 0x44)                # NEG
        a.emit(0xE6, 0x07)
    else:
        # R27 = camx & 7  (legacy; pairs with R26=camx>>3)
        a.emit(0x7D)
        a.emit(0xE6, 0x07)
    a.emit(0xD3, 0x99)
    a.emit(0x3E, 0x9B)                    # R#27
    a.emit(0xD3, 0x99)
    a.emit(0x2A, CAMX & 0xFF, CAMX >> 8)
    if R27_NEGATE:
        # R26 = ((camx + 7) >> 3) & 0x1F
        a.emit(0x01, 7, 0)
        a.emit(0x09)                      # HL = camx + 7
    for _ in range(3):
        a.emit(0xCB, 0x3C)                # SRL H
        a.emit(0xCB, 0x1D)                # RR L
    a.emit(0x7D)
    a.emit(0xE6, 0x1F)                    # SP2=0: 5-bit HO (H08 must stay 0)
    a.emit(0xD3, 0x99)
    a.emit(0x3E, 0x9A)                    # R#26
    a.emit(0xD3, 0x99)
else:
    # Fallback: no HW H-scroll
    a.emit(0xAF)
    a.emit(0xD3, 0x99)
    a.emit(0x3E, 0x9B)
    a.emit(0xD3, 0x99)
    a.emit(0xAF)
    a.emit(0xD3, 0x99)
    a.emit(0x3E, 0x9A)
    a.emit(0xD3, 0x99)
a.emit(0xC9)

PAL = [
    (0, 0, 0), (1, 2, 6), (1, 5, 1), (1, 4, 1),
    (3, 3, 3), (4, 4, 4), (7, 6, 1), (5, 2, 1),
    (4, 1, 1), (7, 6, 2), (0, 0, 1), (7, 7, 7),
    (5, 4, 2), (0, 3, 0), (2, 2, 2), (7, 7, 7),
]
a.label("SET_PAL")
for i, (r, g, b) in enumerate(PAL):
    a.emit(0x3E, i)
    a.emit(0xD3, 0x99)
    a.emit(0x3E, 0x90)
    a.emit(0xD3, 0x99)
    a.emit(0x3E, ((r & 7) << 4) | (b & 7))
    a.emit(0xD3, 0x9A)
    a.emit(0x3E, g & 7)
    a.emit(0xD3, 0x9A)
a.emit(0xC9)

a.label("VWAIT")
a.emit(0xAF)
a.emit(0xD3, 0x99)
a.emit(0x3E, 0x8F)
a.emit(0xD3, 0x99)
a.label("VW_CLR")
a.emit(0xDB, 0x99)
a.emit(0xE6, 0x80)
a.jr("VW_CLR", "nz")
a.label("VW_SET")
a.emit(0xDB, 0x99)
a.emit(0xE6, 0x80)
a.jr("VW_SET", "z")
a.emit(0xC9)

LPB_SHIFT = int(math.log2(LINES_PER_BANK))
BPL_STOR_SHIFT = int(math.log2(BPL_STOR))
LPB_MASK = LINES_PER_BANK - 1
assert 1 << LPB_SHIFT == LINES_PER_BANK
assert 1 << BPL_STOR_SHIFT == BPL_STOR
assert BPL_STOR_SHIFT >= 8

a.label("MAP_SRC")
a.emit(0x2A, WY & 0xFF, WY >> 8)
for _ in range(LPB_SHIFT):
    a.emit(0xCB, 0x3C)
    a.emit(0xCB, 0x1D)
a.emit(0x7D)
a.emit(0xC6, 1)
a.emit(0x32, 0x00, 0x70)
a.emit(0x2A, WY & 0xFF, WY >> 8)
a.emit(0x7D)
a.emit(0xE6, LPB_MASK)
for _ in range(BPL_STOR_SHIFT - 8):
    a.emit(0x87)
a.emit(0x67)
a.emit(0x2E, 0)
a.emit(0x11, 0x00, 0x80)
a.emit(0x19)
a.emit(0xC9)

# OUT_N: B=count bytes from HL → VRAM port C=98h (OUTI+NOP+JR)
a.label("OUT_N")
a.label("OUT_N_LP")
a.emit(0xED, 0xA3)                        # OUTI
a.emit(0x00)                              # NOP
a.jr("OUT_N_LP", "nz")
a.emit(0xC9)

if USE_HW_HSCROLL:
    # FILL_LINE: circular — world[camx:camx+256] into VRAM line at (camx&255)
    a.label("FILL_LINE")
    a.emit(0xF3)
    a.call("MAP_SRC")
    a.emit(0xED, 0x5B, CAMX & 0xFF, CAMX >> 8)
    a.emit(0xCB, 0x3A)                    # SRL D
    a.emit(0xCB, 0x1B)                    # RR E => DE = camx/2
    a.emit(0x19)                          # HL = ROM line + camx/2
    a.emit(0xE5)                          # save ROM ptr
    # boff = (camx/2) & 127
    a.emit(0x7B)                          # A = E = camx/2 low
    a.emit(0xE6, 0x7F)
    a.emit(0x32, BOFF & 0xFF, BOFF >> 8)
    # VRAM addr = VL*128 + boff
    a.emit(0x3A, VL & 0xFF, VL >> 8)
    a.emit(0x67)
    a.emit(0x2E, 0)
    a.emit(0xCB, 0x3C)
    a.emit(0xCB, 0x1D)                    # HL = VL*128
    a.emit(0x3A, BOFF & 0xFF, BOFF >> 8)
    a.emit(0x85)
    a.emit(0x6F)
    a.emit(0x7C)
    a.emit(0xCE, 0)
    a.emit(0x67)
    a.call("SETWR2")
    a.emit(0xE1)                          # HL = ROM ptr (must survive n1 calc)
    a.emit(0x0E, 0x98)
    # n1 = 128 - boff — DO NOT LD HL,BOFF (that clobbered ROM ptr → stripes)
    a.emit(0x3A, BOFF & 0xFF, BOFF >> 8)  # A = boff
    a.emit(0x47)                          # B = boff
    a.emit(0x3E, PAGE_BPL)                # A = 128
    a.emit(0x90)                          # SUB B => A = 128 - boff
    a.emit(0x47)                          # B = n1
    a.emit(0xB7)
    a.jr("FL_PART2", "z")
    a.call("OUT_N")
    a.label("FL_PART2")
    # if boff != 0: write boff bytes at VL*128+0
    a.emit(0x3A, BOFF & 0xFF, BOFF >> 8)
    a.emit(0xB7)
    a.jr("FL_DONE", "z")
    a.emit(0xE5)                          # save continued ROM ptr
    a.emit(0x3A, VL & 0xFF, VL >> 8)
    a.emit(0x67)
    a.emit(0x2E, 0)
    a.emit(0xCB, 0x3C)
    a.emit(0xCB, 0x1D)
    a.call("SETWR2")
    a.emit(0xE1)
    a.emit(0x0E, 0x98)
    a.emit(0x3A, BOFF & 0xFF, BOFF >> 8)
    a.emit(0x47)
    a.call("OUT_N")
    a.label("FL_DONE")
    a.emit(0xFB)
    a.emit(0xC9)
else:
    a.label("FILL_LINE")
    a.emit(0xF3)
    a.call("MAP_SRC")
    a.emit(0xED, 0x5B, CAMX & 0xFF, CAMX >> 8)
    a.emit(0xCB, 0x3A)
    a.emit(0xCB, 0x1B)
    a.emit(0x19)
    a.emit(0xE5)
    a.emit(0x3A, VL & 0xFF, VL >> 8)
    a.emit(0x67)
    a.emit(0x2E, 0)
    a.emit(0xCB, 0x3C)
    a.emit(0xCB, 0x1D)
    a.call("SETWR2")
    a.emit(0xE1)
    a.emit(0x0E, 0x98)
    a.emit(0x06, PAGE_BPL)
    a.label("FL_OUT")
    a.emit(0xED, 0xA3)
    a.emit(0x00)
    a.jr("FL_OUT", "nz")
    a.emit(0xFB)
    a.emit(0xC9)

# INIT_BLIT: 256 lines
a.label("INIT_BLIT")
a.emit(0x2A, CAMY & 0xFF, CAMY >> 8)
a.emit(0x22, WY & 0xFF, WY >> 8)
a.emit(0xAF)
a.emit(0x32, VL & 0xFF, VL >> 8)
a.emit(0x3E, INIT_LINES & 0xFF)
a.emit(0x32, LINEC & 0xFF, LINEC >> 8)
a.label("IBL1")
a.call("FILL_LINE")
a.emit(0x21, VL & 0xFF, VL >> 8)
a.emit(0x34)
a.emit(0x2A, WY & 0xFF, WY >> 8)
a.emit(0x23)
a.emit(0xE5)
a.emit(0x01, (WORLD_H - 1) & 0xFF, (WORLD_H - 1) >> 8)
a.emit(0xA7)
a.emit(0xED, 0x42)
a.emit(0xE1)
a.jr("IBL_WY", "c")
a.jr("IBL_WY", "z")
a.emit(0x21, (WORLD_H - 1) & 0xFF, (WORLD_H - 1) >> 8)
a.label("IBL_WY")
a.emit(0x22, WY & 0xFF, WY >> 8)
a.emit(0x21, LINEC & 0xFF, LINEC >> 8)
a.emit(0x35)
a.jr("IBL1", "nz")
a.emit(0xC9)

# REFILL_H (fallback): rebuild all 256 lines if camx changed
a.label("REFILL_H")
a.emit(0x2A, CAMX & 0xFF, CAMX >> 8)
a.emit(0xED, 0x5B, OCAMX & 0xFF, OCAMX >> 8)
a.emit(0xA7)
a.emit(0xED, 0x52)
a.emit(0x7C)
a.emit(0xB5)
a.jp("RH_RET", "z")
a.emit(0xAF)
a.emit(0x32, SCRI & 0xFF, SCRI >> 8)
a.label("RH1")
a.emit(0x3A, MAP_R23 & 0xFF, MAP_R23 >> 8)
a.emit(0x21, SCRI & 0xFF, SCRI >> 8)
a.emit(0x86)
a.emit(0x32, VL & 0xFF, VL >> 8)
a.emit(0x2A, CAMY & 0xFF, CAMY >> 8)
a.emit(0x3A, SCRI & 0xFF, SCRI >> 8)
a.emit(0x85)
a.emit(0x6F)
a.emit(0x7C)
a.emit(0xCE, 0)
a.emit(0x67)
a.emit(0xE5)
a.emit(0x01, (WORLD_H - 1) & 0xFF, (WORLD_H - 1) >> 8)
a.emit(0xA7)
a.emit(0xED, 0x42)
a.emit(0xE1)
a.jr("RH_WY", "c")
a.jr("RH_WY", "z")
a.emit(0x21, (WORLD_H - 1) & 0xFF, (WORLD_H - 1) >> 8)
a.label("RH_WY")
a.emit(0x22, WY & 0xFF, WY >> 8)
a.call("FILL_LINE")
a.emit(0x21, SCRI & 0xFF, SCRI >> 8)
a.emit(0x34)
a.jr("RH1", "nz")
a.label("RH_RET")
a.emit(0xC9)

if USE_HW_HSCROLL:
    # FILL_STRIP: NBYT bytes at VRAM (VL*128+BOFF) from ROM WY at SOFF
    # for 256 circular buffer lines (SCRI=0..255)
    a.label("FILL_STRIP")
    a.emit(0xAF)
    a.emit(0x32, SCRI & 0xFF, SCRI >> 8)
    a.label("FST1")
    # VL = MAP_R23 + SCRI
    a.emit(0x3A, MAP_R23 & 0xFF, MAP_R23 >> 8)
    a.emit(0x21, SCRI & 0xFF, SCRI >> 8)
    a.emit(0x86)
    a.emit(0x32, VL & 0xFF, VL >> 8)
    # WY = CAMY + SCRI clamped
    a.emit(0x2A, CAMY & 0xFF, CAMY >> 8)
    a.emit(0x3A, SCRI & 0xFF, SCRI >> 8)
    a.emit(0x85)
    a.emit(0x6F)
    a.emit(0x7C)
    a.emit(0xCE, 0)
    a.emit(0x67)
    a.emit(0xE5)
    a.emit(0x01, (WORLD_H - 1) & 0xFF, (WORLD_H - 1) >> 8)
    a.emit(0xA7)
    a.emit(0xED, 0x42)
    a.emit(0xE1)
    a.jr("FST_WY", "c")
    a.jr("FST_WY", "z")
    a.emit(0x21, (WORLD_H - 1) & 0xFF, (WORLD_H - 1) >> 8)
    a.label("FST_WY")
    a.emit(0x22, WY & 0xFF, WY >> 8)
    a.emit(0xF3)
    a.call("MAP_SRC")
    a.emit(0xED, 0x5B, SOFF & 0xFF, SOFF >> 8)
    a.emit(0x19)                          # HL += SOFF
    a.emit(0xE5)
    # VRAM = VL*128 + BOFF
    a.emit(0x3A, VL & 0xFF, VL >> 8)
    a.emit(0x67)
    a.emit(0x2E, 0)
    a.emit(0xCB, 0x3C)
    a.emit(0xCB, 0x1D)
    a.emit(0x3A, BOFF & 0xFF, BOFF >> 8)
    a.emit(0x85)
    a.emit(0x6F)
    a.emit(0x7C)
    a.emit(0xCE, 0)
    a.emit(0x67)
    a.call("SETWR2")
    a.emit(0xE1)
    a.emit(0x0E, 0x98)
    a.emit(0x3A, NBYT & 0xFF, NBYT >> 8)
    a.emit(0x47)
    a.call("OUT_N")
    a.emit(0xFB)
    a.emit(0x21, SCRI & 0xFF, SCRI >> 8)
    a.emit(0x34)
    a.jr("FST1", "nz")
    a.emit(0xC9)

    # SCROLL_H: edge column strip for camx delta (even, typically ±SPD=2).
    # Invariant VRAM[(camx+i)&255]=world[camx+i]. After WR_HSCROLL to new camx:
    #  cam right +d: entering right cols → VRAM[(ocamx+i)&255] from world[ocamx+256+i]
    #  cam left  -d: entering left  cols → VRAM[(camx+i)&255]  from world[camx+i]
    a.label("SCROLL_H")
    a.emit(0x2A, CAMX & 0xFF, CAMX >> 8)
    a.emit(0xED, 0x5B, OCAMX & 0xFF, OCAMX >> 8)
    a.emit(0xA7)
    a.emit(0xED, 0x52)                    # HL = camx - ocamx
    a.emit(0x7C)
    a.emit(0xB5)
    a.jp("SH_RET", "z")
    a.emit(0x7C)
    a.emit(0xB7)
    a.jp("SH_LEFT", "m")
    # --- camera right (delta > 0)
    # boff = (ocamx/2) & 127; soff = ocamx/2 + 128; nbyt = delta/2
    a.emit(0x7D)                          # A = delta low (2,4,…)
    a.emit(0xCB, 0x3F)                    # SRL A => nbyt
    a.emit(0x32, NBYT & 0xFF, NBYT >> 8)
    a.emit(0x2A, OCAMX & 0xFF, OCAMX >> 8)
    a.emit(0xCB, 0x3C)
    a.emit(0xCB, 0x1D)                    # HL = ocamx/2
    a.emit(0x7D)
    a.emit(0xE6, 0x7F)
    a.emit(0x32, BOFF & 0xFF, BOFF >> 8)
    a.emit(0x11, 128, 0)
    a.emit(0x19)                          # HL = ocamx/2 + 128
    a.emit(0x22, SOFF & 0xFF, SOFF >> 8)
    a.call("FILL_STRIP")
    a.jp("SH_RET")

    a.label("SH_LEFT")
    # camera left: abs delta = ocamx - camx; refill at new left edge
    a.emit(0xED, 0x5B, CAMX & 0xFF, CAMX >> 8)
    a.emit(0x2A, OCAMX & 0xFF, OCAMX >> 8)
    a.emit(0xA7)
    a.emit(0xED, 0x52)                    # HL = abs delta
    a.emit(0x7D)
    a.emit(0xCB, 0x3F)
    a.emit(0x32, NBYT & 0xFF, NBYT >> 8)
    a.emit(0x2A, CAMX & 0xFF, CAMX >> 8)
    a.emit(0xCB, 0x3C)
    a.emit(0xCB, 0x1D)                    # HL = camx/2
    a.emit(0x22, SOFF & 0xFF, SOFF >> 8)
    a.emit(0x7D)
    a.emit(0xE6, 0x7F)
    a.emit(0x32, BOFF & 0xFF, BOFF >> 8)
    a.call("FILL_STRIP")
    a.label("SH_RET")
    a.emit(0xC9)

# -------------------- SCROLL_V --------------------
a.label("SCROLL_V")
a.emit(0x2A, CAMY & 0xFF, CAMY >> 8)
a.emit(0x7C)
a.emit(0xB7)
a.jp("SV_CAMY_POS", "p")
a.emit(0x21, 0, 0)
a.label("SV_CAMY_POS")
a.emit(0xE5)
a.emit(0x01, MAX_CAMY & 0xFF, MAX_CAMY >> 8)
a.emit(0xA7)
a.emit(0xED, 0x42)
a.emit(0xE1)
a.jr("SV_CAMY_OK", "c")
a.jr("SV_CAMY_OK", "z")
a.emit(0x21, MAX_CAMY & 0xFF, MAX_CAMY >> 8)
a.label("SV_CAMY_OK")
a.emit(0x22, CAMY & 0xFF, CAMY >> 8)

a.emit(0x2A, CAMY & 0xFF, CAMY >> 8)
a.emit(0xED, 0x5B, OCAMY & 0xFF, OCAMY >> 8)
a.emit(0xA7)
a.emit(0xED, 0x52)
a.emit(0x7C)
a.emit(0xB5)
a.jp("SV_RET", "z")
a.emit(0x7C)
a.emit(0xB7)
a.jp("SV_UP", "m")
# scroll down
a.emit(0x7D)
a.emit(0xFE, 3)
a.jr("SV_DN_ST", "c")
a.emit(0x3E, 2)
a.label("SV_DN_ST")
a.emit(0x32, SCRI & 0xFF, SCRI >> 8)
a.label("SV_DN1")
a.emit(0x21, MAP_R23 & 0xFF, MAP_R23 >> 8)
a.emit(0x34)
a.emit(0x2A, OCAMY & 0xFF, OCAMY >> 8)
a.emit(0x23)
a.emit(0xE5)
a.emit(0x01, MAX_CAMY & 0xFF, MAX_CAMY >> 8)
a.emit(0xA7)
a.emit(0xED, 0x42)
a.emit(0xE1)
a.jr("SV_DN_OY", "c")
a.jr("SV_DN_OY", "z")
a.emit(0x21, MAX_CAMY & 0xFF, MAX_CAMY >> 8)
a.label("SV_DN_OY")
a.emit(0x22, OCAMY & 0xFF, OCAMY >> 8)
a.emit(0x11, (MAP_H - 1) & 0xFF, (MAP_H - 1) >> 8)
a.emit(0x19)
a.emit(0xE5)
a.emit(0x01, (WORLD_H - 1) & 0xFF, (WORLD_H - 1) >> 8)
a.emit(0xA7)
a.emit(0xED, 0x42)
a.emit(0xE1)
a.jr("SV_DN_WY", "c")
a.jr("SV_DN_WY", "z")
a.emit(0x21, (WORLD_H - 1) & 0xFF, (WORLD_H - 1) >> 8)
a.label("SV_DN_WY")
a.emit(0x22, WY & 0xFF, WY >> 8)
a.emit(0x3A, MAP_R23 & 0xFF, MAP_R23 >> 8)
a.emit(0xC6, (MAP_H - 1) & 0xFF)
a.emit(0x32, VL & 0xFF, VL >> 8)
a.call("FILL_LINE")
a.emit(0x21, SCRI & 0xFF, SCRI >> 8)
a.emit(0x35)
a.jr("SV_DN1", "nz")
a.jp("SV_RET")

a.label("SV_UP")
a.emit(0xED, 0x5B, CAMY & 0xFF, CAMY >> 8)
a.emit(0x2A, OCAMY & 0xFF, OCAMY >> 8)
a.emit(0xA7)
a.emit(0xED, 0x52)
a.emit(0x7D)
a.emit(0xFE, 3)
a.jr("SV_UP_ST", "c")
a.emit(0x3E, 2)
a.label("SV_UP_ST")
a.emit(0x32, SCRI & 0xFF, SCRI >> 8)
a.label("SV_UP1")
a.emit(0x21, MAP_R23 & 0xFF, MAP_R23 >> 8)
a.emit(0x35)
a.emit(0x2A, OCAMY & 0xFF, OCAMY >> 8)
a.emit(0x7C)
a.emit(0xB5)
a.jr("SV_UP_Z", "z")
a.emit(0x2B)
a.label("SV_UP_Z")
a.emit(0x22, OCAMY & 0xFF, OCAMY >> 8)
a.emit(0x22, WY & 0xFF, WY >> 8)
a.emit(0x3A, MAP_R23 & 0xFF, MAP_R23 >> 8)
a.emit(0x32, VL & 0xFF, VL >> 8)
a.call("FILL_LINE")
a.emit(0x21, SCRI & 0xFF, SCRI >> 8)
a.emit(0x35)
a.jr("SV_UP1", "nz")
a.label("SV_RET")
a.emit(0xC9)

# -------------------- INPUT: camera only --------------------
a.label("INPUT")
a.emit(0xAF)
a.call_abs(GTSTCK)
a.emit(0xB7)
a.jr("HAVE_DIR", "nz")
a.emit(0x3E, 1)
a.call_abs(GTSTCK)
a.label("HAVE_DIR")
a.emit(0xB7)
a.jp("INEND", "z")
a.emit(0x47)

# UP
a.emit(0x78)
a.emit(0xFE, 1)
a.jr("DO_UP", "z")
a.emit(0xFE, 2)
a.jr("DO_UP", "z")
a.emit(0xFE, 8)
a.jr("SKIP_UP", "nz")
a.label("DO_UP")
a.emit(0x2A, CAMY & 0xFF, CAMY >> 8)
a.emit(0x7C)
a.emit(0xB5)
a.jr("SKIP_UP", "z")
a.emit(0x11, SPD, 0)
a.emit(0xA7)
a.emit(0xED, 0x52)
a.jr("UP_OK", "nc")
a.emit(0x21, 0, 0)
a.label("UP_OK")
a.emit(0x22, CAMY & 0xFF, CAMY >> 8)
a.label("SKIP_UP")

# DOWN
a.emit(0x78)
a.emit(0xFE, 4)
a.jr("DO_DN", "z")
a.emit(0xFE, 5)
a.jr("DO_DN", "z")
a.emit(0xFE, 6)
a.jr("SKIP_DN", "nz")
a.label("DO_DN")
# Clamp with DE — must NOT use BC: B holds GTSTCK dir for later LEFT/RIGHT.
# Prior bug: LD BC,MAX_CAMY (1580=062Ch) set B=06h → LEFT saw dir 6 → down-left.
a.emit(0x2A, CAMY & 0xFF, CAMY >> 8)
a.emit(0x11, SPD, 0)
a.emit(0x19)
a.emit(0xE5)
a.emit(0x11, MAX_CAMY & 0xFF, MAX_CAMY >> 8)
a.emit(0xA7)
a.emit(0xED, 0x52)                        # SBC HL,DE (preserve B)
a.emit(0xE1)
a.jr("DN_OK", "c")
a.jr("DN_OK", "z")
a.emit(0x21, MAX_CAMY & 0xFF, MAX_CAMY >> 8)
a.label("DN_OK")
a.emit(0x22, CAMY & 0xFF, CAMY >> 8)
a.label("SKIP_DN")

# LEFT
a.emit(0x78)
a.emit(0xFE, 6)
a.jr("DO_LT", "z")
a.emit(0xFE, 7)
a.jr("DO_LT", "z")
a.emit(0xFE, 8)
a.jr("SKIP_LT", "nz")
a.label("DO_LT")
a.emit(0x2A, CAMX & 0xFF, CAMX >> 8)
a.emit(0x7C)
a.emit(0xB5)
a.jr("SKIP_LT", "z")
a.emit(0x11, SPD, 0)
a.emit(0xA7)
a.emit(0xED, 0x52)
a.jr("LT_OK", "nc")
a.emit(0x21, 0, 0)
a.label("LT_OK")
a.emit(0x7D)
a.emit(0xE6, 0xFE)                        # even camx
a.emit(0x6F)
a.emit(0x22, CAMX & 0xFF, CAMX >> 8)
a.label("SKIP_LT")

# RIGHT
a.emit(0x78)
a.emit(0xFE, 2)
a.jr("DO_RT", "z")
a.emit(0xFE, 3)
a.jr("DO_RT", "z")
a.emit(0xFE, 4)
a.jr("SKIP_RT", "nz")
a.label("DO_RT")
# Same as DO_DN: clamp via DE so B (stick dir) stays intact if more handlers follow.
a.emit(0x2A, CAMX & 0xFF, CAMX >> 8)
a.emit(0x11, SPD, 0)
a.emit(0x19)
a.emit(0xE5)
a.emit(0x11, MAX_CAMX & 0xFF, MAX_CAMX >> 8)
a.emit(0xA7)
a.emit(0xED, 0x52)                        # SBC HL,DE
a.emit(0xE1)
a.jr("RT_OK", "c")
a.jr("RT_OK", "z")
a.emit(0x21, MAX_CAMX & 0xFF, MAX_CAMX >> 8)
a.label("RT_OK")
a.emit(0x7D)
a.emit(0xE6, 0xFE)
a.emit(0x6F)
a.emit(0x22, CAMX & 0xFF, CAMX >> 8)
a.label("SKIP_RT")

a.label("INEND")
a.emit(0xC9)

# ---- Background music (MSX-MUSIC + PSG fallback) ----
emit_music(a)

code = a.resolve()
assert len(code) <= BANK_SIZE, f"bank0 overflow: {len(code)}"
# INPUT must preserve B (GTSTCK dir) through DOWN/RIGHT clamps — no SBC HL,BC there.
_in0 = a.labels["INPUT"] - a.org
_in1 = a.labels["INEND"] - a.org + 1
_inp = code[_in0:_in1]
assert bytes((0xED, 0x42)) not in _inp, "INPUT uses SBC HL,BC — clobbers stick dir in B"
assert bytes((0xED, 0x52)) in _inp, "INPUT missing SBC HL,DE clamp"
bank0 = code + bytes(BANK_SIZE - len(code))

for i in range(len(code) - 2):
    if code[i] == 0xCD and code[i + 1] == 0x53 and code[i + 2] == 0x00:
        raise SystemExit("BIOS SETWRT still present")
    if code[i] == 0xCD and code[i + 1] == 0x4D and code[i + 2] == 0x00:
        raise SystemExit("BIOS WRTVRM still present")
    if code[i] == 0x32 and code[i + 1] == 0x9A and code[i + 2] == 0xFD:
        raise SystemExit("H.KEYI (FD9Ah) write still present")

assert a.labels["SETWR2"] >= 0x4000
assert "WR_R23" in a.labels
assert "WR_HSCROLL" in a.labels
assert "SET_SINGLE" in a.labels
assert "DIS_SPRITES" in a.labels
assert "SOFT_EXIT" in a.labels
assert "DRAW_TITLE" in a.labels
assert "START_MAP" in a.labels
_se = a.labels["SOFT_EXIT"] - a.org
assert code[_se] == 0xF3  # DI
assert code[_se + 1] == 0xCD  # CALL MUSIC_MUTE
assert code[_se + 4: _se + 7] == bytes([0xC3, 0x00, 0x00])  # JP 0000h
assert "MUSIC_INIT" in a.labels and "MUSIC_TICK" in a.labels and "MUSIC_MUTE" in a.labels
assert "DETECT_OPLL" in a.labels
assert "REFILL_H" in a.labels
assert "WR_R18_ZERO" in a.labels
assert "DRAW_PLAYER" not in a.labels
assert "RESTORE_PL" not in a.labels
assert "SET_DUAL" not in a.labels
for banned in ("SETUP_SPLIT", "WAIT_SPLIT", "APPLY_HUD", "INIT_HUD",
               "DRAW_HUD", "LOOKUP_NAME", "DRAW_NAME", "IRQ_HDL",
               "WR_R18", "MAP_R18", "HMMM_SHIFT", "HMMM_RIGHT",
               "SET_DUAL"):
    assert banned not in a.labels, f"banned label still present: {banned}"

if USE_HW_HSCROLL:
    assert "SCROLL_H" in a.labels
    assert "FILL_STRIP" in a.labels
else:
    assert "SCROLL_H" not in a.labels

r18_writes = 0
for i in range(len(code) - 1):
    if code[i] == 0x3E and code[i + 1] == 0x92:
        r18_writes += 1
assert r18_writes == 1, f"expected exactly 1 R#18 select (0x92), got {r18_writes}"

if USE_HW_HSCROLL and R27_NEGATE:
    assert any(code[i] == 0xED and code[i + 1] == 0x44 for i in range(len(code) - 1)), \
        "NEG (ED 44) missing — R27=(-camx)&7 not encoded"

for i in range(len(code) - 1):
    if code[i] == 0x3E and code[i + 1] == 0x93:
        raise SystemExit("R#19 write still present")

assert any(code[i] == 0x3E and code[i + 1] == 0x99 for i in range(len(code) - 1)), "R#25 missing"
assert any(code[i] == 0x3E and code[i + 1] == 0x9A for i in range(len(code) - 1)), "R#26 missing"
assert any(code[i] == 0x3E and code[i + 1] == 0x9B for i in range(len(code) - 1)), "R#27 missing"
sp2_set = False
for i in range(len(code) - 3):
    if code[i] == 0x3E and code[i + 1] == 0x03 and code[i + 2] == 0xD3 and code[i + 3] == 0x99:
        if i + 5 < len(code) and code[i + 4] == 0x3E and code[i + 5] == 0x99:
            sp2_set = True
assert not sp2_set, "SP2=1 (R#25=03) must not be present"
assert any(code[i] == 0x3E and code[i + 1] == 0x02 for i in range(len(code) - 1)), "R#25 MSK=1 (02) missing"
assert any(code[i] == 0x3E and code[i + 1] == 0x1F for i in range(len(code) - 1)), "R#2=1Fh missing"
assert any(code[i] == 0x3E and code[i + 1] == 5 for i in range(len(code) - 1)), "CHGMOD 5 missing"
# SPD disable present
assert any(code[i] == 0x3E and code[i + 1] == 0x0A and code[i + 2] == 0xD3 and code[i + 3] == 0x99
           for i in range(len(code) - 3)), "R#8 SPD disable missing"


# ---- Assert FILL_LINE does not clobber ROM ptr with LD HL,BOFF before OUT_N ----
# Old bug: after POP HL (E1), LD C,98 / LD A,128 / LD HL,BOFF / SUB (HL) → OUT_N from RAM
_clobber = bytes([0xE1, 0x0E, 0x98, 0x3E, PAGE_BPL, 0x21, BOFF & 0xFF, BOFF >> 8, 0x96])
assert _clobber not in code, "FILL_LINE still LD HL,BOFF after restoring ROM ptr (stripe bug)"
# Fixed: POP HL; LD C,98; LD A,(BOFF); LD B,A; LD A,128; SUB B
_fixed = bytes([0xE1, 0x0E, 0x98, 0x3A, BOFF & 0xFF, BOFF >> 8, 0x47, 0x3E, PAGE_BPL, 0x90])
assert _fixed in code, "FILL_LINE missing fixed n1=128-boff without HL clobber"

# ---- Soft-sim: circular FILL_LINE / INIT_BLIT / pure DOWN ----
def _r26_r27(cx):
    if R27_NEGATE:
        r27 = (-cx) & 7
        r26 = ((cx + 7) >> 3) & 0x1F
    else:
        r27 = cx & 7
        r26 = (cx >> 3) & 0x1F
    return r26, r27, ((r26 << 3) - r27) & 255

def _fill_line_circular(vram, vl, wy, cx):
    """Asm FILL_LINE: VRAM byte ((cx/2)+bi)&127 = world[wy][cx/2+bi]."""
    assert cx % 2 == 0
    row = world[wy * BPL:(wy + 1) * BPL]
    boff = (cx // 2) & 127
    src = cx // 2
    n1 = 128 - boff
    for i in range(n1):
        vram[vl][(boff + i) & 127] = row[src + i]
    for i in range(boff):
        vram[vl][i] = row[src + n1 + i]

def _viewport_bytes(vram, cx, cy, r23):
    _, _, origin = _r26_r27(cx)
    assert origin == (cx & 255), f"origin {origin} != camx&255 {cx & 255}"
    out = []
    for row_i in range(MAP_H):
        vl = (r23 + row_i) & 255
        line = bytearray(PAGE_BPL)
        for bi in range(PAGE_BPL):
            vx = (origin + 2 * bi) & 255
            line[bi] = vram[vl][(vx // 2) & 127]
        out.append(bytes(line))
    return out

def _expected_viewport(cx, cy):
    out = []
    for row_i in range(MAP_H):
        wy = min(cy + row_i, WORLD_H - 1)
        row = world[wy * BPL:(wy + 1) * BPL]
        out.append(bytes(row[cx // 2: cx // 2 + PAGE_BPL]))
    return out

def _stripe_score(view_rows):
    w = len(view_rows[0])
    h = len(view_rows)
    striped = 0
    for x in range(w):
        col = [view_rows[y][x] for y in range(h)]
        if len(set(col)) <= 2 and h > 20:
            striped += 1
    return striped / w

if USE_HW_HSCROLL:
    _, _, origin = _r26_r27(camx0)
    assert origin == (camx0 & 255)

    vram = [bytearray(PAGE_BPL) for _ in range(256)]
    wy = camy0
    for vl in range(INIT_LINES):
        _fill_line_circular(vram, vl, min(wy, WORLD_H - 1), camx0)
        wy = min(wy + 1, WORLD_H - 1)

    for bi in range(PAGE_BPL):
        expect = world[camy0 * BPL + (camx0 // 2) + bi]
        got = vram[0][((camx0 // 2) + bi) & 127]
        assert got == expect, f"init circular fail bi={bi}"

    init_view = _viewport_bytes(vram, camx0, camy0, 0)
    exp_view = _expected_viewport(camx0, camy0)
    assert init_view == exp_view, "soft-sim init viewport != world at spawn"
    assert _stripe_score(init_view) < 0.25, f"init striped score={_stripe_score(init_view)}"

    camx, camy, r23, ocamy = camx0, camy0, 0, camy0
    down_frames = 0
    for _frame in range(20):
        new_y = min(camy + SPD, MAX_CAMY)
        dy = new_y - camy
        if dy == 0:
            break
        steps = min(dy, 2)
        for _ in range(steps):
            r23 = (r23 + 1) & 255
            ocamy = min(ocamy + 1, MAX_CAMY)
            wy = min(ocamy + (MAP_H - 1), WORLD_H - 1)
            vl = (r23 + (MAP_H - 1)) & 255
            _fill_line_circular(vram, vl, wy, camx)
        camy = new_y
        down_frames += 1
        view = _viewport_bytes(vram, camx, camy, r23)
        assert view == _expected_viewport(camx, camy), f"DOWN mismatch camy={camy}"
        assert _stripe_score(view) < 0.25, f"DOWN striped camy={camy}"

    # Linear blit (old wrong FILL_LINE intent) must NOT match when origin != 0
    vram_lin = [bytearray(PAGE_BPL) for _ in range(256)]
    wy = camy0
    for vl in range(INIT_LINES):
        row = world[min(wy, WORLD_H - 1) * BPL:(min(wy, WORLD_H - 1) + 1) * BPL]
        src = camx0 // 2
        for i in range(PAGE_BPL):
            vram_lin[vl][i] = row[src + i]
        wy = min(wy + 1, WORLD_H - 1)
    lin_view = _viewport_bytes(vram_lin, camx0, camy0, 0)
    assert lin_view != exp_view, "linear blit unexpectedly matches viewport"
    print(
        f"soft-sim OK: init + {down_frames} DOWN; "
        f"stripe_score init={_stripe_score(init_view):.3f} "
        f"linear_would={_stripe_score(lin_view):.3f}"
    )

rom = bytearray(bank0)
pad_row = bytes(BPL_STOR - BPL)
for bi in range(MAP_BANKS):
    y0 = bi * LINES_PER_BANK
    chunk = bytearray()
    for ly in range(LINES_PER_BANK):
        y = y0 + ly
        if y < WORLD_H:
            row = world[y * BPL:(y + 1) * BPL]
            assert len(row) == BPL
            chunk.extend(row)
            chunk.extend(pad_row)
        else:
            chunk.extend(b"\xFF" * BPL_STOR)
    assert len(chunk) == BANK_SIZE
    rom.extend(chunk)

target = TOTAL_BANKS * BANK_SIZE
assert len(rom) == (1 + MAP_BANKS) * BANK_SIZE
rom.extend(b"\xFF" * (target - len(rom)))
assert len(rom) == target
rom_kb = len(rom) // 1024

out = ROOT / "dist" / "medemblik.rom"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(rom)

bbox = meta.get("bbox", [])
bbox_m = meta.get("bbox_m_approx", [])
h_desc = (
    "H scroll: R#26/R#27 Yamaha polarity (R27=(-camx)&7, R26=((camx+7)>>3)&1Fh) "
    "+ edge FILL_STRIP; circular VRAM; R#18 unused for scroll; "
    f"USE_HW_HSCROLL={USE_HW_HSCROLL} R27_NEGATE={R27_NEGATE}; REFILL_H fallback in source"
    if USE_HW_HSCROLL else
    "H scroll: REFILL_H — on camx change, copy 256x128B from ROM; R#26=R#27=0; "
    f"USE_HW_HSCROLL={USE_HW_HSCROLL}"
)
frame_desc = (
    "Frame: VBlank -> snapshot -> INPUT(cam) -> WR_R23 + WR_HSCROLL -> SCROLL_V -> "
    + ("SCROLL_H" if USE_HW_HSCROLL else "REFILL_H")
    + " -> MUSIC_TICK"
)
mapper = ROOT / "dist" / "MAPPER.txt"
mapper.write_text(
    f"ASCII16 Screen5 street-walker — MSX2+ / V9958, single page (SP2=0)\n"
    f"Banks: {TOTAL_BANKS} x {BANK_SIZE} = {len(rom)} bytes ({rom_kb}KB, pad 0xFF)\n"
    f"Switch: 6000h = page 4000h, 7000h = page 8000h\n"
    f"Bank 0 = code; banks 1..{MAP_BANKS} = map "
    f"({LINES_PER_BANK} lines x {BPL_STOR} BPL_STOR, logical BPL={BPL}); "
    f"banks {MAP_BANKS+1}..{TOTAL_BANKS-1} = empty 0xFF\n"
    f"Requires: MSX2+ / turboR (V9958). Screen 5 viewport {VIEW_W}x{MAP_H}; "
    f"world {WORLD_W}x{WORLD_H}\n"
    f"VRAM: 256x256 circular buffer (one Scr5 page, 128 B/line); SETWR2 (R#14)\n"
    f"R#25: MSK=1 SP2=0 (0x02); R#2=1Fh (page0 only); NO dual-page\n"
    f"{h_desc}\n"
    f"FILL_LINE: OUTI+NOP+JR NZ (not OTIR)"
    + (" — circular write" if USE_HW_HSCROLL else " — linear page write (REFILL_H)")
    + "\n"
    f"camx forced even; source X byte-aligned (camx/2)\n"
    f"V scroll: R#23 ring; 128B edge FILL_LINE from ROM; max 2 steps/frame; "
    f"camy 0..{MAX_CAMY}; camx 0..{MAX_CAMX} (even)\n"
    f"Init: R#18=0; R#8 SPD=1 (sprites disabled); blit {INIT_LINES} lines; map_r23=0\n"
    f"{frame_desc}\n"
    f"Input: GTSTCK stick0 | stick1(cursors); SPD={SPD}; camera only — NO player sprite; "
    f"DOWN/RIGHT clamp via DE (B=dir preserved)\n"
    f"Title: Scr0 text 'Medemblik' + 'SPACE / DRUK Spatie'; SPACE (SNSMAT row8 bit0) starts map\n"
    f"Escape: SNSMAT row7 bit2 on title OR map → MUSIC_MUTE then soft exit JP 0000h\n"
    f"Music: Erik Satie Gymnopédie No.1 (PD) ~79 BPM 3/4; melody A3–A4 (1 oct lower); "
    f"pad A2/B2 bass G2/D3; 24 bars/72 beats (~55s); OPLL atten Piano4/Organ6/Bass5; "
    f"PSG vol 11/8/9; Moonblaster F-num + key-off/on; duration events; "
    f"detect RDSLT APRLOPLL@4018h then OPLL@401Ch (FM-PAC 7FF6h|=1 if external); "
    f"MUS_FLAGS bit0=OPLL bit1=play bit2=ext; else PAL PSG; tick div={MUS_TEMPO}; title+map\n"
    f"No screensplit, no FH/R#19/H.KEYI/IE1, no HUD overlay, no SP2, no yellow player\n"
    f"Street names: baked into world bitmap by rasterize_street_world.py (5x7 + white halo)\n"
    f"BBox lat/lon: {bbox}\n"
    f"BBox approx m (N-S, E-W): {bbox_m}\n"
    f"Water polys: {meta.get('n_water_poly')}; canal segments: {meta.get('n_canal_seg')}; "
    f"haven segs: {meta.get('n_haven_seg')}\n"
    f"Spawn: {sx0},{sy0}; init cam: {camx0},{camy0}\n"
    f"Code size: {len(code)} bytes\n"
    f"USE_HW_HSCROLL: {USE_HW_HSCROLL}\n"
    f"R27_NEGATE: {R27_NEGATE}\n"
    + (
        "NOTE: HW H-scroll ON — Yamaha R27=(-camx)&7, R26=((camx+7)>>3)&1Fh; "
        "origin=((R26<<3)-R27)&255 == camx&255; edge FILL_STRIP only; "
        "R#18=0 once (not used for scroll); REFILL_H remains if USE_HW_HSCROLL=False. "
        "FILL_LINE circular: VRAM[(camx+i)&255]=world[camx+i]; n1=128-boff via LD A,(BOFF)/SUB B "
        "(NOT LD HL,BOFF which clobbered ROM ptr after POP HL → init/UP/DOWN stripes). "
        "INPUT: DOWN/RIGHT clamps use SBC HL,DE (B=dir preserved). "
        "SCROLL_H no-ops when camx unchanged; SCROLL_V FILL_LINE uses current camx. "
        "Music: MUSIC_MUTE on Escape; FM if OPLL (MUS_FLAGS bit0) else PSG; bit2=external FM-PAC.\n"
        if USE_HW_HSCROLL else
        "NOTE: HW H-scroll DISABLED — REFILL_H + R#26=R#27=0.\n"
    )
)

print(f"ROM: {out}")
print(f"size: {len(rom)} ({rom_kb}KB)")
print(f"map_banks: {MAP_BANKS} lines_per_bank: {LINES_PER_BANK} bpl_stor: {BPL_STOR}")
print(f"total_banks: {TOTAL_BANKS}")
print(f"world: {WORLD_W}x{WORLD_H} bpl={BPL}")
print(f"code_bytes: {len(code)}")
print(f"SETWR2: @{a.labels['SETWR2']:04X}")
print(f"SET_SINGLE: @{a.labels['SET_SINGLE']:04X}")
print(f"DIS_SPRITES: @{a.labels['DIS_SPRITES']:04X}")
print(f"WR_R23: @{a.labels['WR_R23']:04X}")
print(f"WR_HSCROLL: @{a.labels['WR_HSCROLL']:04X}")
print(f"FILL_LINE: @{a.labels['FILL_LINE']:04X}")
print(f"USE_HW_HSCROLL: {USE_HW_HSCROLL}")
print(f"R27_NEGATE: {R27_NEGATE}")
if USE_HW_HSCROLL:
    print(f"SCROLL_H: @{a.labels['SCROLL_H']:04X}")
    print(f"FILL_STRIP: @{a.labels['FILL_STRIP']:04X}")
    print("H formula: R27=(-camx)&7; R26=((camx+7)>>3)&1Fh" if R27_NEGATE
          else "H formula: R27=camx&7; R26=(camx>>3)&1Fh (legacy)")
print(f"REFILL_H: @{a.labels['REFILL_H']:04X} (fallback kept)")
print(f"SP2: ABSENT")
print(f"player: REMOVED (camera-only)")
print(f"title: Scr0 Medemblik; SPACE starts; ESC→mute+JP0000")
print(f"music: Gymnopedie No.1 (PD); Moonblaster F-num; tempo_div={MUS_TEMPO} events={SONG_LEN}")
print(f"MUSIC_INIT: @{a.labels['MUSIC_INIT']:04X} TICK:@{a.labels['MUSIC_TICK']:04X} "
      f"MUTE:@{a.labels['MUSIC_MUTE']:04X}")
print("FM opening F#4-A4-G4 (ch0, after key-off R20=00):")
for name, fn, lo, r20 in expected_opening_fm_writes():
    print(f"  {name}: fnum={fn:03X}h  OUT 7C,10 / 7D,{lo:02X}  then  7C,20 / 7D,{r20:02X}")
print(f"SOFT_EXIT: @{a.labels['SOFT_EXIT']:04X}")
print(f"DRAW_TITLE: @{a.labels['DRAW_TITLE']:04X}")
print(f"init_cam: {camx0},{camy0}")
print(f"spawn: {sx0},{sy0}")
print(f"labels: {len(a.labels)}")
print(f"MAPPER: {mapper}")
