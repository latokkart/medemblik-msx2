"""Minimal correct YM2413 (MSX-MUSIC) + PSG fallback player.

Erik Satie – Gymnopédie No. 1 (public domain, 1888). Melodic loop with
bass+pad accompaniment. Tempo ~79 BPM @50 Hz (MUS_TEMPO=38 frames/beat).
Melody ~1 octave below the prior A4–A5 arrangement (now A3–A4); pad dropped
to A2/B2 so it sits under the melody without mud. Louder OPLL/PSG levels.
24-bar / 72-beat loop (~55 s) covering opening theme + restatement + bridge.

F-num: Moonblaster / MSX-MUSIC standard (A..G# per octave):
  AD B7 C2 CD D9 E6 F4 103 112 122 134 146
  Reg 0x1x = Fnum lo; reg 0x2x = KeyOn(10h)|Sus(20h)|(block<<1)|Fnum9.

Prior bug: homemade table [0x159,0x16D,…] (wrong scale) + no key-off before
re-pitch → vague beeps / unrecognisable pitches; PSG also retriggered every
step with no hold discipline.

Detect: RDSLT APRLOPLL@4018h then OPLL@401Ch; external FM-PAC WRSLT 7FF6h|=1.
MUS_FLAGS: bit0=OPLL present, bit1=playing, bit2=external (7FF6 enabled).
"""

MUS_FLAGS = 0xE030
MUS_SLOT = 0xE031
MUS_DIV = 0xE032
MUS_POS = 0xE033
MUS_WAIT = 0xE034
MUS_LAST0 = 0xE035
MUS_LAST1 = 0xE036
MUS_LAST2 = 0xE037

EXPTBL = 0xFCC1
RDSLT, WRSLT = 0x000C, 0x0014

MUS_TEMPO = 38

# Moonblaster F-num: A A# B C C# D D# E F F# G G#
_FNUM = [0x0AD, 0x0B7, 0x0C2, 0x0CD, 0x0D9, 0x0E6,
         0x0F4, 0x103, 0x112, 0x122, 0x134, 0x146]
_A, _As, _B, _C, _Cs, _D, _Ds, _E, _F, _Fs, _G, _Gs = range(12)

_NOTE_DEF = [
    (_G, 2),   # 1 G2  bass
    (_D, 3),   # 2 D3  bass
    (_A, 2),   # 3 A2  pad (was A3)
    (_B, 2),   # 4 B2  pad (was B3)
    (_A, 3),   # 5 A3  mel (was A4)
    (_B, 3),   # 6 B3  mel (was B4)
    (_Cs, 4),  # 7 C#4
    (_D, 4),   # 8 D4
    (_E, 4),   # 9 E4
    (_Fs, 4),  # 10 F#4
    (_G, 4),   # 11 G4
    (_A, 4),   # 12 A4
]

NOTE_LO, NOTE_HI = [], []
for st, blk in _NOTE_DEF:
    fn = _FNUM[st]
    NOTE_LO.append(fn & 0xFF)
    NOTE_HI.append(((blk & 7) << 1) | ((fn >> 8) & 1))

# MIDI: G2 D3 A2 B2 A3 B3 C#4 D4 E4 F#4 G4 A4
_MIDI = [43, 50, 45, 47, 57, 59, 61, 62, 64, 66, 67, 69]


def _psg_per(midi):
    """PAL MSX: AY clock ~1.7734 MHz → period = 1773400/(16*freq)."""
    freq = 440.0 * (2 ** ((midi - 69) / 12.0))
    return max(1, min(4095, int(round(1773400.0 / (16.0 * freq)))))


PSG_LO = [_psg_per(m) & 0xFF for m in _MIDI]
PSG_HI = [(_psg_per(m) >> 8) & 0x0F for m in _MIDI]

G2, D3, A2, B2 = 1, 2, 3, 4
A3, B3, Cs4, D4, E4, Fs4, G4, A4 = 5, 6, 7, 8, 9, 10, 11, 12

# Duration events (beats, mel, pad, bass) — 24 bars × 3/4 = 72 beats
# Bars 1–8 theme; 9–12 restatement→A; 13–16 settle; 17–20 bridge; 21–24 descent→A
_EVENTS = [
    # 1–4 theme open
    (1, Fs4, B2, G2), (1, A4, B2, G2), (1, G4, B2, G2),
    (2, Fs4, A2, D3), (1, E4, A2, D3),
    (1, D4, B2, G2), (1, E4, B2, G2), (1, Fs4, B2, G2),
    (3, D4, A2, D3),
    # 5–8 theme close
    (1, Cs4, B2, G2), (1, D4, B2, G2), (1, E4, B2, G2),
    (2, Cs4, A2, D3), (1, B3, A2, D3),
    (1, A3, B2, G2), (1, B3, B2, G2), (1, Cs4, B2, G2),
    (3, A3, A2, D3),
    # 9–12 restatement ending high A
    (1, Fs4, B2, G2), (1, A4, B2, G2), (1, G4, B2, G2),
    (2, Fs4, A2, D3), (1, E4, A2, D3),
    (1, D4, B2, G2), (1, E4, B2, G2), (1, Fs4, B2, G2),
    (3, A4, A2, D3),
    # 13–16 settle on A (as bars 5–8)
    (1, Cs4, B2, G2), (1, D4, B2, G2), (1, E4, B2, G2),
    (2, Cs4, A2, D3), (1, B3, A2, D3),
    (1, A3, B2, G2), (1, B3, B2, G2), (1, Cs4, B2, G2),
    (3, A3, A2, D3),
    # 17–20 rising bridge (PD-style contour)
    (1, E4, B2, G2), (1, Fs4, B2, G2), (1, G4, B2, G2),
    (2, A4, A2, D3), (1, G4, A2, D3),
    (1, Fs4, B2, G2), (1, E4, B2, G2), (1, D4, B2, G2),
    (3, Cs4, A2, D3),
    # 21–24 descent home → A (loops cleanly into F#)
    (1, B3, B2, G2), (1, Cs4, B2, G2), (1, D4, B2, G2),
    (2, E4, A2, D3), (1, D4, A2, D3),
    (1, Cs4, B2, G2), (1, B3, B2, G2), (1, A3, B2, G2),
    (3, A3, A2, D3),
]
SONG_LEN = len(_EVENTS)
assert sum(e[0] for e in _EVENTS) == 72


def expected_opening_fm_writes(sustain=True):
    """Opening F#4-A4-G4 expected ch0 register writes (after key-off)."""
    sus = 0x20 if sustain else 0
    rows = []
    for name, idx, blk in (("F#4", _Fs, 4), ("A4", _A, 4), ("G4", _G, 4)):
        fn = _FNUM[idx]
        lo = fn & 0xFF
        r20 = 0x10 | sus | ((blk & 7) << 1) | ((fn >> 8) & 1)
        rows.append((name, fn, lo, r20))
    return rows


def emit_music(a):
    """Emit music routines + data into Asm builder `a`."""

    # ---------- FM_WR / PSG_WR ----------
    a.label("FM_WR")
    a.emit(0xD3, 0x7C)
    a.emit(0x00, 0x00, 0x00, 0x00)
    a.emit(0x7B)
    a.emit(0xD3, 0x7D)
    a.emit(0xC5)                # preserve BC (DJNZ uses B; callers keep note/ch)
    a.emit(0x06, 6)
    a.label("FM_WR_DL")
    a.djnz("FM_WR_DL")
    a.emit(0xC1)
    a.emit(0xC9)

    a.label("PSG_WR")
    a.emit(0xD3, 0xA0)
    a.emit(0x7B)
    a.emit(0xD3, 0xA1)
    a.emit(0xC9)

    # ---------- MUSIC_MUTE ----------
    a.label("MUSIC_MUTE")
    for reg, val in ((8, 0), (9, 0), (10, 0), (7, 0x3F)):
        a.emit(0x3E, reg)
        a.emit(0x1E, val)
        a.call("PSG_WR")
    a.emit(0x3A, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xE6, 0x01)
    a.jr("MM_DONE", "z")
    a.emit(0x0E, 0)
    a.label("MM_FLP")
    a.emit(0x79)
    a.emit(0xC6, 0x20)
    a.emit(0x1E, 0)
    a.call("FM_WR")
    a.emit(0x79)
    a.emit(0xC6, 0x30)
    a.emit(0x1E, 0x0F)
    a.call("FM_WR")
    a.emit(0x0C)
    a.emit(0x79)
    a.emit(0xFE, 9)
    a.jr("MM_FLP", "c")
    a.label("MM_DONE")
    a.emit(0x3A, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xE6, 0xFD)
    a.emit(0x32, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xAF)
    a.emit(0x32, MUS_LAST0 & 0xFF, MUS_LAST0 >> 8)
    a.emit(0x32, MUS_LAST1 & 0xFF, MUS_LAST1 >> 8)
    a.emit(0x32, MUS_LAST2 & 0xFF, MUS_LAST2 >> 8)
    a.emit(0xC9)

    # ---------- Detect helpers ----------
    a.label("CMP_STR")
    a.label("CMP_LP")
    a.emit(0xC5, 0xD5, 0xE5)
    a.emit(0x79)
    a.call_abs(RDSLT)
    a.emit(0xE1, 0xD1)
    a.emit(0x4F)
    a.emit(0x1A)
    a.emit(0xB9)
    a.emit(0xC1)
    a.jr("CMP_FAIL", "nz")
    a.emit(0x23, 0x13)
    a.djnz("CMP_LP")
    a.emit(0xAF)
    a.emit(0xC9)
    a.label("CMP_FAIL")
    a.emit(0xF6, 0xFF)
    a.emit(0xC9)

    a.label("STR_APRLOPLL")
    a.emit(*list(b"APRLOPLL"))
    a.label("STR_OPLL")
    a.emit(*list(b"OPLL"))

    a.label("CHECK_SLOT_APR")
    a.emit(0x21, 0x18, 0x40)
    a.emit(0x11)
    a.word("STR_APRLOPLL")
    a.emit(0x06, 8)
    a.jp("CMP_STR")

    a.label("CHECK_SLOT_OPLL")
    a.emit(0x21, 0x1C, 0x40)
    a.emit(0x11)
    a.word("STR_OPLL")
    a.emit(0x06, 4)
    a.jp("CMP_STR")

    a.label("TRY_SLOT_APR")
    a.call("CHECK_SLOT_APR")
    a.jr("TRY_MISS", "nz")
    a.emit(0x79, 0xFE, 0xFF)
    a.emit(0xC9)
    a.label("TRY_SLOT_OPLL")
    a.call("CHECK_SLOT_OPLL")
    a.jr("TRY_MISS", "nz")
    a.emit(0x79, 0xFE, 0xFF)
    a.emit(0xC9)
    a.label("TRY_MISS")
    a.emit(0xAF)
    a.emit(0xC9)

    def emit_scan(name, try_lab):
        a.label(name)
        a.emit(0x06, 0)
        a.label(name + "_P")
        a.emit(0x21, EXPTBL & 0xFF, EXPTBL >> 8)
        a.emit(0x78, 0x85, 0x6F)
        a.emit(0x3E, 0)
        a.emit(0x8C, 0x67)
        a.emit(0x7E)
        a.emit(0xE6, 0x80)
        a.jr(name + "_F", "z")
        a.emit(0x0E, 0)
        a.label(name + "_S")
        a.emit(0x79, 0x87, 0x87, 0xB0)
        a.emit(0xF6, 0x80)
        a.emit(0xC5)
        a.emit(0x4F)
        a.call(try_lab)
        a.emit(0xC1)
        a.emit(0xC0)
        a.emit(0x0C)
        a.emit(0x79, 0xFE, 4)
        a.jr(name + "_S", "c")
        a.jr(name + "_N")
        a.label(name + "_F")
        a.emit(0xC5)
        a.emit(0x48)
        a.call(try_lab)
        a.emit(0xC1)
        a.emit(0xC0)
        a.label(name + "_N")
        a.emit(0x04)
        a.emit(0x78, 0xFE, 4)
        a.jr(name + "_P", "c")
        a.emit(0xAF)
        a.emit(0xC9)

    emit_scan("SCAN_APR", "TRY_SLOT_APR")
    emit_scan("SCAN_OPLL", "TRY_SLOT_OPLL")

    a.label("DETECT_OPLL")
    a.emit(0xAF)
    a.emit(0x32, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0x32, MUS_SLOT & 0xFF, MUS_SLOT >> 8)
    a.call("SCAN_APR")
    a.jr("DET_INTERNAL", "nz")
    a.call("SCAN_OPLL")
    a.jr("DET_NONE", "z")
    a.emit(0x32, MUS_SLOT & 0xFF, MUS_SLOT >> 8)
    a.emit(0x47)
    a.emit(0x21, 0xF6, 0x7F)
    a.emit(0x78)
    a.call_abs(RDSLT)
    a.emit(0xF6, 0x01)
    a.emit(0x5F)
    a.emit(0x21, 0xF6, 0x7F)
    a.emit(0x78)
    a.call_abs(WRSLT)
    a.emit(0x3E, 0x05)
    a.emit(0x32, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.call("FM_SILENT_INIT")
    a.emit(0xC9)
    a.label("DET_INTERNAL")
    a.emit(0x32, MUS_SLOT & 0xFF, MUS_SLOT >> 8)
    a.emit(0x3E, 0x01)
    a.emit(0x32, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.call("FM_SILENT_INIT")
    a.emit(0xC9)
    a.label("DET_NONE")
    a.emit(0xAF)
    a.emit(0x32, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xC9)

    a.label("FM_SILENT_INIT")
    a.emit(0x3E, 0x0E)
    a.emit(0x1E, 0)
    a.call("FM_WR")
    a.emit(0x0E, 0)
    a.label("FSI_LP")
    a.emit(0x79)
    a.emit(0xC6, 0x20)
    a.emit(0x1E, 0)
    a.call("FM_WR")
    a.emit(0x0C)
    a.emit(0x79, 0xFE, 9)
    a.jr("FSI_LP", "c")
    a.emit(0xC9)

    # ---------- FM_NOTE ----------
    # In: A=note (0=rest), C=channel. Out: clobbers AF,BC,DE,HL
    # If note == MUS_LAST[ch]: return. Else store, key-off, (rest?ret), fnum, key-on|sus.
    a.label("FM_NOTE")
    a.emit(0x47)                # B = note
    a.emit(0x21, MUS_LAST0 & 0xFF, MUS_LAST0 >> 8)
    a.emit(0x79)                # A = ch
    a.emit(0x85, 0x6F)
    a.emit(0x3E, 0)
    a.emit(0x8C, 0x67)          # HL = &MUS_LAST[ch]
    a.emit(0x78)                # A = note
    a.emit(0xBE)
    a.emit(0xC8)                # RET Z — same pitch, keep sounding
    a.emit(0x77)                # store new last
    # key off
    a.emit(0x79)
    a.emit(0xC6, 0x20)
    a.emit(0x1E, 0)
    a.call("FM_WR")
    a.emit(0x78)                # A = note
    a.emit(0xB7)
    a.emit(0xC8)                # RET Z — rest, already keyed off
    a.emit(0x3D)                # A = index 0..
    a.emit(0xC5)                # save BC (B=note, C=ch)
    a.emit(0x6F, 0x26, 0)
    a.emit(0xE5)
    a.emit(0x11)
    a.word("TBL_NOTE_LO")
    a.emit(0x19)
    a.emit(0x5E)                # E = fnum lo  (use E directly)
    a.emit(0xE1)
    a.emit(0x11)
    a.word("TBL_NOTE_HI")
    a.emit(0x19)
    a.emit(0x56)                # D = (block<<1)|f9
    a.emit(0xC1)                # C = ch
    a.emit(0x79)
    a.emit(0xC6, 0x10)          # A = reg 0x10+ch  (E already fnum lo)
    a.call("FM_WR")
    a.emit(0x7A)                # A = D
    a.emit(0xF6, 0x30)          # KeyOn|Sustain
    a.emit(0x5F)                # E = key byte
    a.emit(0x79)
    a.emit(0xC6, 0x20)
    a.call("FM_WR")
    a.emit(0xC9)

    # ---------- PSG_NOTE ----------
    a.label("PSG_NOTE")
    a.emit(0x47)                # B = note
    a.emit(0x21, MUS_LAST0 & 0xFF, MUS_LAST0 >> 8)
    a.emit(0x79)
    a.emit(0x85, 0x6F)
    a.emit(0x3E, 0)
    a.emit(0x8C, 0x67)
    a.emit(0x78)
    a.emit(0xBE)
    a.emit(0xC8)
    a.emit(0x77)
    a.emit(0x78)
    a.emit(0xB7)
    a.jr("PSG_OFF", "z")
    a.emit(0x3D)
    a.emit(0xC5)
    a.emit(0x6F, 0x26, 0)
    a.emit(0xE5)
    a.emit(0x11)
    a.word("TBL_PSG_LO")
    a.emit(0x19)
    a.emit(0x5E)                # E = period lo
    a.emit(0xE1)
    a.emit(0x11)
    a.word("TBL_PSG_HI")
    a.emit(0x19)
    a.emit(0x56)                # D = period hi
    a.emit(0xC1)                # C = ch
    a.emit(0x79)
    a.emit(0x87)                # A = ch*2 = tone reg
    a.emit(0xC5)
    a.call("PSG_WR")            # E already lo
    a.emit(0xC1)
    a.emit(0x79)
    a.emit(0x87)
    a.emit(0x3C)                # hi reg
    a.emit(0x5A)                # E = D
    a.call("PSG_WR")
    # volume: ch0 mel=11, ch1 pad=8, ch2 bass=9 (louder, still <15)
    a.emit(0x79)
    a.emit(0xC6, 8)
    a.emit(0x47)                # B = vol reg
    a.emit(0xFE, 8)
    a.jr("PV1", "nz")
    a.emit(0x1E, 11)
    a.jr("PVW")
    a.label("PV1")
    a.emit(0xFE, 9)
    a.jr("PV2", "nz")
    a.emit(0x1E, 8)
    a.jr("PVW")
    a.label("PV2")
    a.emit(0x1E, 9)
    a.label("PVW")
    a.emit(0x78)
    a.call("PSG_WR")
    a.emit(0xC9)
    a.label("PSG_OFF")
    a.emit(0x79)
    a.emit(0xC6, 8)
    a.emit(0x1E, 0)
    a.call("PSG_WR")
    a.emit(0xC9)

    # ---------- Apply event notes (HL → song ptr at mel) ----------
    # SONG_APPLY_FM / SONG_APPLY_PSG: MUS_POS event → set 3 voices
    a.label("FM_STEP")
    a.call("SONG_LOAD")         # HL → dur byte of event; sets MUS_WAIT from dur
    a.emit(0x23)                # HL → mel
    a.emit(0x7E)
    a.emit(0x23)
    a.emit(0xE5)
    a.emit(0x0E, 0)
    a.call("FM_NOTE")
    a.emit(0xE1)
    a.emit(0x7E)
    a.emit(0x23)
    a.emit(0xE5)
    a.emit(0x0E, 1)
    a.call("FM_NOTE")
    a.emit(0xE1)
    a.emit(0x7E)
    a.emit(0x0E, 2)
    a.call("FM_NOTE")
    a.jp("SONG_ADV")

    a.label("PSG_STEP")
    a.call("SONG_LOAD")
    a.emit(0x23)
    a.emit(0x7E)
    a.emit(0x23)
    a.emit(0xE5)
    a.emit(0x0E, 0)
    a.call("PSG_NOTE")
    a.emit(0xE1)
    a.emit(0x7E)
    a.emit(0x23)
    a.emit(0xE5)
    a.emit(0x0E, 1)
    a.call("PSG_NOTE")
    a.emit(0xE1)
    a.emit(0x7E)
    a.emit(0x0E, 2)
    a.call("PSG_NOTE")
    a.jp("SONG_ADV")

    # SONG_LOAD: HL = &event[MUS_POS]; MUS_WAIT = dur-1 (this beat consumes 1)
    a.label("SONG_LOAD")
    a.emit(0x3A, MUS_POS & 0xFF, MUS_POS >> 8)
    a.emit(0x6F, 0x26, 0)
    a.emit(0x29)                # *2
    a.emit(0x29)                # *4
    a.emit(0x11)
    a.word("SONG_DATA")
    a.emit(0x19)                # HL → event
    a.emit(0x7E)                # A = dur
    a.emit(0x3D)                # dur-1 remaining after this beat
    a.emit(0x32, MUS_WAIT & 0xFF, MUS_WAIT >> 8)
    a.emit(0xC9)

    a.label("SONG_ADV")
    a.emit(0x3A, MUS_POS & 0xFF, MUS_POS >> 8)
    a.emit(0x3C)
    a.emit(0xFE, SONG_LEN)
    a.jr("SA_STORE", "c")
    a.emit(0xAF)
    a.label("SA_STORE")
    a.emit(0x32, MUS_POS & 0xFF, MUS_POS >> 8)
    a.emit(0xC9)

    # ---------- MUSIC_TICK ----------
    a.label("MUSIC_TICK")
    a.emit(0x3A, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xE6, 0x02)
    a.emit(0xC8)
    a.emit(0x21, MUS_DIV & 0xFF, MUS_DIV >> 8)
    a.emit(0x35)
    a.emit(0xC0)
    a.emit(0x36, MUS_TEMPO)
    # beat: if MUS_WAIT>0, just dec; else fetch event
    a.emit(0x21, MUS_WAIT & 0xFF, MUS_WAIT >> 8)
    a.emit(0x7E)
    a.emit(0xB7)
    a.jr("MT_FETCH", "z")
    a.emit(0x35)                # dec wait
    a.emit(0xC9)
    a.label("MT_FETCH")
    a.emit(0x3A, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xE6, 0x01)
    a.jp("FM_STEP", "nz")
    a.jp("PSG_STEP")

    # ---------- MUSIC_INIT ----------
    a.label("MUSIC_INIT")
    a.emit(0xF3)
    a.emit(0xAF)
    a.emit(0x32, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0x32, MUS_SLOT & 0xFF, MUS_SLOT >> 8)
    a.emit(0x32, MUS_POS & 0xFF, MUS_POS >> 8)
    a.emit(0x32, MUS_WAIT & 0xFF, MUS_WAIT >> 8)
    a.emit(0x32, MUS_LAST0 & 0xFF, MUS_LAST0 >> 8)
    a.emit(0x32, MUS_LAST1 & 0xFF, MUS_LAST1 >> 8)
    a.emit(0x32, MUS_LAST2 & 0xFF, MUS_LAST2 >> 8)
    a.call("DETECT_OPLL")
    a.call("MUSIC_MUTE")
    # MUTE clears playing + lasts; restore OPLL detect bits
    # DETECT already set flags; MUTE cleared bit1 only (E6 FD). bit0/2 kept. Good.
    a.emit(0x3E, MUS_TEMPO)
    a.emit(0x32, MUS_DIV & 0xFF, MUS_DIV >> 8)
    a.emit(0x3A, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xE6, 0x01)
    a.jr("MI_PSG", "z")
    # Soft instruments: Piano mel, Organ pad, Acoustic Bass — louder (atten closer to 0)
    a.emit(0x3E, 0x30)
    a.emit(0x1E, (3 << 4) | 4)    # Piano atten 4 (was 10)
    a.call("FM_WR")
    a.emit(0x3E, 0x31)
    a.emit(0x1E, (8 << 4) | 6)    # Organ atten 6 (was 12)
    a.call("FM_WR")
    a.emit(0x3E, 0x32)
    a.emit(0x1E, (14 << 4) | 5)   # Acoustic Bass atten 5 (was 11)
    a.call("FM_WR")
    a.emit(0x3E, 0x0E)
    a.emit(0x1E, 0)
    a.call("FM_WR")
    a.jr("MI_GO")
    a.label("MI_PSG")
    a.emit(0x3E, 7)
    a.emit(0x1E, 0x38)            # tone A/B/C on, noise off
    a.call("PSG_WR")
    a.label("MI_GO")
    a.emit(0xAF)
    a.emit(0x32, MUS_WAIT & 0xFF, MUS_WAIT >> 8)  # fetch on first beat
    a.emit(0x32, MUS_POS & 0xFF, MUS_POS >> 8)
    a.emit(0x3A, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xF6, 0x02)
    a.emit(0x32, MUS_FLAGS & 0xFF, MUS_FLAGS >> 8)
    a.emit(0xC9)

    # ---------- Tables + song ----------
    a.label("TBL_NOTE_LO")
    a.emit(*NOTE_LO)
    a.label("TBL_NOTE_HI")
    a.emit(*NOTE_HI)
    a.label("TBL_PSG_LO")
    a.emit(*PSG_LO)
    a.label("TBL_PSG_HI")
    a.emit(*PSG_HI)
    a.label("SONG_DATA")
    for dur, mel, pad, bass in _EVENTS:
        a.emit(dur, mel, pad, bass)
