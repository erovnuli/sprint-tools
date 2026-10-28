# lay6-tools - utilities for Sprint-Layout 6 files
# Copyright (C) 2026 Alex
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version. It is distributed
# WITHOUT ANY WARRANTY; see the LICENSE file for details.
"""kpcb2sprint.py - KiCad board (.kicad_pcb, KiCad 6 or newer) -> Sprint-Layout 6 board (.lay6).

    python kpcb2sprint.py board.kicad_pcb board.lay6 [--no-text] [--no-zones] [--margin 5]

Converts footprints (pads, silkscreen, reference text; each footprint becomes one group),
tracks (straight and arc), vias, filled copper zones, board outline (Edge.Cuts) and board-level
graphics/text on silkscreen and copper. Fab, courtyard and user layers are left out.
Needs lay6.py and kicad2sprint.py next to it (skel_font.py too, for text).
"""
import argparse, math, os, struct, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lay6 as L
import kicad2sprint as K

CU = {"F.Cu": L.C1, "B.Cu": L.C2, "In1.Cu": L.I1, "In2.Cu": L.I2}
SILK = {"F.SilkS": L.S1, "F.Silkscreen": L.S1, "B.SilkS": L.S2, "B.Silkscreen": L.S2}
EDGE = "Edge.Cuts"
ZONE_W = 0.05            # outline width for filled zones (KiCad fills are already final copper)


# ------------------------------------------------------------------ helpers
def kid_str(node, name):
    k = K.kid(node, name)
    return k[1] if k and len(k) > 1 else None


def hidden(node):
    if kid_str(node, "hide") == "yes" or "hide" in node:
        return True
    eff = K.kid(node, "effects")
    return bool(eff and (kid_str(eff, "hide") == "yes" or "hide" in eff))


def arc_points(s, m, e, max_step_deg=10):
    """Points along the circular arc through s, m, e (any coordinate system)."""
    c = K.circumcentre(s, m, e)
    if c is None:
        return [s, e]
    r = math.dist(s, c)
    a0 = math.atan2(s[1] - c[1], s[0] - c[0])
    am = math.atan2(m[1] - c[1], m[0] - c[0])
    a1 = math.atan2(e[1] - c[1], e[0] - c[0])
    ccw = (am - a0) % (2 * math.pi) < (a1 - a0) % (2 * math.pi)
    sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
    n = max(2, int(abs(math.degrees(sweep)) / max_step_deg) + 1)
    return [(c[0] + r * math.cos(a0 + sweep * i / n), c[1] + r * math.sin(a0 + sweep * i / n))
            for i in range(n + 1)]


class Board:
    """KiCad board coordinates (mm, Y down) -> Sprint UI (mm, Y up, 100 mm 'reference' height)."""

    def __init__(self, minx, miny, margin):
        self.minx, self.miny, self.margin = minx, miny, margin

    def T(self, x, y):
        return x - self.minx + self.margin, 100.0 - (y - self.miny + self.margin)


class FootprintConv(K.Converter):
    """kicad2sprint's footprint converter, placed at the footprint's board position/rotation."""

    def __init__(self, board, fx, fy, fang):
        super().__init__()
        self.board, self.fx, self.fy = board, fx, fy
        self.c, self.s = math.cos(math.radians(fang)), math.sin(math.radians(fang))

    def local_to_kicad(self, x, y):
        # KiCad: Y down, positive angle = counter-clockwise as seen on screen
        return self.fx + x * self.c + y * self.s, self.fy - x * self.s + y * self.c

    def P(self, x, y):
        return self.board.T(*self.local_to_kicad(x, y))

    def graphic(self, tag, g):
        if tag == "fp_rect":                                # rotated rectangles: 4 real corners
            lay = (K.layers_of(g) or [""])[0]
            layer = K.SILK_LAYERS.get(lay)
            if layer is None or (layer == L.S2 and not self.back_silk):
                return []
            (x0, y0), (x1, y1) = K.nums(g, "start", 2), K.nums(g, "end", 2)
            pts = [self.P(x0, y0), self.P(x1, y0), self.P(x1, y1), self.P(x0, y1)]
            return [L.line(pts + pts[:1], self.width(g), layer=layer)]
        return super().graphic(tag, g)


# ------------------------------------------------------------------ text
_font_ok = None


def text_records(s, x, y, size, angle, layer, mirror=False):
    """KiCad-style text centred at UI point (x, y), cap height `size`, rotated `angle` deg CCW."""
    global _font_ok
    if not s.strip():
        return []
    lines = s.split("\\n") if "\\n" in s else s.split("\n")
    h = size * 18 / 16                                   # Sprint height: cap height is 16/18 of it
    strokes, gap = [], size * 1.6
    for n, ln in enumerate(lines):                       # Sprint's own font when it has every glyph
        st = L.sprint_font_strokes(ln, h)
        if st is None:
            strokes = None
            break
        strokes += [[(x, y - n * gap) for x, y in q] for q in st]
    try:
        if strokes is None:
            import skel_font as S
            strokes = []
            for n, ln in enumerate(lines):
                st, _ = S.text_strokes(ln, 0, -n * gap, size * 9 / 8, ref="HEL")
                strokes += st
        _font_ok = True
    except Exception as e:
        if _font_ok is None:
            print("note: text skipped (%s)" % e)
        _font_ok = False
        return []
    if not strokes:
        return []
    xs = [p[0] for st in strokes for p in st]; ys = [p[1] for st in strokes for p in st]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    c, sn = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    out = []
    for st in strokes:
        pts = []
        for px, py in st:
            dx, dy = px - cx, py - cy
            if mirror:
                dx = -dx
            pts.append((x + dx * c - dy * sn, y + dx * sn + dy * c))
        out.append(pts)
    ax, ay = out[0][0]
    label = s.encode("ascii", "replace").decode()[:60]
    return [L.text(label, out, ax, ay, height=h, layer=layer, stroke_w=max(h / 9, 0.15))]


def text_of(node, conv_point, board_angle_extra=0.0):
    """(string, UI x, UI y, size, angle, layer, mirror) for fp_text / property / gr_text, or None."""
    lay = kid_str(node, "layer")
    layer = SILK.get(lay) or CU.get(lay)
    if layer is None or hidden(node):
        return None
    if node[0] == "property":
        if node[1] != "Reference":
            return None
        s = node[2]
    elif node[0] == "fp_text":
        if node[1] not in ("reference", "user"):
            return None
        s = node[2]
    else:
        s = node[1]
    at = K.nums(node, "at") or [0, 0]
    x, y = conv_point(at[0], at[1])
    ang = ((at[2] if len(at) > 2 else 0.0) + board_angle_extra) % 360
    if node[0] in ("property", "fp_text") and 90 < ang <= 270:
        ang -= 180                               # KiCad keeps footprint text readable ("keep upright")
    eff = K.kid(node, "effects")
    size = 1.0
    mirror = False
    if eff:
        font = K.kid(eff, "font")
        if font and K.nums(font, "size"):
            size = K.nums(font, "size")[0]
        just = K.kid(eff, "justify")
        mirror = bool(just and "mirror" in just)
    return s, x, y, size, ang, layer, mirror


# ------------------------------------------------------------------ converter
def convert(pcb, a):
    notes = defaultdict(int)
    # board extents: Edge.Cuts, else everything with coordinates
    pts = []
    for g in pcb:
        if isinstance(g, list) and g and g[0].startswith("gr_") and EDGE in K.layers_of(g):
            for k in ("start", "end", "mid", "center"):
                v = K.nums(g, k, 2) if K.kid(g, k) else None
                if v:
                    pts.append(v)
            if g[0] == "gr_circle":
                c, e = K.nums(g, "center", 2), K.nums(g, "end", 2)
                r = math.dist(c, e)
                pts += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
            for xy in K.kids(K.kid(g, "pts") or [], "xy"):
                pts.append((float(xy[1]), float(xy[2])))
    if not pts:
        notes["no Edge.Cuts outline - extents taken from footprints"] += 1
        for fp in K.kids(pcb, "footprint"):
            pts.append(tuple(K.nums(fp, "at", 2)))
    minx, miny = min(p[0] for p in pts), min(p[1] for p in pts)
    maxx, maxy = max(p[0] for p in pts), max(p[1] for p in pts)
    B = Board(minx, miny, a.margin)
    canvas_w = max(100.0, math.ceil(maxx - minx + 2 * a.margin))
    canvas_h = max(100.0, math.ceil(maxy - miny + 2 * a.margin))

    objs, gid = [], 0
    # ---- footprints
    for fp in K.kids(pcb, "footprint"):
        fx, fy, *fa = K.nums(fp, "at")
        fang = fa[0] if fa else 0.0
        conv = FootprintConv(B, fx, fy, fang)
        conv.back_silk = True
        recs = conv.convert(fp) or []
        for n in conv.notes:
            if not any(k in n for k in ("text/properties", "macro written", "nothing to convert", "non-90 rotation")):
                notes[n.split(":", 1)[-1].strip()] += 1
        if a.text:
            for t in K.kids(fp, "property") + K.kids(fp, "fp_text"):
                tx = text_of(t, conv.P)
                if tx:
                    recs += text_records(tx[0], *tx[1:6], mirror=tx[6])
        if recs:
            gid += 1
            objs += L.group(recs, gid)

    # ---- tracks (joined into polylines per net / layer / width)
    runs = defaultdict(list)
    for seg in K.kids(pcb, "segment"):
        lay = kid_str(seg, "layer")
        if lay not in CU:
            continue
        w = (K.nums(seg, "width") or [0.25])[0]
        net = kid_str(seg, "net")
        s, e = B.T(*K.nums(seg, "start", 2)), B.T(*K.nums(seg, "end", 2))
        key = (lay, round(w, 4), net)
        runs[key].append((tuple(round(v, 4) for v in s), tuple(round(v, 4) for v in e)))
    for arc_ in K.kids(pcb, "arc"):
        lay = kid_str(arc_, "layer")
        if lay not in CU:
            continue
        w = (K.nums(arc_, "width") or [0.25])[0]
        ps = arc_points(*[B.T(*K.nums(arc_, k, 2)) for k in ("start", "mid", "end")])
        ps = [tuple(round(v, 4) for v in p) for p in ps]
        runs[(lay, round(w, 4), kid_str(arc_, "net"))] += list(zip(ps, ps[1:]))
        notes["arc tracks drawn as short straight pieces"] += 1
    n_tracks = 0
    for (lay, w, net), segs in runs.items():
        for path in chains(segs):
            objs.append(L.track(path, w, layer=CU[lay])); n_tracks += 1
        if lay.startswith("In"):
            notes["inner-layer tracks on layers 5/6 (UNVERIFIED in the app)"] += 1

    # ---- vias
    n_vias = 0
    for v in K.kids(pcb, "via"):
        x, y = B.T(*K.nums(v, "at", 2))
        size = (K.nums(v, "size") or [0.8])[0]
        drill = (K.nums(v, "drill") or [0.4])[0]
        objs.append(L.tht_pad(x, y, size, drill, plated=True)); n_vias += 1
        if any(t in v for t in ("blind", "micro", "buried")):
            notes["blind/buried/micro vias drawn as through vias"] += 1

    # ---- zones
    n_zones = 0
    if a.zones:
        for z in K.kids(pcb, "zone"):
            if K.kid(z, "keepout"):
                notes["rule areas / keepouts skipped"] += 1; continue
            fills = K.kids(z, "filled_polygon")
            if not fills:
                notes["unfilled zone drawn from its outline (refill in KiCad first)"] += 1
                lays = K.layers_of(z)
                poly = K.kid(z, "polygon")
                fills = [["filled_polygon", ["layer", l], K.kid(poly, "pts")] for l in lays] if poly else []
            for f in fills:
                lay = kid_str(f, "layer") or (K.layers_of(z) or [""])[0]
                if lay not in CU:
                    continue
                ps = [B.T(float(xy[1]), float(xy[2])) for xy in K.kids(K.kid(f, "pts"), "xy")]
                if len(ps) >= 3:
                    objs.append(L.zone(ps, width=ZONE_W, layer=CU[lay])); n_zones += 1

    # ---- board graphics
    for g in pcb:
        if not (isinstance(g, list) and g and g[0].startswith("gr_")):
            continue
        tag, lay = g[0], (K.layers_of(g) or [""])[0]
        if tag == "gr_text":
            if a.text:
                tx = text_of(g, B.T)
                if tx:
                    objs += text_records(tx[0], *tx[1:6], mirror=tx[6])
            continue
        layer = L.OUTLINE if lay == EDGE else SILK.get(lay) or CU.get(lay)
        if layer is None:
            continue
        st = K.kid(g, "stroke")
        w = (K.nums(st, "width") if st else K.nums(g, "width")) or [0.1]
        w = w[0] if w[0] > 0 else 0.1
        filled = (kid_str(g, "fill") in ("yes", "solid")) or bool(K.kid(K.kid(g, "fill") or [], "type") and
                                                                 kid_str(K.kid(g, "fill"), "type") == "solid")
        if tag == "gr_line":
            objs.append(L.line([B.T(*K.nums(g, "start", 2)), B.T(*K.nums(g, "end", 2))], w, layer=layer))
        elif tag == "gr_rect":
            (x0, y0), (x1, y1) = K.nums(g, "start", 2), K.nums(g, "end", 2)
            ps = [B.T(x0, y0), B.T(x1, y0), B.T(x1, y1), B.T(x0, y1)]
            objs.append(L.zone(ps, width=w, layer=layer) if filled and layer in (L.C1, L.C2)
                        else L.line(ps + ps[:1], w, layer=layer))
        elif tag == "gr_circle":
            c, e = B.T(*K.nums(g, "center", 2)), B.T(*K.nums(g, "end", 2))
            objs.append(L.arc(c[0], c[1], math.dist(c, e), w, 0, 0, layer=layer))
        elif tag == "gr_arc":
            if K.kid(g, "mid"):
                ps = arc_points(*[B.T(*K.nums(g, k, 2)) for k in ("start", "mid", "end")], max_step_deg=5)
                objs.append(L.line(ps, w, layer=layer))
            else:
                notes["old-style gr_arc skipped"] += 1
        elif tag == "gr_poly":
            ps = [B.T(float(xy[1]), float(xy[2])) for xy in K.kids(K.kid(g, "pts"), "xy")]
            if len(ps) >= 2:
                objs.append(L.zone(ps, width=w, layer=layer) if filled and layer in (L.C1, L.C2)
                            else L.line(ps + ps[:1], w, layer=layer))
        elif tag == "gr_curve":
            p0, p1, p2, p3 = [B.T(float(xy[1]), float(xy[2])) for xy in K.kids(K.kid(g, "pts"), "xy")][:4]
            bz = [tuple(((1 - t) ** 3) * a_ + 3 * ((1 - t) ** 2) * t * b_ + 3 * (1 - t) * t * t * c_ + t ** 3 * d_
                        for a_, b_, c_, d_ in zip(p0, p1, p2, p3)) for t in [i / 16 for i in range(17)]]
            objs.append(L.line(bz, w, layer=layer))
    return objs, (canvas_w, canvas_h), notes, dict(footprints=gid, tracks=n_tracks, vias=n_vias, zones=n_zones)


def chains(segs):
    """Join touching segments into polylines (no branching)."""
    adj = defaultdict(list)
    for i, (p, q) in enumerate(segs):
        adj[p].append(i); adj[q].append(i)
    used, out = set(), []
    starts = [p for p in adj if len(adj[p]) != 2] + list(adj)
    for s in starts:
        for i in adj[s]:
            if i in used:
                continue
            path, cur = [s], s
            while True:
                nxt = [j for j in adj[cur] if j not in used]
                if not nxt:
                    break
                j = nxt[0]; used.add(j)
                p, q = segs[j]
                cur = q if p == cur else p
                path.append(cur)
                if len(adj[cur]) != 2:
                    break
            out.append(path)
    return out


def write(path, objs, canvas):
    head = bytearray(L.HEAD)
    w, h = canvas
    struct.pack_into("<I", head, 0x2B, int(round(w * 10000)))
    struct.pack_into("<I", head, 0x2F, int(round(h * 10000)))
    recs = []
    for o in objs:
        recs += o if isinstance(o, list) else [o]
    n_pads = sum(1 for r in recs if r[0] in (2, 8))
    data = bytes(head) + struct.pack("<I", len(recs)) + b"".join(recs) + bytes(4 + 4 * n_pads) + L.TAIL
    with open(path, "wb") as f:
        f.write(data)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kpcb2sprint.py",
                                 description="KiCad board (.kicad_pcb, KiCad 6+) -> Sprint-Layout 6 board (.lay6).")
    ap.add_argument("input"); ap.add_argument("output")
    ap.add_argument("--no-text", dest="text", action="store_false", help="leave out references and board text")
    ap.add_argument("--no-zones", dest="zones", action="store_false", help="leave out copper zones")
    ap.add_argument("--margin", type=float, default=5.0, help="space around the board outline in mm (default 5)")
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        ap.print_help(); return 0
    a = ap.parse_args(argv)
    pcb = K.sexp_parse(open(a.input, encoding="utf-8").read())
    if not pcb or pcb[0] != "kicad_pcb":
        print("not a KiCad board file"); return 1
    objs, canvas, notes, n = convert(pcb, a)
    write(a.output, objs, canvas)
    print("%(footprints)d footprints, %(tracks)d tracks, %(vias)d vias, %(zones)d zones" % n)
    print("canvas %.0f x %.0f mm%s" % (canvas[0], canvas[1],
          "" if canvas == (100.0, 100.0) else "  (canvas sizes other than 100 x 100 mm are UNVERIFIED in the app)"))
    for k, v in sorted(notes.items()):
        print("  - %s%s" % (k, "" if v == 1 else " (%d)" % v))
    print("-> " + a.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
