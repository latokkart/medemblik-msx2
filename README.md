# Medemblik MSX2

Top-down **Screen 5** street map of **Medemblik** (Netherlands) for **MSX2+ / turboR** (V9958). Walk the historic harbor center with hardware H-scroll, a title screen, Escape-to-exit, and Erik Satie's *Gymnopédie No.1* as BGM (public domain).

Street layout is derived from [OpenStreetMap](https://www.openstreetmap.org/copyright) data (highways, water, buildings) around Radboud / Oude Haven, rasterized to a large Scr5 world bitmap packed into an ASCII16 mapper ROM (~2 MB).

## Play

- ROM: `dist/medemblik.rom` (2 MB ASCII16 cartridge; assembled by CI from `dist/zlib_b64/` — see below)
- Requires: MSX2+ / turboR with V9958 (hardware H-scroll via R#26/R#27)
- Controls: cursor keys / joystick — pan the camera over the streets
- Title: Scr0 text "Medemblik"; press **SPACE** to start
- **Escape**: mute music and soft-reset (`JP 0000h`) from title or map
- Music: *Gymnopédie No.1* (~79 BPM 3/4) on OPLL (FM-PAC / built-in) when present, else PSG

### openMSX

```bash
openmsx -machine Panasonic_FS-A1GT -cart dist/medemblik.rom
# or another MSX2+ / turboR machine with V9958
```

## Rebuild

World bitmap (large binary, not committed — rebuild from OSM):

```bash
python3 tools/rasterize_street_world.py   # writes data/street_world.bin + meta
python3 tools/build_street_rom.py         # writes dist/medemblik.rom + MAPPER.txt
```

After rebuilding the ROM locally, regenerate uploadable chunks (see `PUSH_MANIFEST` / docs). Large `street_world.bin` / raw OSM extracts are **not** pushed — regenerate with the rasterizer.

## GitHub Actions — assemble ROM from zlib_b64

Binary ROMs are stored as zlib-compressed, base64-encoded text chunks under `dist/zlib_b64/` so they fit text-friendly pushes.

Workflow `.github/workflows/assemble-rom.yml` concatenates chunks, decodes, verifies `dist/SHA256.txt`, and uploads the ROM artifact.

## Mapper / tech notes

See `dist/MAPPER.txt` and `dist/PHASE_NOTES.md`.

- ASCII16: 128 × 16 KB banks; bank 0 = code; banks 1..112 = map
- Scr5 viewport 256×212; world ~1584×1792; circular VRAM buffer
- HW H-scroll: Yamaha R27 = `(-camx)&7`, R26 = `((camx+7)>>3)&1Fh` + edge fill
- Street names baked into the world bitmap (5×7 + halo)

## License

MIT for code and generated ROM. Map data © OpenStreetMap contributors (ODbL). *Gymnopédie No.1* melody arrangement is public domain (Erik Satie).
