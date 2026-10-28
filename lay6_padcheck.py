"""lay6_padcheck.py - check how pads are connected through copper on a Sprint-Layout 6 board.

    python lay6_padcheck.py board.lay6 --from tht:1.0/2.0 --to tht:0.5/0.8 [--min 1] [--max 1]
                            [--tol 0.01] [--mark marked.lay6] [--list]

Rule: every pad matching --from must be connected THROUGH COPPER to at least --min and at most
--max pads matching --to (default: at least 1, no upper limit). "Through copper" means the real,
manufactured copper: tracks, copper zones and pads that touch or overlap on the same layer, and
plated through-hole pads / vias that join the layers. An unplated through-hole pad does not join
the layers. Copper circles/arcs and text on copper layers are not counted.

Pad patterns (sizes in mm, matched within --tol):
    tht:DRILL/OUTER       through-hole pad, e.g. tht:1.0/2.0   (via = small tht pad)
    tht:DRILL/*           any outer size;  tht:*/2.0  any drill
    smd:W x H             SMD pad, e.g. smd:1.5x1.0 (either orientation), smd:*  any SMD pad
--mark writes a COPY of the board with a ring on the top silkscreen around every failing pad
(all rings in one group, so one click selects them for deleting). The original is never changed.
"""
import argparse, math, os, struct, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lay6 as L

COPPER = (1, 3, 5, 6)
LNAME = {1: "top", 3: "bottom", 5: "inner 1", 6: "inner 2"}


# ------------------------------------------------------------------ geometry
def seg_seg(a, b, c, d):
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2, d3, d4 = cross(c, d, a), cross(c, d, b), cross(a, b, c), cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return 0.0
    return min(pt_seg(a, c, d), pt_seg(b, c, d), pt_seg(c, a, b), pt_seg(d, a, b))


def pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def in_poly(p, poly):
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


class Shape:
    """Copper on one layer: a core (point, segment or polygon) grown by radius r."""

    def __init__(self, kind, pts, r):
        self.kind, self.pts, self.r = kind, pts, r          # kind: "seg" (1 or 2 points) or "poly"
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        self.box = (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)

    def edges(self):
        P = self.pts
        if self.kind == "poly":
            return list(zip(P, P[1:] + P[:1]))
        return [(P[0], P[-1])]

    def dist(self, o):
        """Distance between the two pieces of copper (<= 0 means they touch)."""
        if self.kind == "poly" and any(in_poly(p, self.pts) for p in o.pts):
            return -o.r
        if o.kind == "poly" and any(in_poly(p, o.pts) for p in self.pts):
            return -self.r
        d = min(seg_seg(a, b, c, e) for a, b in self.edges() for c, e in o.edges())
        return d - self.r - o.r


# ------------------------------------------------------------------ reading the board
def read(data):
    canvas_h = L.canvas(data)[1]
    pads, copper, zones = [], [], []   # copper: (layer, Shape, pad index / ("zone", n) / None)
    for o, t, ln in L.walk(data):
        lay = data[o + 0x16]
        if t == 7:
            continue
        k = struct.unpack_from("<I", data, o + 0x55 + struct.unpack_from("<I", data, o + 0x51)[0])[0]
        p = o + 0x59 + struct.unpack_from("<I", data, o + 0x51)[0] + 4 * k
        if t == 5:
            continue
        m = struct.unpack_from("<I", data, p)[0]
        c = struct.unpack_from("<%df" % (2 * m), data, p + 4)
        pts = [(c[2 * i] / 1e4, c[2 * i + 1] / 1e4 + canvas_h) for i in range(m)]
        if t == 8 and lay in COPPER:
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            w = math.dist(pts[0], pts[1]); h = math.dist(pts[1], pts[2])
            idx = len(pads)
            pads.append(dict(kind="smd", size=(w, h), at=((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2),
                             layers=[lay]))
            copper.append((lay, Shape("poly", pts, 0.0), idx))
        elif t == 2:
            x, y, R, D = struct.unpack_from("<4f", data, o + 1)
            R, D = R / 1e4, D / 1e4
            at = (x / 1e4, y / 1e4 + canvas_h)
            shape = data[o + 0x17]
            plated = data[o + 0x39] != 0
            idx = len(pads)
            pads.append(dict(kind="tht", drill=2 * D, outer=2 * R, at=at, plated=plated, layers=list(COPPER)))
            sh = Shape("poly", pts, 0.0) if shape in (2, 3) else Shape("seg", [pts[0], pts[-1]] if shape == 4 else [at], R)
            for l in COPPER:
                copper.append((l, sh, idx))
        elif t == 6 and lay in COPPER:
            w = struct.unpack_from("<I", data, o + 0x11)[0] / 1e4
            for a, b in zip(pts, pts[1:]):
                copper.append((lay, Shape("seg", [a, b], w / 2), None))
        elif t == 4 and lay in COPPER and len(pts) >= 3:
            w = struct.unpack_from("<I", data, o + 0x11)[0] / 1e4
            zones.append((lay, pts, len(copper)))      # edges become copper pieces; inside is tested apart
            for a, b in zip(pts, pts[1:] + pts[:1]):
                copper.append((lay, Shape("seg", [a, b], w / 2), ("zone", len(zones) - 1)))
    return canvas_h, pads, copper, zones


def connectivity(pads, copper, zones, cell=4.0):
    """Union-find over copper pieces; returns the copper group of every pad."""
    n = len(copper)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i

    def join(i, j):
        a, b = root(i), root(j)
        if a != b:
            parent[a] = b
    # the edges of one zone are one piece of copper
    zfirst = {}
    for i, (_, _, pi) in enumerate(copper):
        if isinstance(pi, tuple):
            if pi[1] in zfirst:
                join(i, zfirst[pi[1]])
            else:
                zfirst[pi[1]] = i
    # anything lying inside a zone touches it
    for zi, (zl, zpts, _) in enumerate(zones):
        xs = [p[0] for p in zpts]; ys = [p[1] for p in zpts]
        bx = (min(xs), min(ys), max(xs), max(ys))
        for i, (lay, sh, pi) in enumerate(copper):
            if lay != zl or isinstance(pi, tuple):
                continue
            q = sh.pts[0]
            if bx[0] <= q[0] <= bx[2] and bx[1] <= q[1] <= bx[3] and in_poly(q, zpts):
                join(i, zfirst[zi])
    # plated through-hole pads join their copies on every layer
    first = {}
    for i, (_, _, pi) in enumerate(copper):
        if isinstance(pi, int) and pads[pi]["kind"] == "tht" and pads[pi]["plated"]:
            if pi in first:
                join(i, first[pi])
            else:
                first[pi] = i
    # touching copper on the same layer (spatial grid keeps it fast)
    grid = defaultdict(list)
    for i, (lay, sh, _) in enumerate(copper):
        x0, y0, x1, y1 = sh.box
        for gx in range(int(x0 // cell), int(x1 // cell) + 1):
            for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                grid[(lay, gx, gy)].append(i)
    done = set()
    for bucket in grid.values():
        for a in range(len(bucket)):
            for b in range(a + 1, len(bucket)):
                i, j = bucket[a], bucket[b]
                if (i, j) in done or root(i) == root(j):
                    continue
                done.add((i, j))
                A, B = copper[i][1].box, copper[j][1].box
                if A[0] > B[2] or B[0] > A[2] or A[1] > B[3] or B[1] > A[3]:
                    continue
                if copper[i][1].dist(copper[j][1]) <= 1e-6:
                    join(i, j)
    groups = defaultdict(set)
    for i, (_, _, pi) in enumerate(copper):
        if isinstance(pi, int):
            groups[pi].add(root(i))
    return groups               # pad index -> set of copper groups (1 for plated, per layer for unplated)


# ------------------------------------------------------------------ pad patterns
def parse_pattern(s):
    kind, _, size = s.partition(":")
    kind = kind.strip().lower()
    if kind not in ("tht", "smd"):
        raise ValueError("pattern must start with tht: or smd:  (%s)" % s)
    num = lambda v: None if v.strip() in ("*", "") else float(v)
    if kind == "tht":
        d, _, o = size.partition("/")
        return ("tht", num(d), num(o or "*"))
    if size.strip() in ("*", ""):
        return ("smd", None, None)
    w, _, h = size.lower().partition("x")
    return ("smd", num(w), num(h or "*"))


def matches(pad, pat, tol):
    ok = lambda v, want: want is None or abs(v - want) <= tol
    if pat[0] != pad["kind"]:
        return False
    if pat[0] == "tht":
        return ok(pad["drill"], pat[1]) and ok(pad["outer"], pat[2])
    w, h = pad["size"]
    return (ok(w, pat[1]) and ok(h, pat[2])) or (ok(h, pat[1]) and ok(w, pat[2]))


def describe(pad):
    if pad["kind"] == "tht":
        return "through-hole %.2f/%.2f%s" % (pad["drill"], pad["outer"], "" if pad["plated"] else " (unplated)")
    return "SMD %.2fx%.2f %s" % (pad["size"][0], pad["size"][1], LNAME[pad["layers"][0]])


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="lay6_padcheck.py", description="Check that pads of one kind are connected "
                                 "through copper to pads of another kind.")
    ap.add_argument("board")
    ap.add_argument("--from", dest="src", required=False, help="pads to check, e.g. tht:1.0/2.0")
    ap.add_argument("--to", dest="dst", required=False, help="pads they must reach, e.g. tht:0.5/0.8")
    ap.add_argument("--min", type=int, default=1, help="at least this many --to pads (default 1)")
    ap.add_argument("--max", type=int, default=None, help="at most this many --to pads (e.g. 1 = exactly one)")
    ap.add_argument("--shared", action="store_true",
                    help="also report --to pads that serve more than one --from pad")
    ap.add_argument("--tol", type=float, default=0.01, help="size tolerance in mm (default 0.01)")
    ap.add_argument("--mark", metavar="OUT.lay6", help="write a copy with failing pads ringed")
    ap.add_argument("--list", action="store_true", help="list the pad sizes found on the board and exit")
    ap.add_argument("--board", dest="board_no", type=int, metavar="N",
                    help="board number in files with several boards (default: the first one with objects)")
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        ap.print_help(); return 0
    a = ap.parse_args(argv)
    L.SELECT = a.board_no
    data = open(a.board, "rb").read()
    if data[:4] != b"\x06\x33\xaa\xff":
        print("not a Sprint-Layout 6 file"); return 1
    canvas_h, pads, copper, zones = read(data)

    if a.list or not (a.src and a.dst):
        count = defaultdict(int)
        for p in pads:
            key = ("tht:%.2f/%.2f" % (p["drill"], p["outer"])) if p["kind"] == "tht" else \
                  ("smd:%.2fx%.2f" % tuple(sorted(p["size"], reverse=True)))
            count[key] += 1
        print("pad sizes on this board (use as --from / --to):")
        for k, v in sorted(count.items()):
            print("  %-22s %4d" % (k, v))
        return 0

    src, dst = parse_pattern(a.src), parse_pattern(a.dst)
    groups = connectivity(pads, copper, zones)
    S = [i for i, p in enumerate(pads) if matches(p, src, a.tol)]
    D = [i for i, p in enumerate(pads) if matches(p, dst, a.tol)]
    by_group = defaultdict(set)
    for j in D:
        for g in groups.get(j, ()):
            by_group[g].add(j)
    print("%d pads match --from %s, %d pads match --to %s" % (len(S), a.src, len(D), a.dst))
    fails, served = [], defaultdict(list)
    for i in S:
        reach = set()
        for g in groups.get(i, ()):
            reach |= by_group[g]
        reach.discard(i)
        for j in reach:
            served[j].append(i)
        if len(reach) < a.min or (a.max is not None and len(reach) > a.max):
            fails.append((i, sorted(reach)))
    for i, reach in fails:
        p = pads[i]
        what = "connected to none" if not reach else "connected to %d: %s" % (
            len(reach), ", ".join("(%.2f, %.2f)" % pads[j]["at"] for j in reach))
        print("  FAIL  %s at X %8.3f  Y %8.3f   %s" % (describe(p), p["at"][0], p["at"][1], what))
    shared = [(j, s) for j, s in served.items() if len(s) > 1] if a.shared else []
    for j, s in shared:
        print("  SHARED  %s at X %8.3f  Y %8.3f   serves %d --from pads" % (describe(pads[j]), *pads[j]["at"], len(s)))
    rule = "at least %d" % a.min if a.max is None else ("exactly %d" % a.min if a.min == a.max
                                                          else "%d to %d" % (a.min, a.max))
    print("%d of %d pads fail the rule (%s)%s" % (len(fails), len(S), rule,
          "" if not a.shared else ", %d shared --to pads" % len(shared)))

    marks = [pads[i]["at"] for i, _ in fails] + [pads[j]["at"] for j, _ in shared]
    if a.mark and marks:
        rings = L.group([L.arc(x, y, 1.6, 0.2, 0, 0, canvas_h=canvas_h, layer=L.S1) for x, y in marks], 9998)
        co = L.count_ofs(data)
        n = struct.unpack_from("<I", data, co)[0]
        end = co + 4
        for o, t, ln in L.walk(data):
            end = o + ln
        out = (data[:co] + struct.pack("<I", n + len(rings)) + data[co + 4:end]
               + b"".join(rings) + data[end:])
        open(a.mark, "wb").write(out)
        print("marked copy -> " + a.mark)
    return 2 if fails or shared else 0


if __name__ == "__main__":
    sys.exit(main())
