# lay6-tools - utilities for Sprint-Layout 6 files
# Copyright (C) 2026 Alex
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version. It is distributed
# WITHOUT ANY WARRANTY; see the LICENSE file for details.
"""lay6.py - writer/reader for the "06 33 AA FF" .lay6 board format (self-contained).

All geometry in UI millimetres: X from the left, Y as the app's UI shows it.
Stored Y = Y_ui - canvas_height. Stored units: 0.1 um, IEEE float32 little-endian.
Templates below come from files saved by the real application and are copied
verbatim except for the fields this module sets. See FORMAT.md for the spec.

Verified in the app (Oct 2026): SMD pads (any rotation), through-hole pads (round, square,
octagon, 2:1 oblong at any angle, plated, thermal), zones (solid, hatched, cutout, top and
bottom copper), lines, arcs, circles, outline-layer drawings, Latin and Georgian text,
groups, board and macro files, KiCad-converted footprints.
"""
import base64, os, struct

UNIT = 10000.0           # stored units per mm
COUNT_OFS = 0x21A        # u32 object count; records start right after
COPPER, SILK = 1, 2      # layer byte at +0x16 of every object
# Sprint-Layout 6 layers: 1 = C1 top copper, 2 = S1 top silk, 3 = C2 bottom copper (verified);
# also verified: 4 = S2 bottom silk, 7 = O outline; expected, unverified: 5 = I1, 6 = I2
C1, S1, C2, S2, I1, I2, OUTLINE = 1, 2, 3, 4, 5, 6, 7


def _b(*parts):
    return base64.b64decode("".join(parts))


HEAD = _b(
    "BjOq/wEAAAAHQm9hcmQgMUZAAMWNQADQ+p4B8EJJAPhCSQAAAAAAYHjUCUBCDwBAQg8AAAAAAAAA"
    "AAAAAAAAzshAL26jAbwFYj83AgAAqwEAAAEAAAABAQEBAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABYAgAAWAIAAAAAAAAAAAAA"
    "AAAAAAAAAAAA/wAAAP8AAAAAAADAvfD/AA==")  # 538 bytes
TAIL = _b(
    "APqeAYA7aQZ8+p4BQlVWAJj6ngGAO2kGjPqeAUJVVgC7D7kB7GJuBoA7aQZ0FHIGgDtpBpj6ngFv"
    "VkkA7GJuBoA7aQao+p4Bb1ZJAIA7aQYSsAAAAAAAAAAAAAAAAAAA1PqeAYoA+p4BgDtpBnz6ngFC"
    "VVYAmPqeAYA7aQaM+p4BQlVWALsPuQHsYm4GgDtpBnQUcgaAO2kGmPqeAW9WSQDsYm4GgDtpBqj6"
    "ngFvVkkAgDtpBhKwAAAAAAAAAAAAAAAAAADU+p4BigD6ngGAO2kGfPqeAUJVVgCY+p4BgDtpBoz6"
    "ngFCVVYAuw+5AexibgaAO2kGdBRyBoA7aQaY+p4Bb1ZJAOxibgaAO2kGqPqeAW9WSQCAO2kGErAA"
    "AAAAAAAAAAAAAAAAANT6ngGKAAAAAA==")  # 307 bytes
PAD = _b(
    "CAA+30gA0qXIAEAcRgBAHEYAAAAAAAEA2HXUCQAAAFUAAAAwddQJAACgDwAAIHbUCQAAAABkAAAA"
    "AAEAAAAVzVsHAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEAAAAAM3cSABho8gAr+FIAGGjyACv4UgA"
    "Q6jIAM3cSABDqMg=")  # 125 bytes
LINE = _b(
    "BgAAAABkAAAAAAB6RgAA+kTcBQAAAAIAID35EQAAAAA5MAAIPfkRArygDwAAeHmEAgAwMAAAAAAb"
    "AAAAAAAAAAsAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")  # 89 bytes
TRACK = _b(
    "BgAAAAAAAAAAAAAAAAAAAAC4CwAAAAMAoGTcDgAAAAAAAADYYtwOAACgDwAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")  # 89 bytes: app-drawn 0.3 mm track header
CIRCLE = _b(
    "BVUxCUmQXOXIAEgKRgCgcEUgvwIAAAIAAAAAAAAAACC/AgDIWPkRAACgDwAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")  # 89 bytes
TEXT_HDR = _b(
    "B4AK2UgA1ePIAECcRgAAgD8BAAAAAAIAAAAAAAAAAAAAAABgZdwOAAGgDwAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAA=")  # 77 bytes
STROKE_HDR = _b(
    "BgAAAAAAAAAAAAAAAAAAAACuCAAAAAIAMGXcDgAAAAAAAAAQZNwOAACgDwAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAA=")  # 77 bytes


# --------------------------------------------------------------------------- objects
def _f(v_mm):
    return v_mm * UNIT


def pad(x, y, w, h, canvas_h=100.0, layer=COPPER):
    """Rectangular SMD pad centred at (x, y), size w x h. 125 bytes."""
    r = bytearray(PAD)
    X, Y, W, H = _f(x), _f(y - canvas_h), _f(w), _f(h)
    struct.pack_into("<4f", r, 0x01, X, Y, W, H)
    r[0x16] = layer
    pts = [(X - W/2, Y + H/2), (X + W/2, Y + H/2), (X + W/2, Y - H/2), (X - W/2, Y - H/2)]
    struct.pack_into("<8f", r, 0x5D, *[c for p in pts for c in p])
    return bytes(r)


def line(points, width=0.2, canvas_h=100.0, layer=SILK):
    """Polyline through points [(x, y), ...], type 6. On a copper layer this is a TRACK.
    Width = u32 at +0x11 (0.1 um units) - the field the app uses for silkscreen lines and tracks
    alike (verified 0.2 ... 0.8 mm). The f32 at +0x0D is ignored by the app (it writes 0 there
    for tracks). Tracks follow pads they end on when the app's "rubber band" button is on
    (verified, also for bent tracks) - there is no stored link, only matching end points."""
    copper = layer in (C1, C2, I1, I2)
    r = bytearray(TRACK if copper else LINE)
    r[0x16] = layer
    if not copper:
        struct.pack_into("<f", r, 0x0D, _f(width))
    struct.pack_into("<I", r, 0x11, int(round(_f(width))))
    r += struct.pack("<I", len(points))
    for x, y in points:
        r += struct.pack("<2f", _f(x), _f(y - canvas_h))
    return bytes(r)


def track(points, width=0.3, canvas_h=100.0, layer=C1):
    """Copper track (C1 top / C2 bottom) through points - same as line() on a copper layer."""
    return line(points, width, canvas_h, layer)


def rect(x0, y0, x1, y1, width=0.2, canvas_h=100.0, layer=SILK):
    c = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]
    return [line([c[i], c[(i + 1) % 4]], width, canvas_h, layer) for i in range(4)]


def circle(x, y, canvas_h=100.0, layer=SILK, radius=None, width=None):
    """Full circle. Without radius/width: the app's pin-1 dot (radius 0.635, line 0.5 mm)."""
    if radius is None and width is None:
        r = bytearray(CIRCLE)                      # verbatim template (stores 180/180 = full)
        r[0x16] = layer
        struct.pack_into("<2f", r, 0x01, _f(x), _f(y - canvas_h))
        return bytes(r)
    return arc(x, y, radius if radius is not None else 0.635, width if width is not None else 0.5,
               0, 0, canvas_h, layer)


def arc(x, y, radius, width, start, end, canvas_h=100.0, layer=SILK):
    """Arc / circle, type 5, 89 bytes. Angles in degrees, 0 = +X (right), counter-clockwise
    from start to end; start == end gives a full circle.
    Stored: +0x01/+0x05 centre, +0x09 OUTER radius, +0x0D INNER radius (f32),
            +0x11 END angle, +0x1F START angle (u32 millidegrees).
    Verified in the app: quarter, half and three-quarter arcs, full circles."""
    r = bytearray(CIRCLE)
    r[0x16] = layer
    struct.pack_into("<4f", r, 0x01, _f(x), _f(y - canvas_h),
                     _f(radius + width / 2), _f(radius - width / 2))
    struct.pack_into("<I", r, 0x11, int(round((end % 360) * 1000)))
    struct.pack_into("<I", r, 0x1F, int(round((start % 360) * 1000)))
    return bytes(r)


def text(label, strokes, x, y, canvas_h=100.0, height=None, layer=SILK, stroke_w=None,
         mirror_h=False, mirror_v=False, rotation=0, weight="normal", width="normal"):
    """Text object. label = ASCII string shown in the app's edit box.
    strokes = list of polylines [(x, y), ...] in UI mm - this is what the app DRAWS.
    The app does not re-render on load; if the user edits the label in the app,
    it re-renders the strokes from the label with its own font.
    stroke_w = line width of the strokes in mm (the app uses height / 9; default: the
    template's 0.2222, i.e. 2 mm text).
    mirror_h / mirror_v: the app's mirror flags (u8 +0x32 horizontal, +0x33 vertical). They do not
    move the strokes - pass strokes already mirrored about the anchor (latin_text() does).
    rotation: the app's text angle in whole degrees, CLOCKWISE (u32 at +0x35, little-endian; 45, 90
    and 279 verified - 279 is stored 17 01 00 00). Like the flags it does not move the strokes.
    weight / width: the app's thickness (thin / normal / bold: f32 at +0x0D = 0 / 1 / 2) and
    character width (narrow / normal / wide: u8 at +0x11 = 0 / 1 / 2) settings; record only.
    stroke_w None = the app's width for the weight (text_stroke_width()), if height is given."""
    h = bytearray(TEXT_HDR)
    struct.pack_into("<I", h, 0x35, int(round(rotation)) % 360)
    struct.pack_into("<f", h, 0x0D, float(TEXT_WEIGHT[weight]))
    h[0x11] = TEXT_WIDTH[width]
    if stroke_w is None and height is not None:
        stroke_w = text_stroke_width(height, weight)
    h[0x32] = 1 if mirror_h else 0
    h[0x33] = 1 if mirror_v else 0
    struct.pack_into("<2f", h, 0x01, _f(x), _f(y - canvas_h))
    if height is not None: struct.pack_into("<f", h, 0x09, _f(height))
    h[0x16] = layer
    r = bytes(h) + struct.pack("<I", len(label)) + label.encode("ascii")
    r += struct.pack("<III", 0, 0, len(strokes))
    sh = bytearray(STROKE_HDR)
    if stroke_w is not None:
        struct.pack_into("<I", sh, 0x11, int(round(_f(stroke_w))))
    for s in strokes:
        r += bytes(sh) + struct.pack("<I", len(s))
        r += b"".join(struct.pack("<2f", _f(px), _f(py - canvas_h)) for px, py in s)
    return r


_SPRINT_FONT = None


TEXT_WEIGHT = {"thin": 0, "normal": 1, "bold": 2}       # f32 at +0x0D (0.0 / 1.0 / 2.0)
TEXT_WIDTH = {"narrow": 0, "normal": 1, "wide": 2}       # u8 at +0x11
_X_SCALE = {0: 2.0 / 3, 1: 1.0, 2: 4.0 / 3}              # glyph width factor (verified)
_GAP_U = {0: 8.0 / 3, 1: 11.0 / 3, 2: 14.0 / 3}         # letter gap in height/15 units, by weight
_STROKE = {0: 5.0 / 81, 1: 1.0 / 9, 2: 10.0 / 63}         # stroke width / height, by weight (1 and 2 mm verified)


def text_stroke_width(height, weight="normal"):
    """The app's stroke width for a text of this height and weight (thin / normal / bold)."""
    return height * _STROKE[TEXT_WEIGHT[weight]]


def sprint_font_strokes(s, height, weight="normal", width="normal"):
    """Strokes of s in Sprint-Layout's own label font (sprint_font.json, captured from the app),
    baseline at y = 0, starting at x = 0, in mm. None if a character has no glyph.
    Grid: x step height/15 (times 2/3 narrow, 4/3 wide), y step height/18. After every glyph a
    gap of 11/3 x-steps (8/3 thin, 14/3 bold; not scaled by the width setting); a space is a
    0.2-step blank glyph plus its gap. Verified against the app for all weights and widths."""
    global _SPRINT_FONT
    if _SPRINT_FONT is None:
        import json
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprint_font.json")
        g = json.load(open(path, encoding="utf-8"))["glyphs"]
        norm = {}
        for ch, sts in g.items():
            xs = [p[0] for st in sts for p in st]
            flip = max(xs) <= 0 < -min(xs)              # a few glyphs were captured mirrored
            sts = [[(-x if flip else x, y) for x, y in st] for st in sts]
            x0 = min(p[0] for st in sts for p in st)
            norm[ch] = [[(x - x0, y) for x, y in st] for st in sts]
        _SPRINT_FONT = norm
    wt, wd = TEXT_WEIGHT[weight], TEXT_WIDTH[width]
    u = height / 15.0
    sx, sy, gap = u * _X_SCALE[wd], height / 18.0, u * _GAP_U[wt]
    out, x = [], 0.0
    for ch in s:
        if ch == " ":
            x += 0.2 * sx + gap
            continue
        sts = _SPRINT_FONT.get(ch)
        if sts is None:
            return None
        for st in sts:
            out.append([(x + px * sx, py * sy) for px, py in st])
        x += max(p[0] for st in sts for p in st) * sx + gap
    return out


THT = _b(
    "AgA+30gAbtfIAEAcRgBAnEUAAAAAAAEB0FvWDgAAAFVVVVXIY9YOAACgDwAA6FvWDgAAAABkAAAA"
    "AAEAAAAVzVsHAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")  # 89 bytes: through-hole pad header, no point list


# through-hole pad shape byte (+0x17); 'rounded' = the app's "rounded" option
THT_SHAPES = {"round": 1, "octagon": 2, "square": 3, "rounded": 4}


def tht_pad(x, y, outer, drill, canvas_h=100.0, plated=False, thermal=False,
            shape="round", layer=C1, length=None, angle=0.0):
    """Through-hole pad, type 2. outer / drill are DIAMETERS in mm (as in the app's dialog).
    Stored: +0x01/+0x05 centre, +0x09 outer RADIUS, +0x0D drill RADIUS (f32), then a point
    list (the outline). Counts as a pad in the per-pad tail list. All variants below were
    reproduced byte for byte from app-saved files (except pointers / selection flag).
      shape    round / octagon / square / rounded   u8 at +0x17 (1/2/3/4); point list = outline:
             round: 2 points (x-r, y)-(x+r, y), ignored when drawing; square 4 corners;
             octagon 8 corners at 0.415*r (app's rounding); rounded = OBLONG: the segment
             between its 2 points swept by the pad radius. App default: points at +-r on X,
             i.e. 2*outer long, 1*outer wide - ALWAYS: other lengths display on load but the app
             rebuilds them to +-r when the pad is edited, so `length` is ignored. angle (deg, CCW
             from +X) is kept by the app - verified horizontal, vertical, 45 deg.
      layer    copper side, u8 at +0x16 (C1 = 1 top, C2 = 3 bottom)
      plated   "through pad" (hole metallisation)  u8 at +0x39 - unplated holes do NOT connect
               top and bottom copper
      thermal  "thermal pad" (thermal relief)     u8 at +0x32; the app also set +0x39 with it,
               so thermal=True implies plated=True here (same as the app's saved file)."""
    r = bytearray(THT)
    X, Y, R, D = _f(x), _f(y - canvas_h), _f(outer) / 2, _f(drill) / 2
    struct.pack_into("<4f", r, 0x01, X, Y, R, D)
    r[0x39] = 1 if (plated or thermal) else 0
    r[0x32] = 1 if thermal else 0
    r[0x16] = layer
    r[0x17] = THT_SHAPES[shape]
    if shape == "round":                       # circle; the 2 points are not used for drawing
        pts = [(X - R, Y), (X + R, Y)]
    elif shape == "rounded":                   # oblong: segment p1-p2 swept by the pad radius
        import math
        # The app ALWAYS uses points at +-R (2:1 oblong): any other length displays on load but
        # is rebuilt to +-R as soon as the pad is edited. So `length` is ignored.
        h = R
        a = math.radians(angle)
        dx, dy = h * math.cos(a), h * math.sin(a)
        pts = [(X - dx, Y - dy), (X + dx, Y + dy)]
    elif shape == "square":
        pts = [(X - R, Y + R), (X + R, Y + R), (X + R, Y - R), (X - R, Y - R)]
    else:                                      # regular octagon, flats on the axes
        a = float(int(R * 0.415 + 0.5))        # app uses 0.415 x R, rounded half-up to 0.1 um (measured)
        pts = [(X - R, Y + a), (X - a, Y + R), (X + a, Y + R), (X + R, Y + a),
               (X + R, Y - a), (X + a, Y - R), (X - a, Y - R), (X - R, Y - a)]
    r += struct.pack("<I", len(pts)) + b"".join(struct.pack("<2f", px, py) for px, py in pts)
    return bytes(r)


ZONE = _b(
    "BAAAAAAAAAAAAAAAAAAAAAC4CwAAAAMAkI3DCQAAAAAAAABIeMMJAACgDwAAAAAAAAAAAQC4CwAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")  # 89 bytes: zone header, no point list


def zone(points, width=0.3, canvas_h=100.0, layer=C2, hatch=False, hatch_width=None,
         cutout=False):
    """Copper zone (filled polygon of any shape), type 4. points = outline corners in UI mm.
      width       outline thickness            u32 at +0x11 (0.1 um units)
      hatch       hatched instead of solid     u8  at +0x32
      hatch_width None -> hatch uses the outline thickness ("outline" mode, +0x33 = 1);
                  a value -> "self-defined" mode (+0x33 = 0) with that thickness
                  self-defined hatch thickness u32 at +0x35 (holds the outline value otherwise)
      cutout      "cutout area" option         u8  at +0x34
    Centre/size fields stay 0 - the outline alone defines the zone. Zones add nothing to the
    per-pad tail list. The "cutout soldermask" option (u8 at +0x4A) is NOT supported yet:
    the app then writes 5 at the point-count field with only 4 points stored (unexplained).
    Verified in the app: shapes of 4 and 6 corners, widths, hatch, both hatch modes, cutout."""
    r = bytearray(ZONE)
    r[0x16] = layer
    w = int(round(_f(width)))
    struct.pack_into("<I", r, 0x11, w)
    r[0x32] = 1 if hatch else 0
    r[0x33] = 1 if hatch_width is None else 0
    r[0x34] = 1 if cutout else 0
    struct.pack_into("<I", r, 0x35, w if hatch_width is None else int(round(_f(hatch_width))))
    r += struct.pack("<I", len(points))
    for x, y in points:
        r += struct.pack("<2f", _f(x), _f(y - canvas_h))
    return bytes(r)


# ----------------------------------------------------------------- app font (Latin)
def app_font_strokes(s, x0, y0, height=2.0, weight="normal", width="normal"):
    """Strokes of s in the app's own label font, baseline-left at (x0, y0), UI mm.
    Raises KeyError for a character the app's font does not have."""
    st = sprint_font_strokes(s, height, weight, width)
    if st is None:
        raise KeyError("character not in the app's font: %r" % s)
    return [[(x0 + x, y0 + y) for x, y in q] for q in st]


def latin_text(s, x, y, height=2.0, canvas_h=100.0, layer=SILK, mirror_h=False, mirror_v=False,
               rotation=0, weight="normal", width="normal"):
    """Text exactly as the app would draw it: its own font, stroke width height/9.
    Editing it in the app gives the same strokes. Mirroring is about the anchor (x, y), as in the
    app: horizontal = the text runs leftwards from x, vertical = it hangs down from y (verified)."""
    import math
    st = [[(x + (x - px if mirror_h else px - x), y + (y - py if mirror_v else py - y)) for px, py in q]
          for q in app_font_strokes(s, x, y, height, weight, width)]
    if rotation:              # clockwise about the anchor (verified 45, 90 and 279 degrees, no mirroring)
        a = math.radians(-rotation)
        c, sn = math.cos(a), math.sin(a)
        st = [[(x + (px - x) * c - (py - y) * sn, y + (px - x) * sn + (py - y) * c) for px, py in q] for q in st]
    return text(s, st, x, y, canvas_h, height=height, layer=layer,
                stroke_w=text_stroke_width(height, weight), mirror_h=mirror_h, mirror_v=mirror_v,
                rotation=rotation, weight=weight, width=width)


def set_name(rec, value):
    """Set the hidden element-name string of a record (u32 length + bytes at +0x51, after the
    label for text). Sprint-Layout 6.0 keeps it through editing and saving and shows it nowhere,
    so it can carry extra data (e.g. UTF-8). At most 255 bytes: 256 load but are saved as 255,
    257 or more make the app quit while opening the file."""
    v = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if len(v) > 255:
        raise ValueError("element name longer than 255 bytes")
    r = bytes(rec)
    p = 0x51 + struct.unpack_from("<I", r, 0x4D)[0] if r[0] == 7 else 0x51
    n = struct.unpack_from("<I", r, p)[0]
    return r[:p] + struct.pack("<I", len(v)) + v + r[p + 4 + n:]


def set_mask(rec, exposed=True):
    """Solder mask setting of a pad, track, line or zone record (u8 at +0x3A): 1 = no solder
    mask over it (exposed copper), 0 = covered by solder mask. Verified for SMD pads, through-hole
    pads, tracks and zones. Defaults from the templates: pads 1 (exposed), tracks/zones 0."""
    r = bytearray(rec)
    r[0x3A] = 1 if exposed else 0
    return bytes(r)


# ------------------------------------------------------------------------- file I/O
def build(path, objects, canvas_h=100.0, canvas_w=None, name=None):
    """objects: list of records (bytes) or lists of records. Writes a one-board file.
    canvas_w / canvas_h: canvas size in mm (header +0x23 / +0x27, i.e. file 0x2B / 0x2F). Make
    every object's canvas_h argument the same value as canvas_h here (stored Y = Y_ui - canvas_h).
    Sizes other than 100 x 100 open fine in the app (124 x 100, 152 x 100 seen). name: board
    (tab) name, at most 9 characters."""
    recs = []
    for o in objects:
        recs += o if isinstance(o, list) else [o]
    n_pads = sum(1 for r in recs if r[0] in (2, 8))
    head = bytearray(HEAD)
    struct.pack_into("<I", head, 0x2B, int(round((canvas_w or 100.0) * UNIT)))
    struct.pack_into("<I", head, 0x2F, int(round(canvas_h * UNIT)))
    if name is not None:
        nb = name.encode("cp1251")[:9]
        head[0x08] = len(nb)
        head[0x09:0x09 + len(nb)] = nb
    data = bytes(head) + struct.pack("<I", len(recs)) + b"".join(recs) + bytes(4 + 4 * n_pads) + TAIL
    with open(path, "wb") as f:
        f.write(data)
    return data


SELECT = None    # board to work on in multi-board files: None = first board with objects, or 1, 2, ...


def boards(data):
    """All boards of a file: [{name, header, count_ofs, start, end, n, canvas_w, canvas_h}].
    Layout: magic, u32 number of boards, then per board a 0x212-byte header (u8 name length +
    name, canvas width/height at +0x23/+0x27), u32 object count, the objects and one u32 per pad
    of that board; after the last board one u32 and the trailer (307 bytes in most files)."""
    nb = struct.unpack_from("<I", data, 4)[0]
    if not 1 <= nb <= 1000:
        raise ValueError("implausible number of boards: %d" % nb)
    out, o = [], 8
    for _ in range(nb):
        nl = data[o]
        b = dict(name=data[o + 1:o + 1 + nl].decode("cp1251", "replace"), header=o, count_ofs=o + 0x212,
                 start=o + 0x216, n=struct.unpack_from("<I", data, o + 0x212)[0],
                 canvas_w=struct.unpack_from("<I", data, o + 0x23)[0] / 1e4,
                 canvas_h=struct.unpack_from("<I", data, o + 0x27)[0] / 1e4)
        p, pads = b["start"], 0
        for _ in range(b["n"]):
            if data[p] in (2, 8):
                pads += 1
            p += record_length(data, p)
        b["end"] = p                  # end of the objects (new objects are inserted here)
        b["pads"] = pads
        out.append(b)
        o = p + 4 * pads              # this board's pad list (one u32 per pad) comes next
    return out


def board(data):
    """The board the tools work on (see SELECT)."""
    bs = boards(data)
    if SELECT:
        if not 1 <= SELECT <= len(bs):
            raise ValueError("this file has %d board(s); --board %d does not exist" % (len(bs), SELECT))
        return bs[SELECT - 1]
    for b in bs:
        if b["n"]:
            return b
    return bs[0]


def count_ofs(data):
    return board(data)["count_ofs"]


def canvas(data):
    """(width, height) in mm of the selected board's canvas."""
    b = board(data)
    return b["canvas_w"], b["canvas_h"]


def record_length(data, o):
    """Length of the object record at o (the format has no length field)."""
    t = data[o]
    if t == 7:
        L = struct.unpack_from("<I", data, o + 0x4D)[0]
        p = o + 0x51 + L
        p += 4 + struct.unpack_from("<I", data, p)[0]   # element name string (usually empty)
        k = struct.unpack_from("<I", data, p)[0]
        p += 4 + 4 * k
        m = struct.unpack_from("<I", data, p)[0]
        p += 4
        for _ in range(m):
            q = struct.unpack_from("<I", data, p + 0x4D)[0]
            p += 0x51 + 8 * q
        if data[o + 0x17] == 1:          # a component's ID text: component properties follow
            p += 4 + 4 + 1 + 8           # f32 X/Y offset, u8 centre mode, f64 rotation
            for _ in range(2):           # u32 length + package string, then comment string
                p += 4 + struct.unpack_from("<I", data, p)[0]
            p += 1                       # u8 "use pick+place data"
        return p - o
    if t not in (2, 4, 5, 6, 8):
        raise ValueError("unknown object type %d at offset 0x%X" % (t, o))
    s = struct.unpack_from("<I", data, o + 0x51)[0]
    k = struct.unpack_from("<I", data, o + 0x55 + s)[0]
    p = o + 0x59 + s + 4 * k
    if t == 5:
        return p - o
    m = struct.unpack_from("<I", data, p)[0]
    return p + 4 + 8 * m - o


def walk(data):
    """Yield (offset, type, length) for the objects of the selected board (see board())."""
    b = board(data)
    o = b["start"]
    for _ in range(b["n"]):
        ln = record_length(data, o)
        yield o, data[o], ln
        o += ln


# ---------------------------------------------------------------- footprint helpers
def dual_row(n_pins, pitch, row, pad_w, pad_h, cx=50.0, cy=50.0):
    """SOIC/WSON style: rows vertical, pin 1 top-left, CCW numbering."""
    half = n_pins // 2
    ys = [cy + (half - 1) / 2 * pitch - i * pitch for i in range(half)]
    return ([(cx - row / 2, yy, pad_w, pad_h) for yy in ys] +
            [(cx + row / 2, yy, pad_w, pad_h) for yy in reversed(ys)])


def quad(n_pins, pitch, row, pad_len, pad_wid, cx=50.0, cy=50.0):
    """QFP style: pin 1 top of left side, CCW. row = centre-to-centre of opposite rows."""
    k = n_pins // 4
    off = [((k - 1) / 2 - i) * pitch for i in range(k)]
    C = row / 2
    return ([(cx - C, cy + o, pad_len, pad_wid) for o in off] +
            [(cx - o, cy - C, pad_wid, pad_len) for o in off] +
            [(cx + C, cy - o, pad_len, pad_wid) for o in off] +
            [(cx + o, cy + C, pad_wid, pad_len) for o in off])


# ------------------------------------------------------------------------- grouping
def group(objects, gid):
    """Put objects (records or lists of records) into group `gid` and return the new records.
    A group is only a tag list on each member: u32 count at +0x55 (text: after the string),
    then the group ids, INNERMOST FIRST. There is no separate group record. Nesting is done
    by grouping already-grouped records again - the outer id is appended:
        pins_l = group(left_pads, 1); pins_r = group(right_pads, 2)
        part   = group(pins_l + pins_r + silk, 3)   # pads get [1, 3] / [2, 3], silk gets [3]
    Ids are plain integers chosen by the app (1, 2, 3 ... in creation order).
    Verified against app-saved files: one level, and two levels of nesting."""
    out = []
    for o in objects:
        for r in (o if isinstance(o, list) else [o]):
            if r[0] == 7:
                L = struct.unpack_from("<I", r, 0x4D)[0]
                p = 0x51 + L
                p += 4 + struct.unpack_from("<I", r, p)[0]   # element name string (usually empty)
            else:
                p = 0x55 + struct.unpack_from("<I", r, 0x51)[0]   # after the element name string
            k = struct.unpack_from("<I", r, p)[0]
            refs = list(struct.unpack_from("<%dI" % k, r, p + 4)) + [gid]
            out.append(r[:p] + struct.pack("<%dI" % (len(refs) + 1), len(refs), *refs) + r[p + 4 + 4 * k:])
    return out


# ------------------------------------------------------------------- macro (.LMK) files
# Library component ("macro"): magic, u32 object count, the object records (same layout
# as on a board), 4 + 4*n_pads zero bytes, then a 98-byte block (pointers; copied).
# No board header. Geometry is RELATIVE to an origin the app picks itself: the centre
# of the leftmost pad (ties: the topmost). Point lists and circle centres are shifted;
# pad centre fields keep their absolute board values (the app ignores them).
MACRO_TAIL = _b(
    "AAAAHPqeAar4UXacQWMGVPqeAdYtQgCkVLIOTkZAADSWWwbslVsGoKTGCf////8AAAAAAAAAHPqe"
    "Aar4UXacQWMGVPqeAdYtQgCkVLIOTkZAADSWWwbslVsGoKTGCf////8=")  # 98 bytes


def _shift(rec, dx, dy):
    r = bytearray(rec)
    t = r[0]
    if t == 7:        # text: anchor + every stroke point (UNVERIFIED against an app-saved macro)
        x, y = struct.unpack_from("<2f", r, 0x01)
        struct.pack_into("<2f", r, 0x01, x - dx, y - dy)
        L = struct.unpack_from("<I", r, 0x4D)[0]
        p = 0x51 + L
        p += 4 + struct.unpack_from("<I", r, p)[0]   # element name string (usually empty)
        p += 4 + 4 * struct.unpack_from("<I", r, p)[0]
        n = struct.unpack_from("<I", r, p)[0]
        p += 4
        for _ in range(n):
            m = struct.unpack_from("<I", r, p + 0x4D)[0]
            for j in range(m):
                q = p + 0x51 + 8 * j
                x, y = struct.unpack_from("<2f", r, q)
                struct.pack_into("<2f", r, q, x - dx, y - dy)
            p += 0x51 + 8 * m
        return bytes(r)
    if t in (2, 5):   # circle; through-hole pad (app shifts the centre too - verified on DIP-8 macro)
        x, y = struct.unpack_from("<2f", r, 0x01)
        struct.pack_into("<2f", r, 0x01, x - dx, y - dy)
        if t == 5:
            return bytes(r)
    k = struct.unpack_from("<I", r, 0x55 + struct.unpack_from("<I", r, 0x51)[0])[0]
    p = 0x59 + struct.unpack_from("<I", r, 0x51)[0] + 4 * k
    m = struct.unpack_from("<I", r, p)[0]
    for j in range(m):
        x, y = struct.unpack_from("<2f", r, p + 4 + 8 * j)
        struct.pack_into("<2f", r, p + 4 + 8 * j, x - dx, y - dy)
    return bytes(r)


def macro_origin(recs):
    """Stored-unit (x, y) of the leftmost pad centre (topmost on ties), from its corners."""
    best = None
    for r in recs:
        if r[0] not in (2, 8):
            continue
        p = 0x59 + struct.unpack_from("<I", r, 0x51)[0] + 4 * struct.unpack_from("<I", r, 0x55 + struct.unpack_from("<I", r, 0x51)[0])[0]
        m = struct.unpack_from("<I", r, p)[0]
        c = struct.unpack_from("<%df" % (2 * m), r, p + 4)
        cx, cy = (min(c[0::2]) + max(c[0::2])) / 2, (min(c[1::2]) + max(c[1::2])) / 2
        key = (round(cx), -round(cy))
        if best is None or key < best[0]:
            best = (key, cx, cy)
    if best is None:                 # no pads: keep coordinates as they are (UNVERIFIED)
        return 0.0, 0.0
    return best[1], best[2]


def build_macro(path, objects):
    """objects: the same records you would pass to build() (board coordinates)."""
    recs = []
    for o in objects:
        recs += o if isinstance(o, list) else [o]
    ox, oy = macro_origin(recs)
    body = b"".join(_shift(r, ox, oy) for r in recs)
    n_pads = sum(1 for r in recs if r[0] in (2, 8))
    data = b"\x06\x33\xaa\xff" + struct.pack("<I", len(recs)) + body + bytes(4 + 4 * n_pads) + MACRO_TAIL
    with open(path, "wb") as f:
        f.write(data)
    return data


if __name__ == "__main__":
    # Example: TQFP-44 (as verified in the app) with silkscreen body and pin-1 dot.
    pads = [pad(*p) for p in quad(44, 0.8, 12.2, 1.8, 0.5)]
    build("TQFP-44.lay6", pads + [rect(45, 45, 55, 55), circle(46, 54)])
