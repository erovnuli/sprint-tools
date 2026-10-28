"""lay6_vias.py - find vias that do not join the two copper layers.

    python lay6_vias.py board.lay6 [--via 0.4/0.7] [--tol 0.01] [--mark out.lay6]
                        [--ring 1.0] [--ring-layer 4]

A via (plated through-hole pad of the given drill/outer size) is useful only when copper on the
TOP and on the BOTTOM layer reaches it. It is reported when copper touches it on one layer only
("single-layer via") or on no layer at all ("floating via"). Copper counted: tracks, copper
arcs/circles, SMD pads, copper zones (not cutout zones) and other through-hole pads.
--mark writes a COPY of the board with a ring (default 1.0 mm diameter, layer 4 = S2 bottom
silkscreen) around every single-layer via; --mark-floating rings the floating ones too.
"""
import argparse, math, os, struct, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lay6 as L
from lay6_padcheck import Shape

TOP, BOT = 1, 3


def pts_of(d, o):
    k = struct.unpack_from("<I", d, o + 0x55 + struct.unpack_from("<I", d, o + 0x51)[0])[0]
    p = o + 0x59 + struct.unpack_from("<I", d, o + 0x51)[0] + 4 * k
    m = struct.unpack_from("<I", d, p)[0]
    c = struct.unpack_from("<%df" % (2 * m), d, p + 4)
    return [(c[2 * i] / 1e4, c[2 * i + 1] / 1e4) for i in range(m)]


def arc_pts(cx, cy, r, start, end):
    span = (end - start) % 360 or 360
    n = max(2, int(span / 5) + 1)
    return [(cx + r * math.cos(math.radians(start + span * i / n)),
             cy + r * math.sin(math.radians(start + span * i / n))) for i in range(n + 1)]


def read(d):
    """-> vias-candidates list (index, x, y, drill, outer, plated), copper [(layers, Shape, owner)]"""
    pads, copper = [], []
    for n, (o, t, ln) in enumerate(L.walk(d)):
        lay = d[o + 0x16]
        if t == 2:
            x, y, R, D = struct.unpack_from("<4f", d, o + 1)
            x, y, R, D = x / 1e4, y / 1e4, R / 1e4, D / 1e4
            shape = d[o + 0x17]
            if lay not in (TOP, BOT, 5, 6):          # a "pad" drawn on silkscreen etc. is not copper
                continue
            pts = pts_of(d, o)
            sh = Shape("poly", pts, 0.0) if shape in (2, 3) else \
                Shape("seg", [pts[0], pts[-1]] if shape == 4 else [(x, y)], R)
            pads.append(dict(n=n, o=o, x=x, y=y, drill=2 * D, outer=2 * R, plated=d[o + 0x39] != 0, shape=sh))
            copper.append(((TOP, BOT), sh, ("pad", len(pads) - 1)))
        elif lay not in (TOP, BOT):
            continue
        elif t == 8:
            copper.append(((lay,), Shape("poly", pts_of(d, o), 0.0), ("smd", n)))
        elif t == 6:
            w = struct.unpack_from("<I", d, o + 0x11)[0] / 1e4
            P = pts_of(d, o)
            for a, b in zip(P, P[1:]):
                copper.append(((lay,), Shape("seg", [a, b], w / 2), ("track", n)))
        elif t == 4:
            if d[o + 0x34]:                          # cutout area: removes copper, not copper
                continue
            w = struct.unpack_from("<I", d, o + 0x11)[0] / 1e4
            copper.append(((lay,), Shape("poly", pts_of(d, o), w / 2), ("zone", n)))
        elif t == 5:
            x, y, Ro, Ri = struct.unpack_from("<4f", d, o + 1)
            end = struct.unpack_from("<I", d, o + 0x11)[0] / 1000.0
            start = struct.unpack_from("<I", d, o + 0x1F)[0] / 1000.0
            r, w = (Ro + Ri) / 2e4, (Ro - Ri) / 1e4
            P = arc_pts(x / 1e4, y / 1e4, r, start, end)
            for a, b in zip(P, P[1:]):
                copper.append(((lay,), Shape("seg", [a, b], w / 2), ("arc", n)))
    return pads, copper


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lay6_vias.py", description="Find vias that touch copper on only one layer.")
    ap.add_argument("board")
    ap.add_argument("--via", default="0.4/0.7", help="via size DRILL/OUTER in mm (default 0.4/0.7)")
    ap.add_argument("--tol", type=float, default=0.01, help="size tolerance in mm")
    ap.add_argument("--mark", metavar="OUT.lay6", help="write a copy with rings around the problem vias")
    ap.add_argument("--mark-floating", action="store_true", help="also ring vias touching no copper at all")
    ap.add_argument("--ring", type=float, default=1.0, help="ring diameter in mm (default 1.0)")
    ap.add_argument("--ring-layer", type=int, default=4, help="ring layer: 2 = S1, 4 = S2 (default 4)")
    ap.add_argument("--board", dest="board_no", type=int, metavar="N",
                    help="board number in files with several boards (default: the first one with objects)")
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        ap.print_help(); return 0
    a = ap.parse_args(argv)
    L.SELECT = a.board_no
    d = open(a.board, "rb").read()
    if d[:4] != b"\x06\x33\xaa\xff":
        print("not a Sprint-Layout 6 file"); return 1
    canvas_h = L.canvas(d)[1]
    drill, outer = (float(v) for v in a.via.split("/"))
    pads, copper = read(d)
    vias = [i for i, p in enumerate(pads) if abs(p["drill"] - drill) <= a.tol and abs(p["outer"] - outer) <= a.tol]

    # spatial grid of copper for speed
    cell = 3.0
    grid = defaultdict(list)
    for k, (lays, sh, own) in enumerate(copper):
        x0, y0, x1, y1 = sh.box
        for gx in range(int(x0 // cell), int(x1 // cell) + 1):
            for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                grid[(gx, gy)].append(k)

    single, floating, both = [], [], 0
    for vi in vias:
        v = pads[vi]; sh = v["shape"]
        touched = defaultdict(set)
        x0, y0, x1, y1 = sh.box
        cand = set()
        for gx in range(int(x0 // cell), int(x1 // cell) + 1):
            for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                cand.update(grid[(gx, gy)])
        for k in cand:
            lays, csh, own = copper[k]
            if own == ("pad", vi):
                continue
            B = csh.box
            if B[0] > sh.box[2] or sh.box[0] > B[2] or B[1] > sh.box[3] or sh.box[1] > B[3]:
                continue
            if sh.dist(csh) <= 1e-6:
                for l in lays:
                    touched[l].add(own[0])
        if TOP in touched and BOT in touched:
            both += 1
        elif TOP in touched or BOT in touched:
            single.append((vi, TOP if TOP in touched else BOT, sorted(touched[TOP] | touched[BOT])))
        else:
            floating.append(vi)

    name = {TOP: "top (C1)", BOT: "bottom (C2)"}
    print("%d vias of %.2f/%.2f mm checked: %d join both layers, %d touch one layer only, %d touch nothing"
          % (len(vias), drill, outer, both, len(single), len(floating)))
    ui = lambda p: (p["x"], p["y"] + canvas_h)
    for vi, lay, what in sorted(single, key=lambda s: (-ui(pads[s[0]])[1], ui(pads[s[0]])[0])):
        x, y = ui(pads[vi])
        print("  ONE LAYER  X %8.3f  Y %8.3f   only %-12s (%s)" % (x, y, name[lay], ", ".join(what)))
    for vi in floating:
        x, y = ui(pads[vi])
        print("  FLOATING   X %8.3f  Y %8.3f   no copper touches it" % (x, y))

    marks = [ui(pads[vi]) for vi, _, _ in single] + ([ui(pads[vi]) for vi in floating] if a.mark_floating else [])
    if a.mark and marks:
        w = 0.15
        rings = L.group([L.arc(x, y, a.ring / 2, w, 0, 0, canvas_h=canvas_h, layer=a.ring_layer) for x, y in marks], 9997)
        co = L.count_ofs(d)
        n = struct.unpack_from("<I", d, co)[0]
        end = co + 4
        for o, t, ln in L.walk(d):
            end = o + ln
        out = (d[:co] + struct.pack("<I", n + len(rings)) + d[co + 4:end] + b"".join(rings) + d[end:])
        open(a.mark, "wb").write(out)
        print("%d rings (%.1f mm, layer %d) -> %s" % (len(rings), a.ring, a.ring_layer, a.mark))
    return 0


if __name__ == "__main__":
    sys.exit(main())
