# lay6-tools - utilities for Sprint-Layout 6 files
# Copyright (C) 2026 Alex
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version. It is distributed
# WITHOUT ANY WARRANTY; see the LICENSE file for details.
"""lay6png.py - Sprint-Layout 6 board (.lay6) -> PNG images, one per layer or layers combined,
at an exact print scale. Made for toner transfer, photo films and UV solder-mask films.

    python lay6png.py board.lay6 [OUTNAME] [options]

Examples
    python lay6png.py board.lay6                      every used layer, 600 dpi, black on white,
                                                      top layers mirrored for toner transfer
    python lay6png.py board.lay6 out --dpi 1200 --layers c1,c2 --holes center --grow 0.05
    python lay6png.py board.lay6 out --layers m1,m2   solder-mask films (openings black)
    python lay6png.py board.lay6 out --color --combine c2,c1,s1 --mirror no   coloured view

Layers: c1 top copper, s1 top silkscreen, c2 bottom copper, s2 bottom silkscreen,
        i1 / i2 inner copper, o outline, m1 / m2 top / bottom solder-mask openings.

Every image of one run has the same size and frame (the board outline, or everything on the
board, plus the margin), so top and bottom images line up. The resolution is stored in the
PNG: print at 100 % / "actual size" (not "fit to page") to get the true size. Check the
first print with a ruler or by laying a part on it.

Mirroring (--mirror auto, the default): Sprint-Layout shows the board from the top. For toner
transfer the print lies face down on the copper, so top-side layers are printed mirrored and
bottom-side layers as they are. --mirror no gives the view of Sprint-Layout.

Needs: lay6.py and lay6cam.py next to it, and   pip install shapely pillow
"""
import argparse, math, os, struct, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lay6 as L

try:
    from shapely.geometry import LineString, Point, Polygon, box
    from shapely.ops import unary_union
    from PIL import Image, ImageDraw, ImageColor
except ImportError:
    sys.exit("lay6png needs shapely and pillow:   pip install shapely pillow")
import lay6cam as C

# Sprint-Layout layer numbers (+0x16) and names used here
NUM = {"c1": 1, "s1": 2, "c2": 3, "s2": 4, "i1": 5, "i2": 6, "o": 7}
COPPER = ("c1", "c2", "i1", "i2")
TOP_SIDE = ("c1", "s1", "m1")
BOTTOM_SIDE = ("c2", "s2", "m2")
ALL = ("c1", "s1", "c2", "s2", "i1", "i2", "o", "m1", "m2")
# Sprint-Layout's drawing order (bottom first), with the masks under the silkscreen
ORDER = ("c2", "i2", "i1", "c1", "m2", "m1", "s2", "s1", "o")
TITLE = {"c1": "C1 top copper", "s1": "S1 top silkscreen", "c2": "C2 bottom copper",
         "s2": "S2 bottom silkscreen", "i1": "I1 inner copper 1", "i2": "I2 inner copper 2",
         "o": "O outline", "m1": "top solder mask openings", "m2": "bottom solder mask openings"}
# Sprint-Layout's screen colours (same as Lay6Edit's defaults)
COLORS = {"c1": "#3C78F0", "s1": "#F02828", "c2": "#28C828", "s2": "#E6D200", "i1": "#E68C28",
          "i2": "#28C8C8", "o": "#F0F0F0", "m1": "#B070E0", "m2": "#E070B0"}


# ------------------------------------------------------------------------------ reading
def u32(d, p):
    return struct.unpack_from("<I", d, p)[0]


def read(data):
    """-> (geoms {layer: [geometry]}, mask {"m1"/"m2": [geometry]}, holes [(x, y, d)],
    outline [LineString], notes [str]). Millimetres, Y up, origin = canvas lower-left."""
    cw, ch = L.canvas(data)
    g = defaultdict(list)
    mask = defaultdict(list)
    holes, outline, notes = [], [], set()
    tht = []
    for o, t, ln in L.walk(data):
        num = data[o + 0x16]
        lay = {v: k for k, v in NUM.items()}.get(num)
        exposed = data[o + 0x3A] != 0
        if t == 2:                                             # through-hole pad
            x, y, R, D = struct.unpack_from("<4f", data, o + 1)
            x, y, R, D = x / 1e4, y / 1e4 + ch, R / 1e4, D / 1e4
            pts = C._points(data, o, ch)
            shape = data[o + 0x17]
            if shape in (2, 3) and len(pts) >= 3:
                geo = Polygon(pts)
            elif shape == 4 and len(pts) >= 2:
                geo = LineString([pts[0], pts[-1]]).buffer(R, quad_segs=16)
            else:
                geo = Point(x, y).buffer(R, quad_segs=16)
            if lay in COPPER:
                tht.append(geo)
                if exposed:
                    mask["m1"].append(geo); mask["m2"].append(geo)
            elif lay:
                g[lay].append(geo)
            if D > 0:
                holes.append((x, y, 2 * D))
            continue
        if lay is None:
            continue
        geo = None
        if t == 8:                                             # SMD pad
            pts = C._points(data, o, ch)
            if len(pts) >= 3:
                geo = Polygon(pts)
        elif t == 6:                                           # line / track
            w = u32(data, o + 0x11) / 1e4
            pts = C._points(data, o, ch)
            if pts:
                geo = C._line(pts, w)
                if lay == "o" and len(pts) >= 2:
                    outline.append(LineString(pts))
        elif t == 5:                                           # arc / circle
            x, y, Ro, Ri = struct.unpack_from("<4f", data, o + 1)
            end, start = u32(data, o + 0x11) / 1000.0, u32(data, o + 0x1F) / 1000.0
            r, w = (Ro + Ri) / 2e4, (Ro - Ri) / 1e4
            pts = C._arc_pts(x / 1e4, y / 1e4 + ch, r, start, end)
            geo = C._line(pts, w)
            if lay == "o":
                outline.append(LineString(pts))
        elif t == 4:                                           # zone
            if data[o + 0x34]:
                notes.add("cut-out areas are not drawn (they only matter for Sprint's ground plane)")
                continue
            w = u32(data, o + 0x11) / 1e4
            pts = C._points(data, o, ch)
            if len(pts) >= 3:
                geo = Polygon(pts).buffer(w / 2, quad_segs=8)
                if not geo.is_valid:
                    geo = geo.buffer(0)
                if data[o + 0x32]:
                    notes.add("hatched zones are drawn solid")
        elif t == 7:                                           # text: its strokes
            h = struct.unpack_from("<f", data, o + 0x09)[0] / 1e4
            parts = [C._line(st, w if 0 < w <= max(h, 0.5) else h / 9)
                     for st, w in text_strokes(data, o, ch) if st]
            if parts:
                geo = unary_union(parts)
        if geo is not None and not geo.is_empty:
            g[lay].append(geo)
            if lay in ("c1", "c2") and exposed and t != 7:
                mask["m1" if lay == "c1" else "m2"].append(geo)
    # through-hole pads are on both outer copper layers, and on inner layers that are in use
    used_inner = [l for l in ("i1", "i2") if g[l]]
    for l in ("c1", "c2") + tuple(used_inner):
        g[l].extend(tht)
    return g, mask, holes, outline, sorted(notes)


def text_strokes(d, o, ch):
    """[(points, width)] of a text object; each stroke carries its own width (+0x11)."""
    p = o + 0x51 + u32(d, o + 0x4D)
    p += 4 + u32(d, p)
    p += 4 + 4 * u32(d, p)
    n = u32(d, p); p += 4
    out = []
    for _ in range(n):
        m = u32(d, p + 0x4D)
        w = u32(d, p + 0x11) / 1e4
        c = struct.unpack_from("<%df" % (2 * m), d, p + 0x51)
        out.append(([(c[2 * i] / 1e4, c[2 * i + 1] / 1e4 + ch) for i in range(m)], w))
        p += 0x51 + 8 * m
    return out


# ------------------------------------------------------------------------------ geometry
def layer_geometry(name, g, mask, holes, outline, a):
    """Final shape of one layer (shapely geometry), holes and compensation applied."""
    if name in ("m1", "m2"):
        parts = mask.get(name, [])
        geo = unary_union(parts) if parts else Polygon()
        if a.mask_grow and not geo.is_empty:
            geo = geo.buffer(a.mask_grow, quad_segs=8)
        return geo
    parts = g.get(name, [])
    geo = unary_union(parts) if parts else Polygon()
    if name in COPPER and a.grow and not geo.is_empty:
        n0 = len(getattr(geo, "geoms", [geo]))
        geo = geo.buffer(a.grow, quad_segs=8)
        n1 = 0 if geo.is_empty else len(getattr(geo, "geoms", [geo]))
        if a.grow < 0 and n1 < n0:
            print("  warning: %s: --grow %.3f makes %d piece(s) of copper vanish or merge"
                  % (name, a.grow, n0 - n1))
    if name in COPPER and holes and a.holes != "none" and not geo.is_empty:
        cuts = []
        for x, y, d in holes:
            dd = d if a.holes == "full" else min(d, a.center_hole)
            if dd > 0:
                cuts.append(Point(x, y).buffer(dd / 2, quad_segs=12))
        if cuts:
            geo = geo.difference(unary_union(cuts))
    return geo


def frame(a, cw, ch, outline, everything):
    if a.area == "canvas":
        x0, y0, x1, y1 = 0.0, 0.0, cw, ch
    elif outline:
        x0, y0, x1, y1 = unary_union(outline).bounds
    elif not everything.is_empty:
        x0, y0, x1, y1 = everything.bounds
    else:
        x0, y0, x1, y1 = 0.0, 0.0, cw, ch
    m = a.margin
    return x0 - m, y0 - m, x1 + m, y1 + m


def marks(fr, a):
    """Registration crosses in the four corners of the margin."""
    x0, y0, x1, y1 = fr
    m = a.margin
    s = min(m * 0.8, 5.0) / 2
    w = max(0.15, a.line)
    out = []
    for cx, cy in ((x0 + m / 2, y0 + m / 2), (x1 - m / 2, y0 + m / 2),
                   (x0 + m / 2, y1 - m / 2), (x1 - m / 2, y1 - m / 2)):
        out.append(LineString([(cx - s, cy), (cx + s, cy)]).buffer(w / 2))
        out.append(LineString([(cx, cy - s), (cx, cy + s)]).buffer(w / 2))
    return unary_union(out)


# ------------------------------------------------------------------------------ drawing
def rasterize(geo, fr, px, mirror):
    """Geometry -> 1-bit image (1 = shape). Polygons with holes are drawn largest first, so an
    island inside another polygon's hole is drawn after that hole."""
    x0, y0, x1, y1 = fr
    W = max(1, int(math.ceil((x1 - x0) * px)))
    H = max(1, int(math.ceil((y1 - y0) * px)))
    img = Image.new("1", (W, H), 0)
    if geo is None or geo.is_empty:
        return img
    dr = ImageDraw.Draw(img)
    if mirror:
        T = lambda p: ((x1 - p[0]) * px, (y1 - p[1]) * px)
    else:
        T = lambda p: ((p[0] - x0) * px, (y1 - p[1]) * px)
    polys = []
    stack = [geo]
    while stack:
        s = stack.pop()
        if s.geom_type == "Polygon":
            polys.append(s)
        elif hasattr(s, "geoms"):
            stack.extend(s.geoms)
    polys.sort(key=lambda p: -Polygon(p.exterior).area)
    for p in polys:
        ext = [T(q) for q in p.exterior.coords]
        if len(ext) >= 3:
            dr.polygon(ext, fill=1)
        for i in p.interiors:
            hole = [T(q) for q in i.coords]
            if len(hole) >= 3:
                dr.polygon(hole, fill=0)
    return img


def save(img, path, dpi):
    img.save(path, dpi=(dpi, dpi))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lay6png.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n\n".join(__doc__.split("\n\n")[1:3]))
    ap.add_argument("board")
    ap.add_argument("outname", nargs="?", help="output name; files get _c1.png etc. added "
                    "(default: the board file name)")
    ap.add_argument("--layers", default="used",
                    help="comma list of c1,s1,c2,s2,i1,i2,o,m1,m2, or 'used' (default: every layer "
                    "with objects, without o, m1, m2) or 'all'")
    ap.add_argument("--combine", metavar="LIST",
                    help="draw these layers into ONE image instead (e.g. c2,c1,s1), in Sprint's order")
    ap.add_argument("--dpi", type=int, default=600, help="resolution (default 600)")
    ap.add_argument("--color", action="store_true",
                    help="Sprint-Layout's layer colours on a black background (default: black on white)")
    ap.add_argument("--ink", default="#000000", help="colour of the shapes in black-and-white mode")
    ap.add_argument("--paper", default="#FFFFFF", help="background in black-and-white mode")
    ap.add_argument("--bg", default="#000000", help="background in --color mode")
    ap.add_argument("--set-color", action="append", default=[], metavar="LAYER=#RRGGBB",
                    help="change a layer colour for --color, e.g. --set-color c1=#FF8000 (repeatable)")
    ap.add_argument("--mirror", default="auto", choices=["auto", "yes", "no"],
                    help="auto: top-side layers mirrored, bottom-side not (toner transfer). Default auto")
    ap.add_argument("--negative", action="store_true", help="swap shapes and background")
    ap.add_argument("--holes", default="center", choices=["center", "full", "none"],
                    help="drill holes in copper: center = small centring hole (default), full = real "
                    "size, none = not drawn")
    ap.add_argument("--center-hole", type=float, default=0.4,
                    help="diameter of the centring hole for --holes center, mm (default 0.4)")
    ap.add_argument("--grow", type=float, default=0.0,
                    help="make copper this much wider on every edge, mm (negative = thinner). "
                    "Compensates toner spread or under-etching. Default 0")
    ap.add_argument("--mask-grow", type=float, default=0.05,
                    help="solder-mask openings larger than the pads by this much on every edge, mm "
                    "(default 0.05)")
    ap.add_argument("--outline", default="yes", choices=["yes", "no"],
                    help="draw the board outline (O layer) into every image as a thin line (default yes)")
    ap.add_argument("--line", type=float, default=0.15, help="width of that outline line, mm (default 0.15)")
    ap.add_argument("--marks", action="store_true", help="registration crosses in the corners of the margin")
    ap.add_argument("--margin", type=float, default=3.0, help="space around the board, mm (default 3)")
    ap.add_argument("--area", default="board", choices=["board", "canvas"],
                    help="board = outline (or everything on the board) plus margin; canvas = "
                    "Sprint's whole canvas plus margin")
    ap.add_argument("--board", dest="board_no", type=int, metavar="N",
                    help="board number in files with several boards (default: the first one with objects)")
    a = ap.parse_args(argv)

    if a.dpi < 50 or a.dpi > 4800:
        sys.exit("--dpi must be between 50 and 4800")
    L.SELECT = a.board_no
    data = open(a.board, "rb").read()
    try:
        cw, ch = L.canvas(data)
        g, mask, holes, outline, notes = read(data)
    except (ValueError, struct.error) as e:
        sys.exit("cannot read %s: %s" % (a.board, e))
    out = a.outname or os.path.splitext(a.board)[0]

    def names(spec):
        if spec == "all":
            return list(ALL)
        if spec == "used":
            return [l for l in ("c1", "s1", "c2", "s2", "i1", "i2") if g.get(l)]
        ls = [s.strip().lower() for s in spec.split(",") if s.strip()]
        bad = [l for l in ls if l not in ALL]
        if bad:
            sys.exit("unknown layer(s): %s (use %s)" % (", ".join(bad), ",".join(ALL)))
        return ls

    colors = dict(COLORS)
    for s in a.set_color:
        k, _, v = s.partition("=")
        if k.lower() not in colors:
            sys.exit("--set-color: unknown layer " + k)
        colors[k.lower()] = v

    everything = unary_union([x for v in g.values() for x in v] + [x for v in mask.values() for x in v])
    fr = frame(a, cw, ch, outline, everything)
    px = a.dpi / 25.4
    extra = []
    if a.outline == "yes" and outline:
        extra.append(unary_union([ls.buffer(a.line / 2) for ls in C.snap_ends(outline)]))
    if a.marks:
        extra.append(marks(fr, a))
    extra = unary_union(extra) if extra else None

    def mirrored(layers):
        if a.mirror != "auto":
            return a.mirror == "yes"
        return any(l in TOP_SIDE for l in layers) and not any(l in BOTTOM_SIDE for l in layers)

    def rgb(c):
        try:
            return ImageColor.getrgb(c)[:3]
        except ValueError:
            sys.exit("not a colour: " + c)

    def finish(shape_img, layers, path):
        """1-bit shape image -> saved file (black-and-white or one colour)."""
        if a.color:
            fg, bg = rgb(colors[layers[0]]), rgb(a.bg)
        else:
            fg, bg = rgb(a.ink), rgb(a.paper)
        if a.negative:
            fg, bg = bg, fg
        if fg == (0, 0, 0) and bg == (255, 255, 255):
            img = shape_img.point(lambda v: 0 if v else 255, "1")      # true 1-bit file
        elif fg == (255, 255, 255) and bg == (0, 0, 0):
            img = shape_img.copy()
        else:
            img = Image.new("RGB", shape_img.size, bg)
            img.paste(fg, (0, 0), shape_img.convert("L").point(lambda v: 255 if v else 0))
        save(img, path, a.dpi)

    W = int(math.ceil((fr[2] - fr[0]) * px)); H = int(math.ceil((fr[3] - fr[1]) * px))
    print("%s: board %r, image %d x %d px = %.1f x %.1f mm at %d dpi"
          % (os.path.basename(a.board), L.board(data)["name"], W, H, fr[2] - fr[0], fr[3] - fr[1], a.dpi))

    if a.combine:
        ls = names(a.combine)
        ls.sort(key=ORDER.index)
        mir = mirrored(ls)
        path = "%s_%s.png" % (out, "+".join(ls))
        if a.color:
            bg = rgb(a.bg)
            img = Image.new("RGB", (W, H), rgb(a.ink) if a.negative else bg)
            for l in ls:
                m = rasterize(layer_geometry(l, g, mask, holes, outline, a), fr, px, mir)
                img.paste(rgb(colors[l]), (0, 0), m.convert("L").point(lambda v: 255 if v else 0))
            if extra is not None:
                m = rasterize(extra, fr, px, mir)
                img.paste(rgb(colors["o"]), (0, 0), m.convert("L").point(lambda v: 255 if v else 0))
            save(img, path, a.dpi)
        else:
            geo = unary_union([layer_geometry(l, g, mask, holes, outline, a) for l in ls]
                              + ([extra] if extra is not None else []))
            finish(rasterize(geo, fr, px, mir), ls, path)
        print("  %-28s -> %s%s" % (" + ".join(ls), path, "   (mirrored)" if mir else ""))
    else:
        ls = names(a.layers)
        if not ls:
            sys.exit("no layers to draw (the board is empty?)")
        for l in ls:
            geo = layer_geometry(l, g, mask, holes, outline, a)
            empty = geo.is_empty
            if extra is not None:
                geo = unary_union([geo, extra])
            mir = mirrored([l])
            path = "%s_%s.png" % (out, l)
            finish(rasterize(geo, fr, px, mir), [l], path)
            print("  %-28s -> %s%s%s" % (TITLE[l], path, "   (mirrored)" if mir else "",
                                         "   (layer is empty)" if empty else ""))
    for n in notes:
        print("  note: " + n)
    print("Print at 100 % / actual size (not 'fit to page').")


if __name__ == "__main__":
    main()
