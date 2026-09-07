#!/usr/bin/env python3
"""Rasterize street-scale Medemblik town+harbour map → Screen 5 world (4bpp).

Scale-locked to the working harbour zoom (~1.27e-5 °/px lon, ~7.81e-6 °/px lat).
Expanded bbox covers Medemblik town + harbours (~1550×1360 m → ~1584×1792 px).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path("/workspace/medemblik-msx2")

# Town + harbours (not ultra-wide zoomed-out)
MIN_LAT, MAX_LAT = 52.7640, 52.7780
MIN_LON, MAX_LON = 5.0980, 5.1180

# Scale lock vs previous 512×512 over 0.004°×0.0065°
DEG_PER_PX_LAT = 0.004 / 512  # 7.8125e-6
DEG_PER_PX_LON = 0.0065 / 512  # 1.26953125e-5


def _rup16(x: float) -> int:
    return int(math.ceil(x / 16.0) * 16)


WORLD_W = _rup16((MAX_LON - MIN_LON) / DEG_PER_PX_LON)  # 1584
WORLD_H = _rup16((MAX_LAT - MIN_LAT) / DEG_PER_PX_LAT)  # 1792
BPL = WORLD_W // 2  # 792
VIEW_W, VIEW_H = 256, 212

C_WATER, C_GRASS, C_ROAD = 1, 2, 4
C_BLD, C_LINE = 7, 8

# Same street-zoom road widths that felt right at this meters/pixel
ROAD_W = {
    "primary": 16,
    "secondary": 14,
    "tertiary": 12,
    "unclassified": 10,
    "residential": 10,
    "pedestrian": 8,
    "living_street": 10,
    "service": 6,
    "busway": 8,
    "cycleway": 4,
    "footway": 3,
    "path": 3,
    "steps": 3,
    "track": 4,
    "platform": 4,
    "construction": 4,
}

HAVEN_NAMES = {
    "Oosterhaven",
    "Pekelharinghaven",
    "Westerhaven",
    "Oude Haven",
    "Regatta Center Medemblik",
}

# Skip waterways/polygons whose geo span is huge (beams from IJsselmeer etc.)
MAX_STROKE_SPAN_DEG = 0.025
MAX_POLY_SPAN_DEG = 0.035

# Medemblik waterfront (land west / water east), approx Oosterdijk → harbour mouth →
# north dike. Used to synthesize IJsselmeer open water inside the bbox.
# Coordinates are (lat, lon).
IJSSELMEER_COAST = [
    (52.76400, 5.11760),
    (52.76500, 5.11740),
    (52.76600, 5.11700),
    (52.76700, 5.11670),
    (52.76800, 5.11640),
    (52.76900, 5.11610),
    (52.77000, 5.11580),
    (52.77100, 5.11550),
    (52.77180, 5.11500),
    (52.77260, 5.11420),
    (52.77340, 5.11360),
    (52.77420, 5.11330),
    (52.77480, 5.11280),
    (52.77540, 5.11050),
    (52.77600, 5.10750),
    (52.77680, 5.10500),
    (52.77760, 5.10420),
    (52.77800, 5.10380),
]

# Closed geo rings (lat, lon) for harbour basins missing as OSM water polygons.
# Built from quay/street + canal centerline so basins are solid blue, not strokes.
SYNTHETIC_HAVEN_BASINS: dict[str, list[tuple[float, float]]] = {
    # Pekelharinghaven: south quay (street) + north/east canal arm
    "Pekelharinghaven": [
        (52.769391, 5.109323),
        (52.769450, 5.109583),
        (52.769542, 5.109822),
        (52.769737, 5.110287),
        (52.770123, 5.111211),
        (52.770880, 5.113043),
        (52.770940, 5.113190),
        (52.771220, 5.113120),
        (52.771411, 5.113011),
        (52.771789, 5.112913),
        (52.772740, 5.111761),  # join Oosterhaven canal
        (52.771928, 5.112500),
        (52.771587, 5.112422),
        (52.770021, 5.109112),
        (52.769391, 5.109323),
    ],
    # Oosterhaven: west quay (street) + east canal toward IJsselmeer mouth
    "Oosterhaven": [
        (52.770537, 5.107726),
        (52.770624, 5.107979),
        (52.771028, 5.108159),
        (52.771108, 5.108289),
        (52.771739, 5.109260),
        (52.772244, 5.110054),
        (52.772755, 5.110887),
        (52.773354, 5.111833),
        (52.773569, 5.112238),
        (52.773646, 5.112338),
        (52.774550, 5.113611),  # mouth into IJsselmeer
        (52.774303, 5.113259),
        (52.773734, 5.112885),
        (52.773345, 5.112466),
        (52.772740, 5.111761),
        (52.772001, 5.110471),
        (52.771912, 5.110334),
        (52.770385, 5.108183),
        (52.770339, 5.108126),
        (52.770537, 5.107726),
    ],
    # Oude Haven: strip between south quay street and north quay (~25–40 m basin)
    "Oude Haven": [
        (52.773602, 5.106322),
        (52.773629, 5.107394),
        (52.773713, 5.109337),
        (52.773771, 5.110296),
        (52.773843, 5.110907),
        (52.773952, 5.111600),
        (52.774240, 5.111391),
        (52.774213, 5.111475),
        (52.774117, 5.110457),
        (52.774035, 5.110277),
        (52.773998, 5.110267),
        (52.77395, 5.109337),
        (52.77388, 5.107394),
        (52.77385, 5.106322),
        (52.773602, 5.106322),
    ],
}


def to_xy(lat: float, lon: float) -> tuple[int, int]:
    x = int((lon - MIN_LON) / (MAX_LON - MIN_LON) * (WORLD_W - 1))
    y = int((MAX_LAT - lat) / (MAX_LAT - MIN_LAT) * (WORLD_H - 1))
    return x, y


def clamp_xy(x: int, y: int) -> tuple[int, int]:
    return max(0, min(WORLD_W - 1, x)), max(0, min(WORLD_H - 1, y))


def way_points(el: dict, clamp: bool = True) -> list[tuple[int, int]]:
    out = []
    for p in el.get("geometry") or []:
        x, y = to_xy(p["lat"], p["lon"])
        out.append(clamp_xy(x, y) if clamp else (x, y))
    return out


def geom_in_bbox(el: dict, pad_lat: float = 0.001, pad_lon: float = 0.001) -> bool:
    for p in el.get("geometry") or []:
        if (MIN_LAT - pad_lat) <= p["lat"] <= (MAX_LAT + pad_lat) and (
            MIN_LON - pad_lon
        ) <= p["lon"] <= (MAX_LON + pad_lon):
            return True
    return False


def geo_span(el: dict) -> tuple[float, float]:
    g = el.get("geometry") or []
    if not g:
        return 0.0, 0.0
    lats = [p["lat"] for p in g]
    lons = [p["lon"] for p in g]
    return max(lats) - min(lats), max(lons) - min(lons)


def barely_clips(el: dict) -> bool:
    """True if geometry is huge and only a tiny fraction lies in the bbox."""
    g = el.get("geometry") or []
    if len(g) < 2:
        return False
    dlat, dlon = geo_span(el)
    if max(dlat, dlon) < MAX_STROKE_SPAN_DEG:
        return False
    inside = 0
    for p in g:
        if MIN_LAT <= p["lat"] <= MAX_LAT and MIN_LON <= p["lon"] <= MAX_LON:
            inside += 1
    return inside < max(2, len(g) // 8)


def is_water_poly(tags: dict) -> bool:
    if tags.get("natural") == "water":
        return True
    if tags.get("leisure") == "marina":
        return True
    if "water" in tags:
        return True
    if tags.get("landuse") == "basin":
        return True
    if tags.get("harbour") == "yes":
        return True
    if tags.get("waterway") in ("riverbank", "dock"):
        return True
    return False


def is_water_stroke(tags: dict) -> bool:
    return tags.get("waterway") in ("canal", "river", "fairway", "stream", "ditch")


def geo_ring_to_xy(ring: list[tuple[float, float]]) -> list[tuple[int, int]]:
    """Map a (lat,lon) ring to pixel coords; do NOT clamp (avoids bbox edge beams)."""
    return [to_xy(lat, lon) for lat, lon in ring]


def paint_water_poly(pix: bytearray, pts: list[tuple[int, int]]) -> None:
    if len(pts) < 3:
        return
    fill_poly(pix, pts, C_WATER)
    for i in range(len(pts)):
        draw_line(pix, *pts[i], *pts[(i + 1) % len(pts)], C_WATER, width=2)


def fill_ijsselmeer(pix: bytearray) -> int:
    """Solid blue for IJsselmeer open water east/north of the Medemblik waterfront."""
    coast_xy = geo_ring_to_xy(IJSSELMEER_COAST)
    # Close via NE and SE bbox corners (open water to the east + wrapped north).
    ne = to_xy(MAX_LAT, MAX_LON)
    se = to_xy(MIN_LAT, MAX_LON)
    # Also cover north strip west to the northern coast tip via NW along max lat.
    # Polygon: coast S→N, then NE corner, SE corner, back to coast start.
    ring = coast_xy + [ne, se]
    paint_water_poly(pix, ring)
    return 1


def fill_synthetic_havens(pix: bytearray) -> dict[str, int]:
    """Fill named harbour basins that lack OSM water polygons (street+canal rings)."""
    counts: dict[str, int] = {}
    for name, ring in SYNTHETIC_HAVEN_BASINS.items():
        pts = geo_ring_to_xy(ring)
        paint_water_poly(pix, pts)
        counts[name] = 1
    return counts


def setp(pix: bytearray, x: int, y: int, c: int) -> None:
    if 0 <= x < WORLD_W and 0 <= y < WORLD_H:
        pix[y * WORLD_W + x] = c


def draw_line(
    pix: bytearray, x0: int, y0: int, x1: int, y1: int, color: int, width: int = 1
) -> None:
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    r = max(0, (width - 1) // 2)
    while True:
        for oy in range(-r, r + 1):
            for ox in range(-r, r + 1):
                if ox * ox + oy * oy <= r * r + r:
                    setp(pix, x + ox, y + oy, color)
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy


def fill_poly(pix: bytearray, points: list[tuple[int, int]], color: int) -> None:
    if len(points) < 3:
        return
    ys = [p[1] for p in points]
    y0, y1 = max(0, min(ys)), min(WORLD_H - 1, max(ys))
    n = len(points)
    for y in range(y0, y1 + 1):
        xs: list[float] = []
        for i in range(n):
            x0, yy0 = points[i]
            x1, yy1 = points[(i + 1) % n]
            if yy0 == yy1:
                continue
            if yy0 > yy1:
                x0, yy0, x1, yy1 = x1, yy1, x0, yy0
            if yy0 <= y < yy1:
                t = (y - yy0) / (yy1 - yy0)
                xs.append(x0 + t * (x1 - x0))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            xa, xb = int(xs[i]), int(xs[i + 1])
            if xa > xb:
                xa, xb = xb, xa
            for x in range(max(0, xa), min(WORLD_W, xb + 1)):
                setp(pix, x, y, color)


def draw_road_seg(
    pix: bytearray, x0: int, y0: int, x1: int, y1: int, width: int
) -> None:
    """Paint road; preserve harbour basins (thin bridge center over water only)."""
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    r = max(0, (width - 1) // 2)
    while True:
        for oy in range(-r, r + 1):
            for ox in range(-r, r + 1):
                if ox * ox + oy * oy > r * r + r:
                    continue
                px, py = x + ox, y + oy
                if not (0 <= px < WORLD_W and 0 <= py < WORLD_H):
                    continue
                cur = pix[py * WORLD_W + px]
                if cur == C_WATER:
                    if ox == 0 and oy == 0 and width >= 6:
                        pix[py * WORLD_W + px] = C_ROAD
                    continue
                pix[py * WORLD_W + px] = C_ROAD
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy


def pack_4bpp(pix: bytearray) -> bytes:
    out = bytearray(WORLD_H * BPL)
    for y in range(WORLD_H):
        row = y * WORLD_W
        dst = y * BPL
        for x in range(0, WORLD_W, 2):
            out[dst + x // 2] = ((pix[row + x] & 0xF) << 4) | (pix[row + x + 1] & 0xF)
    return bytes(out)



# --- 5x7 caps font for Scr5 road labels (column-major bits, LSB top) ---
# Larger than old 3x5 for CRT readability; axis-aligned only.
_FONT5x7 = {
    "A": [0x7E, 0x09, 0x09, 0x09, 0x7E],
    "B": [0x7F, 0x49, 0x49, 0x49, 0x36],
    "C": [0x3E, 0x41, 0x41, 0x41, 0x41],
    "D": [0x7F, 0x41, 0x41, 0x41, 0x3E],
    "E": [0x7F, 0x49, 0x49, 0x49, 0x41],
    "F": [0x7F, 0x09, 0x09, 0x09, 0x01],
    "G": [0x3E, 0x41, 0x49, 0x49, 0x79],
    "H": [0x7F, 0x08, 0x08, 0x08, 0x7F],
    "I": [0x00, 0x41, 0x7F, 0x41, 0x00],
    "J": [0x20, 0x40, 0x41, 0x5F, 0x21],
    "K": [0x7F, 0x08, 0x14, 0x22, 0x41],
    "L": [0x7F, 0x40, 0x40, 0x40, 0x40],
    "M": [0x7F, 0x02, 0x04, 0x02, 0x7F],
    "N": [0x7F, 0x02, 0x04, 0x08, 0x7F],
    "O": [0x3E, 0x41, 0x41, 0x41, 0x3E],
    "P": [0x7F, 0x09, 0x09, 0x09, 0x06],
    "Q": [0x3E, 0x41, 0x51, 0x21, 0x5E],
    "R": [0x7F, 0x09, 0x19, 0x29, 0x46],
    "S": [0x46, 0x49, 0x49, 0x49, 0x31],
    "T": [0x01, 0x01, 0x7F, 0x01, 0x01],
    "U": [0x3F, 0x40, 0x40, 0x40, 0x3F],
    "V": [0x1F, 0x20, 0x40, 0x20, 0x1F],
    "W": [0x7F, 0x20, 0x18, 0x20, 0x7F],
    "X": [0x63, 0x14, 0x08, 0x14, 0x63],
    "Y": [0x03, 0x04, 0x78, 0x04, 0x03],
    "Z": [0x61, 0x51, 0x49, 0x45, 0x43],
    "0": [0x3E, 0x51, 0x49, 0x45, 0x3E],
    "1": [0x00, 0x42, 0x7F, 0x40, 0x00],
    "2": [0x42, 0x61, 0x51, 0x49, 0x46],
    "3": [0x22, 0x49, 0x49, 0x49, 0x36],
    "4": [0x18, 0x14, 0x12, 0x7F, 0x10],
    "5": [0x27, 0x45, 0x45, 0x45, 0x39],
    "6": [0x3E, 0x49, 0x49, 0x49, 0x30],
    "7": [0x61, 0x11, 0x09, 0x05, 0x03],
    "8": [0x36, 0x49, 0x49, 0x49, 0x36],
    "9": [0x06, 0x49, 0x49, 0x49, 0x3E],
    "-": [0x08, 0x08, 0x08, 0x08, 0x08],
    "'": [0x00, 0x00, 0x03, 0x00, 0x00],
    ".": [0x00, 0x00, 0x60, 0x00, 0x00],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00],
}

_FONT_W, _FONT_H = 5, 7
_FONT_ADV = 6  # 5px glyph + 1px gap

C_LABEL = 0  # black on gray road (high contrast)
C_LABEL_HALO = 11  # white 1px halo for Scr5 CRT readability

# Prefer major ways; skip tiny alleys when dense
_LABEL_HW_PRIO = {
    "primary": 0, "secondary": 1, "tertiary": 2, "unclassified": 3,
    "residential": 4, "pedestrian": 5, "living_street": 5, "busway": 6,
    "service": 7,
}
_LABEL_MIN_WIDTH = {
    "primary": 8, "secondary": 8, "tertiary": 6, "unclassified": 6,
    "residential": 6, "pedestrian": 6, "living_street": 6, "busway": 6,
    "service": 6,
}
_MAX_LABEL_CHARS = 12
_LABEL_CELL = 28  # occupancy grid (was 48) — denser labels
_LABEL_MAX = 110
_LABEL_MIN_PLEN = 24  # was 36


def short_street_name(name: str) -> str:
    n = (name or "").strip()
    # drop common suffixes to fit
    for suf in ("straat", "laan", "weg", "singel", "dijk", "kade", "steeg", "pad"):
        if len(n) > _MAX_LABEL_CHARS and n.lower().endswith(suf) and len(n) > len(suf) + 2:
            n = n[: -len(suf)]
            break
    n = n.replace(" ", "")
    if len(n) > _MAX_LABEL_CHARS:
        n = n[:_MAX_LABEL_CHARS]
    return n


def _paint_on_road(pix: bytearray, px: int, py: int, color: int, *, allow_halo: bool = False) -> None:
    """Paint only over road (and optionally prior halo) so labels stay on streets."""
    if 0 <= px < WORLD_W and 0 <= py < WORLD_H:
        cur = pix[py * WORLD_W + px]
        if cur == C_ROAD or (allow_halo and cur == C_LABEL_HALO):
            pix[py * WORLD_W + px] = color


def draw_char(pix: bytearray, x: int, y: int, ch: str, color: int, halo: int | None = C_LABEL_HALO) -> None:
    cols = _FONT5x7.get(ch.upper())
    if not cols:
        cols = _FONT5x7.get(" ")
    # 1px white halo on neighbors only, then black ink (may overwrite halo on glyph).
    if halo is not None:
        for cx, bits in enumerate(cols):
            for cy in range(_FONT_H):
                if bits & (1 << cy):
                    for oy in (-1, 0, 1):
                        for ox in (-1, 0, 1):
                            if ox == 0 and oy == 0:
                                continue
                            _paint_on_road(pix, x + cx + ox, y + cy + oy, halo)
    for cx, bits in enumerate(cols):
        for cy in range(_FONT_H):
            if bits & (1 << cy):
                _paint_on_road(pix, x + cx, y + cy, color, allow_halo=True)


def draw_label_text(pix: bytearray, x: int, y: int, text: str, color: int = C_LABEL) -> int:
    """Axis-aligned 5x7 label with halo; returns pixel width used."""
    tx = x
    for ch in text:
        draw_char(pix, tx, y, ch, color)
        tx += _FONT_ADV
    return tx - x


def path_length(pts: list[tuple[int, int]]) -> float:
    s = 0.0
    for i in range(len(pts) - 1):
        dx = pts[i + 1][0] - pts[i][0]
        dy = pts[i + 1][1] - pts[i][1]
        s += (dx * dx + dy * dy) ** 0.5
    return s


def path_midpoint(pts: list[tuple[int, int]]) -> tuple[int, int, float, float]:
    """Return (x, y, dx, dy) near geometric midpoint along polyline."""
    total = path_length(pts)
    if total < 1 or len(pts) < 2:
        x, y = pts[len(pts) // 2]
        return x, y, 1.0, 0.0
    half = total * 0.45
    acc = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        dx, dy = x1 - x0, y1 - y0
        seg = (dx * dx + dy * dy) ** 0.5
        if seg < 1e-6:
            continue
        if acc + seg >= half:
            t = (half - acc) / seg
            return int(x0 + t * dx), int(y0 + t * dy), dx / seg, dy / seg
        acc += seg
    x0, y0 = pts[-2]
    x1, y1 = pts[-1]
    dx, dy = x1 - x0, y1 - y0
    seg = max(1e-6, (dx * dx + dy * dy) ** 0.5)
    return x1, y1, dx / seg, dy / seg


def bake_street_labels(pix: bytearray, raw: dict) -> dict:
    """Rasterize short OSM street names into road pixels (not HUD)."""
    # Gather best geometry per name
    best: dict[str, dict] = {}
    for el in raw["elements"]:
        tags = el.get("tags") or {}
        hw = tags.get("highway")
        if not hw or hw not in _LABEL_HW_PRIO:
            continue
        # Skip tiny alleys/paths; allow long named service if substantial
        if hw in ("footway", "path", "steps", "track", "cycleway", "platform", "construction"):
            continue
        if hw == "service":
            pass  # length filter below
        name = tags.get("name") or tags.get("name:nl")
        if not name:
            continue
        if not geom_in_bbox(el):
            continue
        pts = way_points(el)
        if len(pts) < 2:
            continue
        plen = path_length(pts)
        min_w = _LABEL_MIN_WIDTH.get(hw, 10)
        if hw == "service" and plen < 60:
            continue
        if ROAD_W.get(hw, 0) < min_w and plen < 80:
            continue
        short = short_street_name(name)
        if len(short) < 3:
            continue
        prev = best.get(short)
        score = (_LABEL_HW_PRIO[hw], -plen)
        if prev is None or score < prev["score"]:
            best[short] = {"score": score, "pts": pts, "hw": hw, "full": name, "plen": plen}

    # Major first
    ordered = sorted(best.items(), key=lambda kv: kv[1]["score"])
    occ = set()
    n_drawn = 0
    n_skip_dense = 0
    drawn_names: list[str] = []
    for short, info in ordered:
        pts = info["pts"]
        if info["plen"] < _LABEL_MIN_PLEN:
            continue
        mx, my, dx, dy = path_midpoint(pts)
        # Always axis-aligned (crisper than rotation at Scr5 scale).
        tw = len(short) * _FONT_ADV
        lx = mx - tw // 2
        ly = my - (_FONT_H // 2)
        # occupancy — same cell only (more labels; font is larger so visual space still OK)
        cx0, cy0 = lx // _LABEL_CELL, ly // _LABEL_CELL
        if (cx0, cy0) in occ:
            n_skip_dense += 1
            continue
        # Try small Y nudges so 5x7 fits on ~10px residential roads
        placed = False
        for dy in (0, -2, 2, -3, 3, -1, 1):
            ly2 = ly + dy
            roadish = 0
            total = 0
            for yy in range(ly2, ly2 + _FONT_H):
                for xx in range(lx, lx + tw):
                    if 0 <= xx < WORLD_W and 0 <= yy < WORLD_H:
                        total += 1
                        if pix[yy * WORLD_W + xx] == C_ROAD:
                            roadish += 1
            if total >= 10 and roadish >= total * 0.30:
                draw_label_text(pix, lx, ly2, short, C_LABEL)
                occ.add((cx0, cy0))
                if tw >= _LABEL_CELL:
                    occ.add((cx0 + 1, cy0))
                    occ.add((cx0 - 1, cy0))
                n_drawn += 1
                drawn_names.append(short)
                placed = True
                break
        if not placed:
            continue
        if n_drawn >= _LABEL_MAX:
            break
    return {
        "n_labels": n_drawn,
        "n_skip_dense": n_skip_dense,
        "labels": drawn_names[:40],
        "font": f"{_FONT_W}x{_FONT_H}",
    }



def find_spawn(pix: bytearray) -> list[int]:
    """Prefer road near Oosterhaven / Pekelharinghaven (central harbours)."""
    # ~lon 5.1115, lat 52.7710 in new world
    cx = int((5.1115 - MIN_LON) / (MAX_LON - MIN_LON) * (WORLD_W - 1))
    cy = int((MAX_LAT - 52.7710) / (MAX_LAT - MIN_LAT) * (WORLD_H - 1))
    best = None
    best_d = 10**9
    for r in range(0, 220):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if abs(dx) != r and abs(dy) != r:
                    continue
                x, y = cx + dx, cy + dy
                if not (8 <= x < WORLD_W - 8 and 8 <= y < WORLD_H - 8):
                    continue
                if pix[y * WORLD_W + x] != C_ROAD:
                    continue
                d = dx * dx + dy * dy
                if d < best_d:
                    best_d = d
                    best = [x & ~1, y]
        if best is not None and r > 16:
            break
    if best is None:
        best = [cx & ~1, cy]
    return best


def main() -> None:
    raw_path = ROOT / "data" / "map_raw.json"
    raw = json.load(open(raw_path))
    pix = bytearray([C_GRASS] * (WORLD_W * WORLD_H))

    n_water_poly = 0
    n_canal_seg = 0
    n_haven_seg = 0
    n_bld = 0
    n_road_seg = 0
    n_skip_huge = 0
    n_ijsselmeer = 0
    synth_havens: dict[str, int] = {}

    # 0) IJsselmeer open water (absent as a local OSM water polygon)
    n_ijsselmeer = fill_ijsselmeer(pix)

    # 1) OSM water polygons — unclamped verts (clamping caused edge "beam" artifacts)
    for el in raw["elements"]:
        tags = el.get("tags") or {}
        if not is_water_poly(tags):
            continue
        if not geom_in_bbox(el):
            continue
        dlat, dlon = geo_span(el)
        if max(dlat, dlon) > MAX_POLY_SPAN_DEG and barely_clips(el):
            n_skip_huge += 1
            continue
        pts = way_points(el, clamp=False)
        if len(pts) < 3:
            continue
        paint_water_poly(pix, pts)
        n_water_poly += 1

    # 1b) Synthetic harbour basins (Oosterhaven / Pekelharinghaven / Oude Haven)
    #     OSM only has waterway=canal centerlines for these; fill solid basins.
    synth_havens = fill_synthetic_havens(pix)

    # 2) Canal / river strokes — thinner now that haven basins are real polygons
    for el in raw["elements"]:
        tags = el.get("tags") or {}
        if not is_water_stroke(tags):
            continue
        if not geom_in_bbox(el):
            continue
        if barely_clips(el):
            n_skip_huge += 1
            continue
        pts = way_points(el, clamp=False)
        if len(pts) < 2:
            continue
        name = tags.get("name") or tags.get("name:nl") or ""
        ww = tags.get("waterway")
        if name in HAVEN_NAMES or (ww == "canal" and "haven" in name.lower()):
            # Basin already filled as polygon; light stroke only to seal edges
            width = 8
            n_haven_seg += 1
        elif ww in ("canal", "fairway", "river"):
            width = 12
        else:
            width = 5
        for i in range(len(pts) - 1):
            draw_line(pix, *pts[i], *pts[i + 1], C_WATER, width=width)
            n_canal_seg += 1

    # 3) Buildings
    for el in raw["elements"]:
        tags = el.get("tags") or {}
        if "building" not in tags:
            continue
        if not geom_in_bbox(el):
            continue
        pts = way_points(el)
        if len(pts) < 3:
            continue
        fill_poly(pix, pts, C_BLD)
        for i in range(len(pts)):
            draw_line(pix, *pts[i], *pts[(i + 1) % len(pts)], C_LINE, width=1)
        n_bld += 1

    # 4) Roads (after water; preserve basins)
    for el in raw["elements"]:
        tags = el.get("tags") or {}
        hw = tags.get("highway")
        if not hw or hw not in ROAD_W:
            continue
        if not geom_in_bbox(el):
            continue
        pts = way_points(el)
        if len(pts) < 2:
            continue
        w = ROAD_W[hw]
        for i in range(len(pts) - 1):
            draw_road_seg(pix, *pts[i], *pts[i + 1], w)
            n_road_seg += 1

    # 5) Bake short street names into road pixels (Scr5 bitmap, not HUD)
    label_info = bake_street_labels(pix, raw)

    spawn = find_spawn(pix)
    packed = pack_4bpp(pix)
    assert len(packed) == WORLD_H * BPL

    (ROOT / "data" / "street_world.bin").write_bytes(packed)
    cos_lat = math.cos(math.radians((MIN_LAT + MAX_LAT) / 2))
    meta = {
        "spawn": spawn,
        "world": [WORLD_W, WORLD_H],
        "view": [VIEW_W, VIEW_H],
        "bpl": BPL,
        "bpl_stor": 1024,  # ROM row pad so 16K banks hold whole lines
        "bbox": [MIN_LAT, MAX_LAT, MIN_LON, MAX_LON],
        "bbox_m_approx": [
            round((MAX_LAT - MIN_LAT) * 111320, 1),
            round((MAX_LON - MIN_LON) * 111320 * cos_lat, 1),
        ],
        "deg_per_px": [DEG_PER_PX_LAT, DEG_PER_PX_LON],
        "n_water_poly": n_water_poly,
        "n_canal_seg": n_canal_seg,
        "n_haven_seg": n_haven_seg,
        "n_bld": n_bld,
        "n_road_seg": n_road_seg,
        "n_skip_huge": n_skip_huge,
        "n_ijsselmeer": n_ijsselmeer,
        "synth_havens": synth_havens,
        "n_labels": label_info.get("n_labels", 0),
        "n_label_skip_dense": label_info.get("n_skip_dense", 0),
        "label_font": label_info.get("font", "5x7"),
        "labels_sample": label_info.get("labels", [])[:20],
    }
    (ROOT / "data" / "street_world_meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    try:
        from PIL import Image

        pal = [
            0, 0, 0,
            20, 60, 180,
            40, 140, 40,
            30, 100, 30,
            140, 140, 140,
            180, 180, 180,
            200, 170, 40,
            160, 70, 40,
            120, 40, 40,
            255, 220, 40,
            0, 0, 40,
            255, 255, 255,
            180, 140, 80,
            0, 100, 0,
            80, 80, 80,
            255, 255, 255,
        ]
        img = Image.new("P", (WORLD_W, WORLD_H))
        img.putpalette(pal + [0] * (768 - len(pal)))
        img.putdata(list(pix))
        img.save(ROOT / "data" / "street_preview.png")
        img.save(ROOT / "dist" / "street_preview.png")
        # Spawn-centered viewport crop (same zoom feel)
        sx, sy = spawn
        x0 = max(0, min(WORLD_W - VIEW_W, sx - VIEW_W // 2))
        y0 = max(0, min(WORLD_H - VIEW_H, sy - VIEW_H // 2))
        view = img.crop((x0, y0, x0 + VIEW_W, y0 + VIEW_H))
        view.save(ROOT / "dist" / "street_view_spawn.png")
        print("preview_png: data/street_preview.png dist/street_preview.png")
        print(f"spawn_view: dist/street_view_spawn.png @ cam {x0},{y0}")
    except Exception as e:
        print(f"preview_png: skipped ({e})")

    water_px = sum(1 for b in pix if b == C_WATER)
    road_px = sum(1 for b in pix if b == C_ROAD)
    print(f"world: {WORLD_W}x{WORLD_H} bpl={BPL} bytes={len(packed)}")
    print(f"bbox: {MIN_LAT},{MAX_LAT},{MIN_LON},{MAX_LON}")
    print(f"bbox_m_approx: {meta['bbox_m_approx']}")
    print(f"deg_per_px: lat={DEG_PER_PX_LAT:.6e} lon={DEG_PER_PX_LON:.6e}")
    print(f"spawn: {spawn}")
    print(f"water_polys: {n_water_poly} canal_segs: {n_canal_seg} haven_segs: {n_haven_seg}")
    print(f"ijsselmeer: {n_ijsselmeer} synth_havens: {synth_havens}")
    print(f"buildings: {n_bld} road_segs: {n_road_seg} skip_huge: {n_skip_huge}")
    print(f"labels: {label_info.get('n_labels')} skip_dense: {label_info.get('n_skip_dense')} sample: {label_info.get('labels', [])[:12]}")
    print(f"water_px: {water_px} road_px: {road_px}")
    print(f"meta: data/street_world_meta.json")


if __name__ == "__main__":
    main()
