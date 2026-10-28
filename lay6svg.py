# lay6-tools - utilities for Sprint-Layout 6 files
# Copyright (C) 2026 Alex
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version. It is distributed
# WITHOUT ANY WARRANTY; see the LICENSE file for details.
"""lay6svg.py - Sprint-Layout 6 board (.lay6) -> SVG at exact 1:1 scale in millimetres.

    python lay6svg.py board.lay6 board.svg [--layers c1,c2,s1,s2,o,drill] [--holes holes.csv]
                      [--no-fill] [--frame]

The SVG is sized in mm (width="..mm", viewBox in mm), so a drawing program imports it at true
size: 1 SVG unit = 1 mm, coordinates written to 0.1 um, straight from the board file - no
printer driver or PDF rounding in between. Every layer is its own group (named after the
Sprint layer). By default these are plain groups (CorelDRAW imports them unlocked, all on one
layer; ungroup or select a group to hide/delete it); --as-layers makes them program layers.
Drill holes go in their own "Drills" group as exact circles; --holes also writes a CSV list of
every hole (x, y, diameter, plated) in the same coordinates as the SVG (origin = top-left
corner of the Sprint canvas, Y downwards; add --yup for Y upwards from the bottom edge).
"""
import argparse, math, os, struct, sys
from collections import defaultdict



SELECT = None    # board in multi-board files: None = first board with objects, or 1, 2, ...


def record_length(data, o):
    t = data[o]
    if t == 7:
        L = struct.unpack_from("<I", data, o + 0x4D)[0]
        p = o + 0x51 + L
        p += 4 + struct.unpack_from("<I", data, p)[0]   # element name string (usually empty)
        p += 4 + 4 * struct.unpack_from("<I", data, p)[0]
        m = struct.unpack_from("<I", data, p)[0]
        p += 4
        for _ in range(m):
            p += 0x51 + 8 * struct.unpack_from("<I", data, p + 0x4D)[0]
        if data[o + 0x17] == 1:          # a component's ID text: component properties follow
            p += 4 + 4 + 1 + 8
            for _ in range(2):
                p += 4 + struct.unpack_from("<I", data, p)[0]
            p += 1
        return p - o
    s = struct.unpack_from("<I", data, o + 0x51)[0]
    k = struct.unpack_from("<I", data, o + 0x55 + s)[0]
    p = o + 0x59 + s + 4 * k
    if t == 5:
        return p - o
    return p + 4 + 8 * struct.unpack_from("<I", data, p)[0] - o


def board(data):
    """(object start, count, canvas w, canvas h) of the board to export. A file holds u32 number of
    boards at 4, then per board a 0x212-byte header, u32 object count, the objects and one u32 per pad."""
    nb = struct.unpack_from("<I", data, 4)[0]
    o, found, all_ = 8, None, []
    for i in range(nb):
        n = struct.unpack_from("<I", data, o + 0x212)[0]
        b = (o + 0x216, n, struct.unpack_from("<I", data, o + 0x23)[0] / 1e4, struct.unpack_from("<I", data, o + 0x27)[0] / 1e4)
        all_.append(b)
        p, pads = b[0], 0
        for _ in range(n):
            if data[p] in (2, 8):
                pads += 1
            p += record_length(data, p)
        o = p + 4 * pads              # this board's pad list (one u32 per pad) follows its objects
    if SELECT:
        if not 1 <= SELECT <= nb:
            raise SystemExit("this file has %d board(s); --board %d does not exist" % (nb, SELECT))
        return all_[SELECT - 1]
    return next((b for b in all_ if b[1]), all_[0])


def walk(data):
    """Yield (offset, type, length) for the objects of the selected board."""
    o, n = board(data)[:2]
    for _ in range(n):
        ln = record_length(data, o)
        yield o, data[o], ln
        o += ln


class L:                 # stand-in so the file needs nothing else next to it
    walk = staticmethod(walk)

LAYERS = {  # layer byte: (key, group name, colour)
    1: ("c1", "C1 copper top", "#c83737"),
    3: ("c2", "C2 copper bottom", "#3771c8"),
    5: ("i1", "I1 inner copper 1", "#c87137"),
    6: ("i2", "I2 inner copper 2", "#37c871"),
    2: ("s1", "S1 silkscreen top", "#d4aa00"),
    4: ("s2", "S2 silkscreen bottom", "#a05a2c"),
    7: ("o", "O outline", "#000000"),
}
ORDER = [3, 6, 5, 1, 4, 2, 7]          # drawn bottom to top
f = lambda v: ("%.4f" % v).rstrip("0").rstrip(".") if abs(v) > 5e-5 else "0"


class Board:
    def __init__(self, data):
        self.data = data
        self.cw, self.ch = board(data)[2:4]

    def P(self, x, y):
        """stored units -> SVG mm (origin top-left of the canvas, Y down)."""
        return x / 1e4, -y / 1e4


def points(d, o):
    k = struct.unpack_from("<I", d, o + 0x55 + struct.unpack_from("<I", d, o + 0x51)[0])[0]
    p = o + 0x59 + struct.unpack_from("<I", d, o + 0x51)[0] + 4 * k
    m = struct.unpack_from("<I", d, p)[0]
    c = struct.unpack_from("<%df" % (2 * m), d, p + 4)
    return [(c[2 * i], c[2 * i + 1]) for i in range(m)]


def text_strokes(d, o):
    Ln = struct.unpack_from("<I", d, o + 0x4D)[0]
    p = o + 0x51 + Ln
    p += 4 + struct.unpack_from("<I", d, p)[0]   # element name string (usually empty)
    p += 4 + 4 * struct.unpack_from("<I", d, p)[0]
    n = struct.unpack_from("<I", d, p)[0]; p += 4
    out = []
    for _ in range(n):
        m = struct.unpack_from("<I", d, p + 0x4D)[0]
        c = struct.unpack_from("<%df" % (2 * m), d, p + 0x51)
        out.append([(c[2 * i], c[2 * i + 1]) for i in range(m)])
        p += 0x51 + 8 * m
    return out


def path_d(pts, close=False):
    s = "M" + " L".join("%s %s" % (f(x), f(y)) for x, y in pts)
    return s + (" Z" if close else "")


def convert(data, keep, fill=True):
    B = Board(data)
    g = defaultdict(list)            # layer byte -> svg elements
    drills, holes = [], []
    counts = defaultdict(int)
    for o, t, ln in L.walk(data):
        lay = data[o + 0x16]
        if t == 8:                                   # SMD pad
            pts = [B.P(*p) for p in points(data, o)]
            g[lay].append('<path d="%s"/>' % path_d(pts, True)); counts["SMD pads"] += 1
        elif t == 2:                                 # through-hole pad / via
            x, y, R, D = struct.unpack_from("<4f", data, o + 1)
            cx, cy = B.P(x, y); R, D = R / 1e4, D / 1e4
            shape = data[o + 0x17]
            plated = data[o + 0x39] != 0
            pts = [B.P(*p) for p in points(data, o)]
            if shape in (2, 3):                      # octagon / square: the stored outline
                el = '<path d="%s"/>' % path_d(pts, True)
            elif shape == 4:                         # oblong: segment swept by the radius
                el = ('<path d="%s" fill="none" stroke-width="%s" stroke-linecap="round"/>'
                      % (path_d([pts[0], pts[-1]]), f(2 * R)))
            else:
                el = '<circle cx="%s" cy="%s" r="%s"/>' % (f(cx), f(cy), f(R))
            for l in (1, 3):                         # a through pad shows on both outer copper layers
                g[l].append(el)
            if D > 0:
                drills.append('<circle cx="%s" cy="%s" r="%s"/>' % (f(cx), f(cy), f(D)))
                holes.append((cx, cy, 2 * D, plated))
            counts["through-hole pads / vias"] += 1
        elif t == 6:                                 # line / track
            w = struct.unpack_from("<I", data, o + 0x11)[0] / 1e4
            pts = [B.P(*p) for p in points(data, o)]
            g[lay].append('<path d="%s" fill="none" stroke-width="%s" stroke-linecap="round" '
                          'stroke-linejoin="round"/>' % (path_d(pts), f(w)))
            counts["tracks / lines"] += 1
        elif t == 4:                                 # zone
            w = struct.unpack_from("<I", data, o + 0x11)[0] / 1e4
            pts = [B.P(*p) for p in points(data, o)]
            cut = data[o + 0x34] != 0
            if cut or not fill:
                g[lay].append('<path d="%s" fill="none" stroke-width="%s" stroke-dasharray="%s"/>'
                              % (path_d(pts, True), f(max(w, 0.05)), "1 0.5" if cut else "none"))
            else:
                g[lay].append('<path d="%s" stroke-width="%s" stroke-linejoin="round"/>'
                              % (path_d(pts, True), f(w)))
            counts["zones"] += 1
            if data[o + 0x32]:
                counts["hatched zones drawn solid"] += 1
        elif t == 5:                                 # arc / circle
            x, y, Ro, Ri = struct.unpack_from("<4f", data, o + 1)
            cx, cy = B.P(x, y)
            r, w = (Ro + Ri) / 2e4, (Ro - Ri) / 1e4
            end = struct.unpack_from("<I", data, o + 0x11)[0] / 1000.0
            start = struct.unpack_from("<I", data, o + 0x1F)[0] / 1000.0
            if abs((end - start) % 360) < 1e-6:
                el = '<circle cx="%s" cy="%s" r="%s" fill="none" stroke-width="%s"/>' % (f(cx), f(cy), f(r), f(w))
            else:
                span = (end - start) % 360
                a0, a1 = math.radians(start), math.radians(end)
                p0 = (cx + r * math.cos(a0), cy - r * math.sin(a0))
                p1 = (cx + r * math.cos(a1), cy - r * math.sin(a1))
                el = ('<path d="M%s %s A%s %s 0 %d 0 %s %s" fill="none" stroke-width="%s" stroke-linecap="round"/>'
                      % (f(p0[0]), f(p0[1]), f(r), f(r), 1 if span > 180 else 0, f(p1[0]), f(p1[1]), f(w)))
            g[lay].append(el); counts["arcs / circles"] += 1
        elif t == 7:                                 # text: its stored strokes
            h = struct.unpack_from("<f", data, o + 0x09)[0] / 1e4
            for st in text_strokes(data, o):
                pts = [B.P(*p) for p in st]
                g[lay].append('<path d="%s" fill="none" stroke-width="%s" stroke-linecap="round" '
                              'stroke-linejoin="round"/>' % (path_d(pts), f(max(h / 8, 0.1))))
            counts["texts"] += 1
    return B, g, drills, holes, counts


def write_svg(path, B, g, drills, keep, frame, as_layers=False):
    """Every Sprint layer is a plain <g> group. With as_layers=True the groups are also tagged as
    Inkscape layers - some programs (CorelDRAW) import those as separate, locked layers."""
    lay_attr = lambda label: (' inkscape:groupmode="layer" inkscape:label="%s"' % label) if as_layers else ""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           'width="%smm" height="%smm" viewBox="0 0 %s %s">' % (f(B.cw), f(B.ch), f(B.cw), f(B.ch)),
           '<!-- Sprint-Layout 6 board, 1 unit = 1 mm, origin = top-left of the Sprint canvas -->']
    if frame:
        out.append('<g id="Frame"%s>'
                   '<rect x="0" y="0" width="%s" height="%s" fill="none" stroke="#999" stroke-width="0.05"/></g>'
                   % (lay_attr("Frame"), f(B.cw), f(B.ch)))
    for lay in ORDER:
        key, name, col = LAYERS[lay]
        if key not in keep or not g.get(lay):
            continue
        out.append('<g id="%s"%s fill="%s" stroke="%s" stroke-width="0">'
                   % (name.split()[0], lay_attr(name), col, col))
        out += g[lay]
        out.append('</g>')
    if "drill" in keep and drills:
        out.append('<g id="Drills"%s fill="#ffffff" stroke="#000000" stroke-width="0.02">' % lay_attr("Drills"))
        out += drills
        out.append('</g>')
    out.append('</svg>')
    open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lay6svg.py", description="Sprint-Layout 6 board -> 1:1 SVG in millimetres.")
    ap.add_argument("board"); ap.add_argument("svg")
    ap.add_argument("--layers", default="c1,c2,i1,i2,s1,s2,o,drill",
                    help="layers to include (default all): c1 c2 i1 i2 s1 s2 o drill")
    ap.add_argument("--holes", metavar="CSV", help="also write every hole as x;y;diameter;plated (mm)")
    ap.add_argument("--yup", action="store_true", help="CSV: Y measured upwards from the bottom edge")
    ap.add_argument("--no-fill", dest="fill", action="store_false", help="draw zones as outlines only")
    ap.add_argument("--as-layers", action="store_true",
                    help="mark each Sprint layer as a drawing-program layer (old behaviour; Corel may lock them)")
    ap.add_argument("--frame", action="store_true", help="add the canvas border as a size reference")
    ap.add_argument("--board", dest="board_no", type=int, metavar="N",
                    help="board number in files with several boards (default: the first one with objects)")
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        ap.print_help(); return 0
    a = ap.parse_args(argv)
    global SELECT
    SELECT = a.board_no
    data = open(a.board, "rb").read()
    if data[:4] != b"\x06\x33\xaa\xff":
        print("not a Sprint-Layout 6 file"); return 1
    keep = set(k.strip().lower() for k in a.layers.split(","))
    B, g, drills, holes, counts = convert(data, keep, a.fill)
    write_svg(a.svg, B, g, drills, keep, a.frame, a.as_layers)
    print("canvas %s x %s mm -> %s" % (f(B.cw), f(B.ch), a.svg))
    for k, v in counts.items():
        print("  %-28s %d" % (k, v))
    if a.holes:
        with open(a.holes, "w") as fh:
            fh.write("x_mm;y_mm;diameter_mm;plated\n")
            for x, y, dmm, pl in sorted(holes, key=lambda h: (round(h[1], 3), h[0])):
                fh.write("%.4f;%.4f;%.4f;%s\n" % (x, B.ch - y if a.yup else y, dmm, "yes" if pl else "no"))
        print("  %d holes -> %s" % (len(holes), a.holes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
