ASCII16 Screen5 street-walker — MSX2+ / V9958, single page (SP2=0)
Banks: 128 x 16384 = 2097152 bytes (2048KB, pad 0xFF)
Switch: 6000h = page 4000h, 7000h = page 8000h
Bank 0 = code; banks 1..112 = map (16 lines x 1024 BPL_STOR, logical BPL=792); banks 113..127 = empty 0xFF
Requires: MSX2+ / turboR (V9958). Screen 5 viewport 256x212; world 1584x1792
VRAM: 256x256 circular buffer (one Scr5 page, 128 B/line); SETWR2 (R#14)
R#25: MSK=1 SP2=0 (0x02); R#2=1Fh (page0 only); NO dual-page
H scroll: R#26/R#27 Yamaha polarity (R27=(-camx)&7, R26=((camx+7)>>3)&1Fh) + edge FILL_STRIP; circular VRAM; R#18 unused for scroll; USE_HW_HSCROLL=True R27_NEGATE=True; REFILL_H fallback in source
FILL_LINE: OUTI+NOP+JR NZ (not OTIR) — circular write
camx forced even; source X byte-aligned (camx/2)
V scroll: R#23 ring; 128B edge FILL_LINE from ROM; max 2 steps/frame; camy 0..1580; camx 0..1328 (even)
Init: R#18=0; R#8 SPD=1 (sprites disabled); blit 256 lines; map_r23=0
Frame: VBlank -> snapshot -> INPUT(cam) -> WR_R23 + WR_HSCROLL -> SCROLL_V -> SCROLL_H -> MUSIC_TICK
Input: GTSTCK stick0 | stick1(cursors); SPD=2; camera only — NO player sprite; DOWN/RIGHT clamp via DE (B=dir preserved)
Title: Scr0 text 'Medemblik' + 'SPACE / DRUK Spatie'; SPACE (SNSMAT row8 bit0) starts map
Escape: SNSMAT row7 bit2 on title OR map → MUSIC_MUTE then soft exit JP 0000h
Music: Erik Satie Gymnopédie No.1 (PD) ~79 BPM 3/4; melody A3–A4 (1 oct lower); pad A2/B2 bass G2/D3; 24 bars/72 beats (~55s); OPLL atten Piano4/Organ6/Bass5; PSG vol 11/8/9; Moonblaster F-num + key-off/on; duration events; detect RDSLT APRLOPLL@4018h then OPLL@401Ch (FM-PAC 7FF6h|=1 if external); MUS_FLAGS bit0=OPLL bit1=play bit2=ext; else PAL PSG; tick div=38; title+map
No screensplit, no FH/R#19/H.KEYI/IE1, no HUD overlay, no SP2, no yellow player
Street names: baked into world bitmap by rasterize_street_world.py (5x7 + white halo)
BBox lat/lon: [52.764, 52.778, 5.098, 5.118]
BBox approx m (N-S, E-W): [1558.5, 1347.0]
Water polys: 18; canal segments: 253; haven segs: 3
Spawn: 1046,872; init cam: 918,766
Code size: 2414 bytes
USE_HW_HSCROLL: True
R27_NEGATE: True
NOTE: HW H-scroll ON — Yamaha R27=(-camx)&7, R26=((camx+7)>>3)&1Fh; origin=((R26<<3)-R27)&255 == camx&255; edge FILL_STRIP only; R#18=0 once (not used for scroll); REFILL_H remains if USE_HW_HSCROLL=False. FILL_LINE circular: VRAM[(camx+i)&255]=world[camx+i]; n1=128-boff via LD A,(BOFF)/SUB B (NOT LD HL,BOFF which clobbered ROM ptr after POP HL → init/UP/DOWN stripes). INPUT: DOWN/RIGHT clamps use SBC HL,DE (B=dir preserved). SCROLL_H no-ops when camx unchanged; SCROLL_V FILL_LINE uses current camx. Music: MUSIC_MUTE on Escape; FM if OPLL (MUS_FLAGS bit0) else PSG; bit2=external FM-PAC.
