"""net2sprint.py - netlist (ExpressPCB format, as exported by LTspice) -> Sprint-Layout 6 board.

Places every part in a simple grid, labels it with its reference, and draws one copper track
per connection (a minimum spanning tree per net). With Sprint's "rubber band" button on, the
tracks follow the pads when the parts are dragged into place.

    python net2sprint.py LT3740.net LT3740.parts LT3740.lay6 [--width 0.3] [--layer top|bottom] [--segments]

Each net is joined by the shortest tree of connections, drawn as few polyline tracks as possible
(a track passes through pads on its way; it splits only where the net branches).

Needs lay6.py and kicad2sprint.py next to it (and skel_font.py for the reference labels).

Parts file - one line per part, '#' starts a comment:
    REF   FOOTPRINT                       [PINMAP]
    U1    kf/Package_DFN_QFN.pretty/DFN-16-1EP_3x4mm_P0.45mm_EP1.7x3.3mm.kicad_mod
    Q1    kf/Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod   1=5,6,7,8 2=4 3=1,2,3
    C3    TH2                             # built-in: two round through-hole pads, 2.54 mm apart
    V1    TH2
    R*    kf/Resistor_SMD.pretty/R_0805_2012Metric.kicad_mod        # wildcard on the prefix
    V1    -                               # '-' = leave the part out
PINMAP maps netlist pin -> footprint pad number(s); without it pin n goes to pad n.
"""
import argparse, fnmatch, math, os, random, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lay6 as L
import kicad2sprint as K

GRID_W = 60.0          # row width (mm) before wrapping; canvas is 100 x 100
MARGIN = 5.0
GAP = 8.0              # space between parts (room for labels and tracks)
LABEL_H = 1.0


# ------------------------------------------------------------------ netlist
def read_expresspcb(path):
    """-> parts [(ref, value)], nets {name: [(ref, pin), ...]}"""
    lines = open(path, encoding="latin-1").read().splitlines()
    q = lambda s: re.findall(r'"([^"]*)"', s)

    def table(title):
        i = lines.index('"%s"' % title) + 1
        out = []
        while i < len(lines) and lines[i].strip():
            out.append(lines[i]); i += 1
        return out
    parts = [tuple(q(l)[:2]) for l in table("Part IDs Table")]
    names = [q(l)[0] for l in table("Net Names Table")]
    nets = defaultdict(list)
    for l in table("Net Connections Table"):
        n, p, pin = [int(v) for v in l.split()[:3]]
        nets[names[n - 1]].append((parts[p - 1][0], str(pin)))
    return parts, nets


def read_parts(path):
    rules = []
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#")[0].split()
        if not line:
            continue
        ref, fp, maps = line[0], line[1], line[2:]
        pinmap = {}
        for m in maps:
            k, v = m.split("=")
            pinmap[k] = v.split(",")
        rules.append((ref, fp, pinmap))
    return rules


def rule_for(ref, rules):
    for r in rules:                      # exact names first, then wildcards, file order
        if r[0] == ref:
            return r
    for r in rules:
        if any(c in r[0] for c in "*?") and fnmatch.fnmatchcase(ref, r[0]):
            return r
    return None


# ------------------------------------------------------------------ footprints
class Footprint:
    """Pads (number -> list of (x, y) relative to origin), records drawn at an origin, bbox."""

    def __init__(self, spec, base):
        self.spec = spec
        if spec == "TH2":
            self.fp = None
            self.pads = {"1": [(0.0, 0.0)], "2": [(2.54, 0.0)]}
            self.tht = {"1", "2"}
            self.shapes = [("1", ("c", (0.0, 0.0), 0.9)), ("2", ("c", (2.54, 0.0), 0.9))]
            self.box = (-1.0, -1.0, 3.54, 1.0)
            return
        path = spec if os.path.isabs(spec) else os.path.join(base, spec)
        self.fp = K.sexp_parse(open(path, encoding="utf-8").read())
        self.pads = defaultdict(list)
        self.tht = set()                                 # pads that go through (reach both layers)
        self.shapes = []                                 # (pad name, copper shape) for clearance checks
        xs, ys = [], []
        for p in K.kids(self.fp, "pad"):
            at = K.nums(p, "at"); w, h = K.nums(p, "size", 2)
            ang = (at[2] if len(at) > 2 else 0) % 180
            if abs(ang - 90) < 1e-6:
                w, h = h, w
            elif abs(ang) > 1e-6:                        # odd angle: use the enclosing square
                w = h = max(w, h)
            x, y = at[0], -at[1]                         # KiCad Y down -> Sprint Y up
            xs += [x - w / 2, x + w / 2]; ys += [y - h / 2, y + h / 2]
            if any(l in ("F.Cu", "B.Cu", "*.Cu") for l in K.layers_of(p)) and p[1] not in ("", None):
                self.pads[str(p[1])].append((x, y))
                round_ = p[3] == "circle" or (p[3] == "oval" and abs(w - h) < 1e-6)
                self.shapes.append((str(p[1]), ("c", (x, y), w / 2) if round_ else ("r", (x, y), w / 2, h / 2)))
                if p[2] == "thru_hole":
                    self.tht.add(str(p[1]))
        for tag in ("fp_line", "fp_rect"):               # silkscreen extents count too
            for g in K.kids(self.fp, tag):
                if (K.layers_of(g) or [""])[0] in ("F.SilkS", "F.Silkscreen"):
                    for k in ("start", "end"):
                        x, y = K.nums(g, k, 2); xs.append(x); ys.append(-y)
        self.box = (min(xs), min(ys), max(xs), max(ys))

    def records(self, ox, oy, r=0):
        if self.fp is None:
            out = []
            for (x, y), in self.pads.values():
                X, Y = rot(x, y, r)
                out.append(L.tht_pad(ox + X, oy + Y, 1.8, 1.0, plated=True))
            return out
        conv = RotConverter(cx=ox, cy=oy, r=r)
        return conv.convert(self.fp) or []

    def rbox(self, r):
        """Bounding box after rotating by r degrees (multiple of 90)."""
        x0, y0, x1, y1 = self.box
        c = [rot(x, y, r) for x in (x0, x1) for y in (y0, y1)]
        return (min(p[0] for p in c), min(p[1] for p in c), max(p[0] for p in c), max(p[1] for p in c))


def rot(x, y, r):
    """Rotate (x, y) counter-clockwise by r degrees (0, 90, 180, 270)."""
    r %= 360
    return {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[r]


class RotConverter(K.Converter):
    """kicad2sprint converter that also rotates the footprint by r degrees (CCW, multiple of 90)."""

    def __init__(self, cx, cy, r=0):
        super().__init__(cx=cx, cy=cy)
        self.r = r

    def P(self, x, y):
        X, Y = rot(x, -y, self.r)
        return self.cx + X, self.cy + Y

    def pad(self, p):
        if self.r:
            at = K.nums(p, "at")
            ang = (at[2] if len(at) > 2 else 0.0) + self.r
            p = [(["at", repr(at[0]), repr(at[1]), repr(ang)] if isinstance(c, list) and c and c[0] == "at" else c)
                 for c in p]
        return super().pad(p)


# ------------------------------------------------------------------ routing
def mst(points):
    """Prim's minimum spanning tree -> list of (a, b) point pairs."""
    pts = list(dict.fromkeys(points))
    if len(pts) < 2:
        return []
    inside, edges = {pts[0]}, []
    rest = set(pts[1:])
    while rest:
        a, b = min(((a, b) for a in inside for b in rest), key=lambda e: math.dist(*e))
        edges.append((a, b)); inside.add(b); rest.discard(b)
    return edges


def chains(edges):
    """Split a tree into as few unbranched paths as possible (each becomes ONE polyline track).
    A tree with b branch ends needs ceil(odd-degree nodes / 2) paths; walks start at odd nodes."""
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b); adj.setdefault(b, []).append(a)
    used, paths = set(), []
    starts = [n for n in adj if len(adj[n]) % 2] + list(adj)
    for s in starts:
        while any(frozenset((s, n)) not in used for n in adj[s]):
            path, cur = [s], s
            while True:
                nxt = [n for n in adj[cur] if frozenset((cur, n)) not in used]
                if not nxt:
                    break
                # keep going straight-ish: prefer the neighbour that continues the direction
                if len(path) > 1:
                    px, py = path[-2]; dx, dy = cur[0] - px, cur[1] - py
                    nxt.sort(key=lambda n: -((n[0] - cur[0]) * dx + (n[1] - cur[1]) * dy) /
                             (math.dist(n, cur) * math.hypot(dx, dy) or 1))
                used.add(frozenset((cur, nxt[0]))); cur = nxt[0]; path.append(cur)
            paths.append(path)
    return paths


# ------------------------------------------------------------------ placement optimiser
def _cross(p1, p2, p3, p4):
    """True if segments p1-p2 and p3-p4 properly cross (shared end points do not count)."""
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 1e-9) - (v < -1e-9)
    if p1 in (p3, p4) or p2 in (p3, p4):
        return False
    return o(p1, p2, p3) * o(p1, p2, p4) < 0 and o(p3, p4, p1) * o(p3, p4, p2) < 0


def _seg_dist(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def place_shape(shape, ox, oy, r):
    """Pad shape in footprint coordinates -> board coordinates (rotation r = 0/90/180/270)."""
    X, Y = rot(shape[1][0], shape[1][1], r)
    c = (round(ox + X, 4), round(oy + Y, 4))
    if shape[0] == "c":
        return ("c", c, shape[2])
    hw, hh = (shape[3], shape[2]) if r % 180 else (shape[2], shape[3])
    return ("r", c, hw, hh)


def _pt_rect(p, c, hw, hh):
    return math.hypot(max(abs(p[0] - c[0]) - hw, 0.0), max(abs(p[1] - c[1]) - hh, 0.0))


def seg_shape_dist(a, b, sh):
    """Distance from segment a-b to a pad shape (0 if the segment runs over it)."""
    if sh[0] == "c":
        return max(0.0, _seg_dist(sh[1], a, b) - sh[2])
    _, c, hw, hh = sh
    # Liang-Barsky clip: does the segment enter the rectangle?
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    inside = True
    for p_, q_ in ((-dx, a[0] - (c[0] - hw)), (dx, (c[0] + hw) - a[0]),
                   (-dy, a[1] - (c[1] - hh)), (dy, (c[1] + hh) - a[1])):
        if p_ == 0:
            if q_ < 0:
                inside = False; break
        else:
            t = q_ / p_
            if p_ < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                inside = False; break
    if inside:
        return 0.0
    corners = [(c[0] + sx * hw, c[1] + sy * hh) for sx in (-1, 1) for sy in (-1, 1)]
    return min([_pt_rect(a, c, hw, hh), _pt_rect(b, c, hw, hh)] + [_seg_dist(k, a, b) for k in corners])


def pt_shape_dist(p, sh):
    if sh[0] == "c":
        return max(0.0, math.dist(p, sh[1]) - sh[2])
    return _pt_rect(p, sh[1], sh[2], sh[3])


def shape_reach(sh):
    return sh[2] if sh[0] == "c" else math.hypot(sh[2], sh[3])


def escape(pt, g, ctr):
    """Fan-out point for a long SMD pad: straight out along the pad's long axis, away from the
    part centre, 0.5 mm past the pad end. Square / round pads (and through-hole pads) get none."""
    if g[0] != "r":
        return []
    hw, hh = g[2], g[3]
    if hw >= 1.5 * hh and abs(pt[0] - ctr[0]) > 1e-6:
        return [(round(pt[0] + math.copysign(hw + 0.5, pt[0] - ctr[0]), 4), pt[1])]
    if hh >= 1.5 * hw and abs(pt[1] - ctr[1]) > 1e-6:
        return [(pt[0], round(pt[1] + math.copysign(hh + 0.5, pt[1] - ctr[1]), 4))]
    return []


def net_paths(items):
    """items: [(pad point, ref, board shape, part centre, through-hole?)] of one net ->
    (unique pad points, [(p, q, escape points at p, escape points at q)] along the shortest tree).
    Pads of the same part are joined directly; otherwise each end fans out first."""
    info = {}
    for pt, ref, g, ctr, tht in items:
        info.setdefault(pt, (ref, g, ctr, tht))
    pts = list(info)
    out = []
    for p, q in mst(pts):
        if info[p][0] == info[q][0]:
            out.append((p, q, [], []))
        else:
            ep = [] if info[p][3] else escape(p, info[p][1], info[p][2])
            eq = [] if info[q][3] else escape(q, info[q][1], info[q][2])
            out.append((p, q, ep, eq))
    return pts, out


def path_of(e):
    p, q, ep, eq = e[-4:]
    return [p] + ep + eq[::-1] + [q]


def subsegs(path):
    return list(zip(path, path[1:]))


class Placer:
    """Simulated annealing over part positions and 90-degree rotations. Cost (lower is better):
    track crossings between different nets, tracks running over pads of other nets, overlapping
    parts, parts off the canvas, and (lightly) total track length."""
    W_CROSS, W_PADHIT, W_OVERLAP, W_OUT, W_LEN = 10.0, 10.0, 40.0, 40.0, 0.15
    CLEAR = 1.5                                  # free space kept around each part (mm)

    def __init__(self, placed, netpads, obstacles, lim, area=(4.0, 4.0, 96.0, 96.0)):
        # netpads: per net [(ref, (x, y)), ...]; obstacles: [(ref, net index or -1, local shape)]
        self.placed, self.netpads, self.obstacles, self.area = placed, netpads, obstacles, area
        self.lim = lim                           # track half-width + clearance
        self.refs = list(placed)

    def box(self, ref, st):
        (ox, oy), r = st[ref]
        x0, y0, x1, y1 = self.placed[ref].rbox(r)
        c = self.CLEAR
        return ox + x0 - c, oy + y0 - c, ox + x1 + c, oy + y1 + c + LABEL_H + 0.6   # label sits on top

    def nets(self, st):
        out = []
        for members in self.netpads:
            items = []
            for ref, (x, y), sh, tht in members:
                (ox, oy), r = st[ref]
                X, Y = rot(x, y, r)
                pt = (round(ox + X, 4), round(oy + Y, 4))
                x0, y0, x1, y1 = self.placed[ref].box
                cx, cy = rot((x0 + x1) / 2, (y0 + y1) / 2, r)
                items.append((pt, ref, place_shape(sh, ox, oy, r), (ox + cx, oy + cy), tht))
            out.append(net_paths(items))
        return out

    def cost(self, st, detail=False):
        nets = self.nets(st)
        segs = [(i, a, b) for i, (_, es) in enumerate(nets) for e in es for a, b in subsegs(path_of(e))]
        cross = sum(1 for k, (i, a, b) in enumerate(segs) for (j, c, d) in segs[k + 1:]
                    if i != j and _cross(a, b, c, d))
        obs = []
        for ref, j, sh in self.obstacles:
            (ox, oy), r = st[ref]
            g = place_shape(sh, ox, oy, r)
            obs.append((j, g, shape_reach(g) + self.lim))
        hits = 0
        for i, a, b in segs:
            x0, x1 = min(a[0], b[0]), max(a[0], b[0]); y0, y1 = min(a[1], b[1]), max(a[1], b[1])
            for j, g, reach in obs:
                c = g[1]
                if j != i and x0 - reach < c[0] < x1 + reach and y0 - reach < c[1] < y1 + reach \
                        and seg_shape_dist(a, b, g) < self.lim:
                    hits += 1
        length = sum(math.dist(a, b) for _, a, b in segs)
        boxes = [self.box(r, st) for r in self.refs]
        over = 0.0
        for k, A in enumerate(boxes):
            for B in boxes[k + 1:]:
                w = min(A[2], B[2]) - max(A[0], B[0]); h = min(A[3], B[3]) - max(A[1], B[1])
                if w > 0 and h > 0:
                    over += w * h
        X0, Y0, X1, Y1 = self.area
        out = sum(max(0, X0 - b[0]) + max(0, b[2] - X1) + max(0, Y0 - b[1]) + max(0, b[3] - Y1) for b in boxes)
        c = (self.W_CROSS * cross + self.W_PADHIT * hits + self.W_OVERLAP * over + self.W_OUT * out
             + self.W_LEN * length)
        return (c, cross, hits, over, length) if detail else c

    def run(self, st, iters=6000, seed=1):
        rnd = random.Random(seed)
        cur, cc = dict(st), self.cost(st)
        best, bc = dict(cur), cc
        T0, T1 = 20.0, 0.05
        X0, Y0, X1, Y1 = self.area
        for i in range(iters):
            T = T0 * (T1 / T0) ** (i / iters)
            step = max(0.5, 25.0 * (1 - i / iters))
            new = dict(cur)
            m = rnd.random()
            ref = rnd.choice(self.refs)
            (ox, oy), r = new[ref]
            if m < 0.6:                                   # move
                new[ref] = ((min(X1, max(X0, ox + rnd.uniform(-step, step))),
                             min(Y1, max(Y0, oy + rnd.uniform(-step, step)))), r)
            elif m < 0.8:                                 # rotate in place
                new[ref] = ((ox, oy), (r + rnd.choice((90, 180, 270))) % 360)
            else:                                         # swap two parts
                other = rnd.choice(self.refs)
                new[ref], new[other] = (new[other][0], r), (new[ref][0], new[other][1])
            nc = self.cost(new)
            if nc < cc or rnd.random() < math.exp((cc - nc) / T):
                cur, cc = new, nc
                if cc < bc:
                    best, bc = dict(cur), cc
        # snap to a 0.635 mm (1/40 inch) grid, keeping the result if it is no worse
        snap = {k: ((round(x / 0.635) * 0.635, round(y / 0.635) * 0.635), r) for k, ((x, y), r) in best.items()}
        return snap if self.cost(snap) <= bc + 1e-6 else best


def label(textstr, x, y):
    try:
        import skel_font as S
        st, _ = S.text_strokes(textstr, x, y, LABEL_H, ref="HEL")
        return [L.text(textstr.encode("ascii", "replace").decode(), st, x, y, height=LABEL_H)]
    except Exception as e:                               # no font libraries: skip labels
        print("        - label %s skipped (%s)" % (textstr, e))
        return []


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="net2sprint.py", description="ExpressPCB netlist (LTspice export) "
                                 "-> Sprint-Layout 6 board with parts placed in a grid and pad-to-pad tracks.")
    ap.add_argument("netlist"); ap.add_argument("parts"); ap.add_argument("output")
    ap.add_argument("--width", type=float, default=0.3, help="track width in mm (default 0.3)")
    ap.add_argument("--layer", choices=("top", "bottom"), default="top", help="track copper layer")
    ap.add_argument("--no-place", dest="place", action="store_false",
                    help="keep the simple grid; do not move/rotate parts to reduce crossings")
    ap.add_argument("--tries", type=int, default=4, help="placement attempts, best one kept (default 4)")
    ap.add_argument("--iters", type=int, default=6000, help="steps per attempt (default 6000)")
    ap.add_argument("--clearance", type=float, default=0.2,
                    help="minimum gap between a track and other copper in mm (default 0.2)")
    ap.add_argument("--no-vias", dest="vias", action="store_false",
                    help="keep every track on one layer (no moving crossing tracks to the 2nd layer)")
    ap.add_argument("--via", nargs=2, type=float, default=(0.7, 0.4), metavar=("OUTER", "DRILL"),
                    help="via size in mm (default 0.7 0.4)")
    ap.add_argument("--segments", dest="chain", action="store_false",
                    help="one straight track per pad pair, instead of joining them into polylines")
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        ap.print_help(); return
    a = ap.parse_args(argv)
    a.via_outer, a.via_drill = a.via
    base = os.path.dirname(os.path.abspath(a.parts))
    parts, nets = read_expresspcb(a.netlist)
    rules = read_parts(a.parts)

    # footprints and pin maps
    placed, pinmaps, skipped = {}, {}, []
    for ref, val in parts:
        r = rule_for(ref, rules)
        if r is None:
            print("no footprint for %s (%s) - add it to the parts file" % (ref, val)); return 1
        if r[1] == "-":
            skipped.append(ref); continue
        placed[ref] = Footprint(r[1], base)
        pinmaps[ref] = r[2]

    # grid placement, biggest parts first
    order = sorted(placed, key=lambda r: -(placed[r].box[2] - placed[r].box[0]) * (placed[r].box[3] - placed[r].box[1]))
    x, ytop, rowh, origin = MARGIN, 100.0 - MARGIN, 0.0, {}
    for ref in order:
        x0, y0, x1, y1 = placed[ref].box
        w, h = x1 - x0, y1 - y0
        if x + w > MARGIN + GRID_W and x > MARGIN:
            x, ytop, rowh = MARGIN, ytop - rowh - GAP, 0.0
        origin[ref] = (x - x0, ytop - LABEL_H - 1.0 - y1)     # top-left of the part box at (x, ytop - label)
        x += w + GAP
        rowh = max(rowh, h + LABEL_H + 1.0)
    if ytop - rowh < 0:
        print("warning: parts run past the bottom of the 100 x 100 mm canvas")
    state = {ref: (origin[ref], 0) for ref in order}
    lim = a.width / 2 + a.clearance              # a track closer than this to foreign copper "touches" it
    padnet = {}                                  # (ref, footprint pad) -> net index
    for i, (name, members) in enumerate(nets.items()):
        for ref, pin in members:
            for pad in pinmaps.get(ref, {}).get(pin, [pin]):
                padnet[(ref, pad)] = i

    if a.place:
        netpads = []
        for name, members in nets.items():
            m = []
            for ref, pin in members:
                if ref in placed:
                    for pad in pinmaps[ref].get(pin, [pin]):
                        m += [(ref, sh[1], sh, pad in placed[ref].tht)
                              for pn, sh in placed[ref].shapes if pn == pad]
            netpads.append(m)
        obstacles = [(ref, padnet.get((ref, pad), -1), sh) for ref in placed for pad, sh in placed[ref].shapes]
        pl = Placer(placed, netpads, obstacles, lim)
        c0 = pl.cost(state, True)
        best = None
        for seed in range(1, a.tries + 1):
            st = pl.run(state, iters=a.iters, seed=seed)
            c = pl.cost(st, True)
            print("  try %d: %3d crossings, %3d tracks touching other pads, overlap %.1f mm2, length %.0f mm"
                  % (seed, c[1], c[2], c[3], c[4]))
            if best is None or c[0] < best[0][0]:
                best = (c, st)
        (c, *_), state = best[0], best[1]
        print("placement: crossings %d -> %d, tracks touching other pads %d -> %d, overlap %.1f mm2"
              % (c0[1], best[0][1], c0[2], best[0][2], best[0][3]))

    objs = []
    for gid, ref in enumerate(order, 1):
        (ox, oy), r = state[ref]
        x0, y0, x1, y1 = placed[ref].rbox(r)
        part = placed[ref].records(ox, oy, r) + label(ref, ox + x0, oy + y1 + 0.6)
        objs += L.group(part, gid)        # pads + silkscreen + label move as one object
    origin = {ref: state[ref][0] for ref in order}

    # tracks: every net's pads, joined along the shortest tree, fanning out of long SMD pads
    netdata, tht_pts, n_tracks = [], set(), 0
    for name, members in nets.items():
        items, missing = [], []
        for ref, pin in members:
            if ref not in placed:
                continue
            (ox, oy), r = state[ref]
            x0, y0, x1, y1 = placed[ref].box
            cx, cy = rot((x0 + x1) / 2, (y0 + y1) / 2, r)
            for pad in pinmaps[ref].get(pin, [pin]):
                shs = [sh for pn, sh in placed[ref].shapes if pn == pad]
                if not shs:
                    missing.append("%s.%s" % (ref, pad)); continue
                for sh in shs:
                    g = place_shape(sh, ox, oy, r)
                    tht = pad in placed[ref].tht
                    items.append((g[1], ref, g, (ox + cx, oy + cy), tht))
                    if tht:
                        tht_pts.add(g[1])
        pts, es = net_paths(items)
        netdata.append((name, pts, es, missing))

    top = L.C1 if a.layer == "top" else L.C2
    other = L.C2 if top == L.C1 else L.C1
    edges = [(i,) + e for i, (_, _, es, _) in enumerate(netdata) for e in es]     # (net, p, q, ep, eq)

    obs = []                                     # every pad's copper: (net, shape, through-hole?)
    for ref in order:
        (ox, oy), r = state[ref]
        for pad, sh in placed[ref].shapes:
            obs.append((padnet.get((ref, pad), -1), place_shape(sh, ox, oy, r), pad in placed[ref].tht))
    part_boxes = []
    for ref in order:
        (ox, oy), r = state[ref]
        x0, y0, x1, y1 = placed[ref].rbox(r)
        part_boxes.append((ox + x0, oy + y0, ox + x1, oy + y1))
    via_r = a.via_outer / 2

    def crosses(n1, path1, n2, path2):
        return n1 != n2 and any(_cross(a1_, b1_, a2_, b2_) for a1_, b1_ in subsegs(path1)
                                for a2_, b2_ in subsegs(path2))

    def touches(net, path, layer_top, vias=()):
        """Does a track of `net` along `path` come too close to copper of another net on that layer?"""
        for p, q in subsegs(path):
            for j, g, tht in obs:
                if j != net and (layer_top or tht) and seg_shape_dist(p, q, g) < lim:
                    return True
            if any(j != net and _seg_dist(v, p, q) < via_r + lim for j, v in vias):
                return True
        return False

    def via_for(net, pad, start, toward, vias):
        """Via on the way from `start` (pad or its fan-out point) towards `toward`, or None."""
        if pad in tht_pts:
            return pad                           # through-hole pad already reaches both layers
        L_ = math.dist(start, toward)
        ux, uy = (toward[0] - start[0]) / L_, (toward[1] - start[1]) / L_
        d = 1.0 if start == pad else via_r + 0.2
        while d <= L_ - 1.0:
            v = (round(start[0] + ux * d, 4), round(start[1] + uy * d, 4))
            ok = all(pt_shape_dist(v, g) >= via_r + a.clearance for j, g, _ in obs) \
                and not any(b[0] - via_r < v[0] < b[2] + via_r and b[1] - via_r < v[1] < b[3] + via_r
                            for b in part_boxes) \
                and all(math.dist(v, w) >= 2 * via_r + a.clearance for _, w in vias) \
                and not any(e[0] != net and _seg_dist(v, s1, s2) < via_r + lim
                            for e in edges for s1, s2 in subsegs(path_of(e)))
            if ok:
                return v
            d += 0.25
        return None

    # move tracks that cross or touch other copper to the second layer, one at a time, always the
    # worst one, as long as its new route is clean there (no crossing, no through-hole pad or via).
    # Only the middle part moves; the fan-out stubs stay on the pads' layer and end in vias.
    on_other, routed = set(), {}                 # routed[k] = (top stub at p, bottom part, top stub at q)
    if a.vias:
        while True:
            all_vias = [(edges[k][0], v) for k in on_other for v in (routed[k][1][0], routed[k][1][-1])]
            topset = [k for k in range(len(edges)) if k not in on_other]
            stubs = [(edges[k][0], st) for k in on_other for st in (routed[k][0], routed[k][2]) if len(st) > 1]
            score = {}
            for k in topset:
                n, P = edges[k][0], path_of(edges[k])
                c = sum(1 for m in topset if m != k and crosses(n, P, edges[m][0], path_of(edges[m])))
                c += sum(1 for n2, st in stubs if crosses(n, P, n2, st))
                c += 1 if touches(n, P, True, all_vias) else 0
                if c:
                    score[k] = c
            moved = False
            for k in sorted(score, key=lambda k: -score[k]):
                net, p, q, ep, eq = edges[k]
                A, B = (ep[-1] if ep else p), (eq[-1] if eq else q)
                if A == B or math.dist(A, B) < 2.5:
                    continue
                v1 = via_for(net, p, A, B, all_vias)
                if v1 is None:
                    continue
                v2 = via_for(net, q, B, A, all_vias + ([(net, v1)] if v1 != p else []))
                if v2 is None:
                    continue
                s1 = [p] + ep + ([v1] if v1 != p else [])
                s2 = [q] + eq + ([v2] if v2 != q else [])
                mid = [v1, v2]
                if any(crosses(net, mid, edges[m][0], routed[m][1]) for m in on_other) \
                        or touches(net, mid, False, all_vias) \
                        or (len(s1) > 1 and touches(net, s1, True, all_vias)) \
                        or (len(s2) > 1 and touches(net, s2, True, all_vias)):
                    continue
                on_other.add(k); routed[k] = (s1, mid, s2); moved = True
                break
            if not moved:
                break

    # build the tracks
    via_list, top_edges, other_edges = [], defaultdict(list), defaultdict(list)
    for k, e in enumerate(edges):
        i = e[0]
        if k not in on_other:
            top_edges[i] += subsegs(path_of(e)); continue
        s1, mid, s2 = routed[k]
        top_edges[i] += subsegs(s1) + subsegs(s2)
        other_edges[i] += subsegs(mid)
        via_list += [(i, v) for v, pad in ((mid[0], e[1]), (mid[1], e[2])) if v != pad]

    print("nets:")
    for i, (name, pts, es, missing) in enumerate(netdata):
        n_here = 0
        for lay, groups in ((top, top_edges), (other, other_edges)):
            if groups.get(i):
                paths = chains(groups[i]) if a.chain else [[p, q] for p, q in groups[i]]
                for path in paths:
                    objs.append(L.track(path, a.width, layer=lay))
                n_here += len(paths)
        n_tracks += n_here
        disp = "GND" if name == "0" else name
        note = "" if len(pts) > 1 else "   (single pad - nothing to connect)"
        moved_here = sum(1 for k in on_other if edges[k][0] == i)
        if moved_here:
            note += "   %d on 2nd layer" % moved_here
        if missing:
            note += "   MISSING pads: " + ", ".join(missing)
        print("  %-6s %2d pads, %2d tracks%s" % (disp, len(pts), n_here, note))
    for _, v in dict.fromkeys(via_list):
        objs.append(L.tht_pad(v[0], v[1], a.via_outer, a.via_drill, plated=True, layer=top))

    # final check on what was actually drawn
    tsegs = [(i, [p, q]) for i, es in top_edges.items() for p, q in es]
    osegs = [(i, [p, q]) for i, es in other_edges.items() for p, q in es]
    cross_left = sum(1 for S in (tsegs, osegs) for k in range(len(S)) for m in range(k + 1, len(S))
                     if crosses(S[k][0], S[k][1], S[m][0], S[m][1]))
    touch_left = sum(1 for i, P in tsegs if touches(i, P, True, via_list)) + \
        sum(1 for i, P in osegs if touches(i, P, False, via_list))
    if a.vias:
        print("2nd layer: %d tracks moved, %d vias" % (len(on_other), len(set(v for _, v in via_list))))
    print("left for hand routing: %d crossings, %d track pieces too close to other copper "
          "(width %.2f, clearance %.2f mm)" % (cross_left, touch_left, a.width, a.clearance))
    L.build(a.output, objs)
    if skipped:
        print("left out: " + ", ".join(skipped))
    print("%d parts, %d tracks -> %s" % (len(placed), n_tracks, a.output))


if __name__ == "__main__":
    sys.exit(main())
