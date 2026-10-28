"""lay6cam.py - Sprint-Layout 6 board (.lay6) -> isolation milling, drilling and outline cutting
for a CNC engraver or router. Writes Roland RML-1 (EGX / MDX) or G-code (GRBL and similar).

    python lay6cam.py board.lay6 OUTNAME [--config cam.ini] [--side top|bottom|both]
                      [--ops iso,drill,outline] [overrides ...]
    python lay6cam.py --write-config cam.ini        (settings file with every option, commented)

Writes OUTNAME_top_iso.rml, OUTNAME_bottom_iso.rml, OUTNAME_drill_0.80.rml, OUTNAME_outline.rml
(.nc for G-code) and OUTNAME_preview.png. Coordinates: origin = lower-left corner of the board
outline (or of the copper, if there is no outline), X right, Y up, millimetres.

The bottom side is mirrored left-right around the board centre, so after turning the board
over (around its vertical axis) it lines up with the top.

Needs: lay6.py next to it, and   pip install shapely pillow
"""
import argparse, configparser, math, os, struct, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lay6 as L

try:
    from shapely.geometry import LineString, Point, Polygon, MultiPolygon, box
    from shapely.ops import unary_union, polygonize, linemerge
    from shapely import affinity
except ImportError:
    sys.exit("lay6cam needs shapely:   pip install shapely pillow")

TOP, BOT, OUTLINE = 1, 3, 7

DEFAULT_CONFIG = """\
# lay6cam settings. Units: mm, mm/s (speeds), rpm. Lines starting with # are comments.
# Any value can also be overridden on the command line, e.g. --passes 3 --feed 4

[machine]
# rml   = Roland RML-1 (EGX-350, EGX-360, MDX-...)      gcode = GRBL / LinuxCNC / Mach3 style
format = rml
# RML only: machine steps per mm. 100 = 0.01 mm/step, 40 = 0.025 mm/step (see the machine's
# resolution setting; check with a test: a 10 mm move must measure 10 mm)
steps_per_mm = 100
# travel height above the surface between cuts
safe_z = 2.0
# moves the whole job; the board's lower-left corner is (0, 0) plus this offset
offset_x = 0
offset_y = 0
# RML only: extra commands at the start, e.g. a spindle-speed command if your machine needs one
rml_start =
# G-code only: spindle on with speed (S is filled in from rpm), and the end of program
gcode_spindle_on = M3 S{rpm}
gcode_end = M5\\nG0 Z{safe_z}\\nM2

[isolation]
# cutter: tip width, and the cone's full angle for V-cutters (0 = treat as a flat tip)
# Roland ZEC-A2013-BAL: 0.13 mm tip
tool_tip = 0.13
tool_angle = 0
# real cutting depth (with a nose cone: the depth set on the cone) - used for the cut width
cut_depth = 0.05
# commanded Z when cutting. With a nose cone and auto-Z, the cone sets the real depth; Z only has
# to press the cone onto the board, so this is a little deeper than cut_depth. Without a cone,
# set it to the real depth (negative = below the surface).
z_down = -0.3
passes = 2
# overlap between neighbouring passes, as a part of the cut width (0.3 = 30 %)
overlap = 0.3
# extra gap between copper and the first pass
clearance = 0
feed = 5
plunge = 1
rpm = 20000
# drop isolation paths shorter than this (tiny slivers)
min_path = 0.05

[drill]
# drill depth below the surface (through the board)
z_down = -1.8
feed = 1
plunge = 0.5
rpm = 15000
# holes with diameters closer than this share one file / tool
group_tolerance = 0.05
# one file per drill size (yes), or all holes in one file (no)
file_per_size = yes

[outline]
# end mill diameter for cutting the board out
tool_diameter = 1.0
# total depth (board thickness plus a little) and depth per pass
depth = 1.7
step_down = 0.4
feed = 2
plunge = 0.5
rpm = 20000
# cut outside the outline (yes) or exactly on it (no)
outside = yes
# tabs that hold the board in place: count (0 = none), width and height
tabs = 4
tab_width = 2.0
tab_height = 0.6
"""

OVERRIDES = [  # (option, section, key, type, help)
    ("format", "machine", "format", str, "rml or gcode"),
    ("steps-per-mm", "machine", "steps_per_mm", float, "RML steps per mm"),
    ("tool-tip", "isolation", "tool_tip", float, "isolation cutter tip width, mm"),
    ("tool-angle", "isolation", "tool_angle", float, "isolation cutter cone angle, degrees"),
    ("cut-depth", "isolation", "cut_depth", float, "real isolation depth, mm"),
    ("z-down", "isolation", "z_down", float, "commanded isolation Z, mm"),
    ("passes", "isolation", "passes", int, "isolation passes"),
    ("overlap", "isolation", "overlap", float, "pass overlap, 0..0.9"),
    ("clearance", "isolation", "clearance", float, "extra gap before the first pass, mm"),
    ("feed", "isolation", "feed", float, "isolation feed, mm/s"),
    ("plunge", "isolation", "plunge", float, "isolation plunge speed, mm/s"),
    ("rpm", "isolation", "rpm", float, "isolation spindle speed"),
]


# ------------------------------------------------------------------------------ reading
def _u32(d, p):
    return struct.unpack_from("<I", d, p)[0]


def _points(d, o, ch):
    s = _u32(d, o + 0x51)
    k = _u32(d, o + 0x55 + s)
    p = o + 0x59 + s + 4 * k
    m = _u32(d, p)
    c = struct.unpack_from("<%df" % (2 * m), d, p + 4)
    return [(c[2 * i] / 1e4, c[2 * i + 1] / 1e4 + ch) for i in range(m)]


def _text_strokes(d, o, ch):
    Ln = _u32(d, o + 0x4D)
    p = o + 0x51 + Ln
    p += 4 + _u32(d, p)
    p += 4 + 4 * _u32(d, p)
    n = _u32(d, p); p += 4
    out = []
    for _ in range(n):
        m = _u32(d, p + 0x4D)
        c = struct.unpack_from("<%df" % (2 * m), d, p + 0x51)
        out.append([(c[2 * i] / 1e4, c[2 * i + 1] / 1e4 + ch) for i in range(m)])
        p += 0x51 + 8 * m
    return out


def _arc_pts(cx, cy, r, start, end):
    span = (end - start) % 360 or 360
    n = max(8, int(span / 3))
    return [(cx + r * math.cos(math.radians(start + span * i / n)),
             cy + r * math.sin(math.radians(start + span * i / n))) for i in range(n + 1)]


def _line(pts, w):
    if len(pts) == 1 or all(p == pts[0] for p in pts):
        return Point(pts[0]).buffer(max(w / 2, 0.01), quad_segs=8)
    return LineString(pts).buffer(max(w / 2, 0.005), quad_segs=8)


def read_board(data):
    """-> dict: copper {layer: [geom]}, cut {layer: [geom]}, holes [(x, y, d, plated)],
    outline [LineString], canvas (w, h)"""
    cw, ch = L.canvas(data)
    copper, cut, holes, outline = defaultdict(list), defaultdict(list), [], []
    for o, t, ln in L.walk(data):
        lay = data[o + 0x16]
        if t == 2:                                       # through-hole pad
            x, y, R, D = struct.unpack_from("<4f", data, o + 1)
            x, y, R, D = x / 1e4, y / 1e4 + ch, R / 1e4, D / 1e4
            pts = _points(data, o, ch)
            shape = data[o + 0x17]
            if shape in (2, 3) and len(pts) >= 3:
                g = Polygon(pts)
            elif shape == 4 and len(pts) >= 2:
                g = LineString([pts[0], pts[-1]]).buffer(R, quad_segs=16)
            else:
                g = Point(x, y).buffer(R, quad_segs=16)
            if lay in (TOP, BOT, 5, 6):
                for l in (TOP, BOT):
                    copper[l].append(g)
            if D > 0:
                holes.append((x, y, 2 * D, data[o + 0x39] != 0))
        elif t == 8 and lay in (TOP, BOT):               # SMD pad
            pts = _points(data, o, ch)
            if len(pts) >= 3:
                copper[lay].append(Polygon(pts))
        elif t == 6:                                     # track / line
            w = _u32(data, o + 0x11) / 1e4
            pts = _points(data, o, ch)
            if lay in (TOP, BOT):
                copper[lay].append(_line(pts, w))
            elif lay == OUTLINE and len(pts) >= 2:
                outline.append(LineString(pts))
        elif t == 4 and lay in (TOP, BOT):               # zone
            w = _u32(data, o + 0x11) / 1e4
            pts = _points(data, o, ch)
            if len(pts) >= 3:
                g = Polygon(pts).buffer(w / 2, quad_segs=8)
                (cut if data[o + 0x34] else copper)[lay].append(g)
        elif t == 5:                                     # arc / circle
            x, y, Ro, Ri = struct.unpack_from("<4f", data, o + 1)
            end = _u32(data, o + 0x11) / 1000.0
            start = _u32(data, o + 0x1F) / 1000.0
            r, w = (Ro + Ri) / 2e4, (Ro - Ri) / 1e4
            pts = _arc_pts(x / 1e4, y / 1e4 + ch, r, start, end)
            if lay in (TOP, BOT):
                copper[lay].append(_line(pts, w))
            elif lay == OUTLINE:
                outline.append(LineString(pts))
        elif t == 7 and lay in (TOP, BOT):               # text on copper: its strokes
            h = struct.unpack_from("<f", data, o + 0x09)[0] / 1e4
            for st in _text_strokes(data, o, ch):
                if st:
                    copper[lay].append(_line(st, max(h / 8, 0.1)))
    return dict(copper=copper, cut=cut, holes=holes, outline=outline, canvas=(cw, ch))


def snap_ends(lines, tol=0.05):
    """Join line ends that are closer than tol (outline pieces drawn by hand rarely meet exactly)."""
    reps = []
    def rep(p):
        for q in reps:
            if abs(q[0] - p[0]) <= tol and abs(q[1] - p[1]) <= tol:
                return q
        reps.append(p); return p
    out = []
    for ls in lines:
        c = list(ls.coords)
        c[0], c[-1] = rep(c[0]), rep(c[-1])
        if len(c) >= 2 and LineString(c).length > 0:
            out.append(LineString(c))
    return out


def board_shape(B):
    """Board area as a polygon (with cut-outs as holes): the largest closed shape on the outline
    layer; else the outline extents; else copper extents + 1 mm."""
    if B["outline"]:
        merged = unary_union(snap_ends(B["outline"]))
        polys = list(polygonize(merged))
        if polys:
            outer = max(polys, key=lambda p: Polygon(p.exterior).area)
            n = len(outer.interiors)
            return outer, "outline" + (", %d cut-out%s" % (n, "s" if n > 1 else "") if n else "")
        return merged.envelope, "outline extents (outline is not closed)"
    allc = unary_union([g for gs in B["copper"].values() for g in gs] or [box(0, 0, 1, 1)])
    return allc.envelope.buffer(1.0, join_style=2), "copper extents + 1 mm (no outline layer)"


# ------------------------------------------------------------------------------ toolpaths
def rings(geom):
    if geom.is_empty:
        return []
    polys = getattr(geom, "geoms", [geom])
    out = []
    for p in polys:
        if p.geom_type != "Polygon":
            continue
        out.append(list(p.exterior.coords))
        out += [list(i.coords) for i in p.interiors]
    return out


def lines_of(geom):
    if geom.is_empty:
        return []
    if geom.geom_type in ("LineString", "LinearRing"):
        return [list(geom.coords)]
    return [c for g in getattr(geom, "geoms", []) for c in lines_of(g)]


def cut_width(cfg):
    tip = cfg.getfloat("isolation", "tool_tip")
    ang = cfg.getfloat("isolation", "tool_angle")
    dep = abs(cfg.getfloat("isolation", "cut_depth"))
    return tip + (2 * dep * math.tan(math.radians(ang / 2)) if ang > 0 else 0)


def isolation(copper, board, cfg):
    """-> (paths, copper geometry, problem points). Paths: list of point lists (closed rings
    are repeated start = end)."""
    w = cut_width(cfg)
    n = max(1, cfg.getint("isolation", "passes"))
    ov = min(max(cfg.getfloat("isolation", "overlap"), 0.0), 0.9)
    clr = cfg.getfloat("isolation", "clearance")
    minp = cfg.getfloat("isolation", "min_path")
    paths = []
    for i in range(n):
        r = w / 2 + clr + i * w * (1 - ov)
        off = copper.buffer(r, quad_segs=16)
        for ring in rings(off):
            ls = LineString(ring)
            inside = ls.intersection(board.buffer(w / 2))
            for c in lines_of(inside):
                if LineString(c).length >= minp:
                    paths.append([(round(x, 4), round(y, 4)) for x, y in LineString(c).simplify(0.003).coords])
    return order(paths), narrow_gaps(copper, w / 2 + clr)


def narrow_gaps(copper, r):
    """Copper islands closer than 2r: the first pass cannot get between them, so they stay
    connected. -> [(x, y, gap)] at the narrowest point of each such pair."""
    from shapely.strtree import STRtree
    from shapely.ops import nearest_points
    isl = [g for g in getattr(copper, "geoms", [copper]) if not g.is_empty]
    if len(isl) < 2:
        return []
    tree = STRtree(isl)
    out = []
    for i, a in enumerate(isl):
        for j in tree.query(a.buffer(2 * r)):
            j = int(j)
            if j <= i:
                continue
            d = a.distance(isl[j])
            if d < 2 * r - 1e-4:
                p, q = nearest_points(a, isl[j])
                out.append(((p.x + q.x) / 2, (p.y + q.y) / 2, d))
    return out



def order(paths, start=(0.0, 0.0)):
    """Nearest-neighbour order; closed rings may start at their nearest point; open paths may
    be reversed."""
    left = list(paths)
    out, cur = [], start
    while left:
        best, bi, bj, rev = None, 0, 0, False
        for i, p in enumerate(left):
            closed = len(p) > 2 and p[0] == p[-1]
            cands = range(len(p) - 1) if closed else (0, len(p) - 1)
            for j in cands:
                d = (p[j][0] - cur[0]) ** 2 + (p[j][1] - cur[1]) ** 2
                if best is None or d < best:
                    best, bi, bj = d, i, j
        p = left.pop(bi)
        if len(p) > 2 and p[0] == p[-1]:
            p = p[bj:-1] + p[:bj] + [p[bj]]
        elif bj == len(p) - 1:
            p = p[::-1]
        out.append(p)
        cur = p[-1]
    return out


def outline_paths(board, cfg):
    """-> contours [(is_outer, points)], depth levels. Outside cutting grows the outer edge and
    shrinks the cut-outs by the tool radius (the tool always runs in the waste material)."""
    d = cfg.getfloat("outline", "tool_diameter")
    shape = board.buffer(d / 2, quad_segs=16) if cfg.getboolean("outline", "outside") else board
    if shape.geom_type != "Polygon":
        shape = max(shape.geoms, key=lambda p: p.area)
    contours = [(False, list(i.coords)) for i in shape.interiors] + [(True, list(shape.exterior.coords))]
    depth = abs(cfg.getfloat("outline", "depth"))
    step = max(0.05, abs(cfg.getfloat("outline", "step_down")))
    levels, z = [], 0.0
    while z < depth - 1e-9:
        z = min(depth, z + step)
        levels.append(z)
    return contours, levels


def tab_intervals(pts, cfg):
    n = cfg.getint("outline", "tabs")
    if n <= 0:
        return []
    ls = LineString(pts)
    tw = cfg.getfloat("outline", "tab_width") + cfg.getfloat("outline", "tool_diameter")
    return [((k + 0.5) * ls.length / n - tw / 2, (k + 0.5) * ls.length / n + tw / 2) for k in range(n)]


def split_at_tabs(pts, tabs):
    """-> [(in_tab, [points])] pieces along the closed path."""
    ls = LineString(pts)
    cuts = sorted(set([0.0, ls.length] + [max(0, min(ls.length, v)) for t in tabs for v in t]))
    pieces = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 1e-6:
            continue
        mid = (a + b) / 2
        intab = any(t0 <= mid <= t1 for t0, t1 in tabs)
        seg = [ls.interpolate(a).coords[0]]
        acc = 0.0
        for p, q in zip(pts, pts[1:]):
            L2 = math.dist(p, q)
            if a < acc + L2 and acc < b:
                if acc > a:
                    seg.append(p)
            acc += L2
        seg.append(ls.interpolate(b).coords[0])
        pieces.append((intab, seg))
    return pieces


# ------------------------------------------------------------------------------ writers
class Writer:
    def __init__(self, cfg, xform):
        self.cfg, self.x = cfg, xform
        self.fmt = cfg.get("machine", "format").strip().lower()
        self.spm = cfg.getfloat("machine", "steps_per_mm")
        self.safe = abs(cfg.getfloat("machine", "safe_z"))
        self.out, self.length, self.plunges, self.time = [], 0.0, 0, 0.0
        self.pos = None

    def s(self, v):                          # mm -> RML steps
        return int(round(v * self.spm))

    def xy(self, p):
        return self.x(p)

    def begin(self, rpm, feed, plunge, zdown):
        self.feed, self.plunge_v = feed, plunge
        if self.fmt == "rml":
            self.out += ["PA;PA;"]
            extra = self.cfg.get("machine", "rml_start").strip()
            if extra:
                self.out.append(extra)
            self.out += ["VS%.1f;" % feed, "!VZ%.1f;" % plunge,
                         "!PZ%d,%d;" % (self.s(zdown), self.s(self.safe)), "!MC1;"]
        else:
            self.out += ["G21", "G90", "G17",
                         self.cfg.get("machine", "gcode_spindle_on").format(rpm=int(rpm)),
                         "G0 Z%.3f" % self.safe]
        self.zdown = zdown

    def path(self, pts, zdown=None, zlift_pieces=None):
        """One cut along pts at zdown (2D). zlift_pieces: [(in_tab, pts, z)] for outline passes."""
        pts = [self.xy(p) for p in pts]
        if self.pos is not None:
            self.time += math.dist(self.pos, pts[0]) / 40.0
        self.length += sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        self.time += sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) / self.feed
        self.time += 2 * self.safe / self.plunge_v
        self.plunges += 1
        if self.fmt == "rml":
            self.out.append("PU%d,%d;" % (self.s(pts[0][0]), self.s(pts[0][1])))
            for x, y in pts:
                self.out.append("PD%d,%d;" % (self.s(x), self.s(y)))
            self.out.append("PU%d,%d;" % (self.s(pts[-1][0]), self.s(pts[-1][1])))
        else:
            z = self.zdown if zdown is None else zdown
            self.out += ["G0 X%.4f Y%.4f" % pts[0], "G1 Z%.4f F%.1f" % (z, self.plunge_v * 60)]
            self.out.append("G1 X%.4f Y%.4f F%.1f" % (pts[1][0], pts[1][1], self.feed * 60) if len(pts) > 1 else "")
            self.out += ["G1 X%.4f Y%.4f" % p for p in pts[2:]]
            self.out.append("G0 Z%.3f" % self.safe)
        self.pos = pts[-1]

    def path3d(self, pieces):
        """Outline pass with tabs: pieces [(z, pts)] - cut continuously, changing Z per piece."""
        first = [self.xy(p) for p in pieces[0][1]][0]
        if self.fmt == "rml":
            self.out.append("PU%d,%d;" % (self.s(first[0]), self.s(first[1])))
            for z, pts in pieces:
                for x, y in (self.xy(p) for p in pts):
                    self.out.append("Z%d,%d,%d;" % (self.s(x), self.s(y), self.s(z)))
            last = self.xy(pieces[-1][1][-1])
            self.out.append("PU%d,%d;" % (self.s(last[0]), self.s(last[1])))
        else:
            self.out += ["G0 X%.4f Y%.4f" % first]
            for k, (z, pts) in enumerate(pieces):
                self.out.append("G1 Z%.4f F%.1f" % (z, self.plunge_v * 60))
                self.out += ["G1 X%.4f Y%.4f F%.1f" % (*self.xy(p), self.feed * 60) for p in pts]
            self.out.append("G0 Z%.3f" % self.safe)
        for z, pts in pieces:
            P = [self.xy(p) for p in pts]
            d = sum(math.dist(a, b) for a, b in zip(P, P[1:]))
            self.length += d; self.time += d / self.feed
        self.time += 2 * self.safe / self.plunge_v; self.plunges += 1

    def drill(self, p):
        x, y = self.xy(p)
        self.plunges += 1
        self.time += 2 * (self.safe + abs(self.zdown)) / self.plunge_v + 0.5
        if self.fmt == "rml":
            self.out += ["PU%d,%d;" % (self.s(x), self.s(y)), "PD%d,%d;" % (self.s(x), self.s(y)),
                         "PU%d,%d;" % (self.s(x), self.s(y))]
        else:
            self.out += ["G0 X%.4f Y%.4f" % (x, y), "G1 Z%.4f F%.1f" % (self.zdown, self.plunge_v * 60),
                         "G0 Z%.3f" % self.safe]

    def end(self):
        if self.fmt == "rml":
            self.out += ["PU;", "!MC0;", "H;"]
        else:
            self.out += self.cfg.get("machine", "gcode_end").format(safe_z=self.safe).replace("\\n", "\n").split("\n")

    def save(self, path):
        sep = "\r\n" if self.fmt == "rml" else "\n"
        with open(path, "w", newline="") as f:
            f.write(sep.join(l for l in self.out if l) + sep)


# ------------------------------------------------------------------------------ preview
def preview(path, board, layers, iso, holes, outline, xform, w):
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = board.buffer(2).bounds
    wide = (x1 - x0) > (y1 - y0)
    sc = max(4.0, min(40.0, 2400.0 / max(x1 - x0, y1 - y0)))
    panels = [k for k in ("top", "bottom") if k in iso] or ["top"]
    Wp, Hp = int((x1 - x0) * sc), int((y1 - y0) * sc) + 24
    n = len(panels)
    img = Image.new("RGB", (Wp, Hp * n + 10 * (n - 1)) if wide else (Wp * n + 10 * (n - 1), Hp), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    for k, side in enumerate(panels):
        ox, oy = (0, k * (Hp + 10)) if wide else (k * (Wp + 10), 0)
        T = lambda p: (ox + (p[0] - x0) * sc, oy + 24 + (y1 - p[1]) * sc)
        dr.text((ox + 4, oy + 4), side.upper() + " (as seen from the top; bottom files are mirrored)", fill=(0, 0, 0))
        dr.polygon([T(p) for p in board.exterior.coords], fill=(250, 246, 225), outline=(150, 150, 150))
        for i in board.interiors:
            dr.polygon([T(p) for p in i.coords], fill=(255, 255, 255), outline=(150, 150, 150))
        cu = layers.get(side)
        if cu is not None:
            for g in getattr(cu, "geoms", [cu]):
                if g.geom_type == "Polygon":
                    dr.polygon([T(p) for p in g.exterior.coords], fill=(205, 120, 60))
                    for i in g.interiors:
                        dr.polygon([T(p) for p in i.coords], fill=(250, 246, 225))
        for p in iso.get(side, []):
            dr.line([T(q) for q in p], fill=(20, 60, 200), width=max(1, int(w * sc)))
        for x, y, d, _ in holes:
            r = d / 2 * sc
            c = T((x, y))
            dr.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=(0, 0, 0))
        for c in outline or []:
            dr.line([T(p) for p in c], fill=(0, 150, 0), width=2)
    img.save(path)


# ------------------------------------------------------------------------------ main
def load_config(path):
    cfg = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cfg.read_string(DEFAULT_CONFIG)
    if path:
        if not os.path.isfile(path):
            sys.exit("settings file not found: " + path)
        cfg.read(path, encoding="utf-8")
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lay6cam.py", description="Sprint-Layout 6 board -> isolation "
                                 "milling, drilling and outline files (Roland RML-1 or G-code).",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="examples:\n"
                                 "  python lay6cam.py --write-config egx350.ini\n"
                                 "  python lay6cam.py board.lay6 board --config egx350.ini\n"
                                 "  python lay6cam.py board.lay6 board --config egx350.ini --side bottom --ops iso --passes 3")
    ap.add_argument("board", nargs="?")
    ap.add_argument("outname", nargs="?", help="output name; files get _top_iso.rml etc. added")
    ap.add_argument("--config", metavar="FILE", help="settings file (see --write-config)")
    ap.add_argument("--write-config", metavar="FILE", help="write a settings file with all defaults and exit")
    ap.add_argument("--side", default="both", choices=["top", "bottom", "both"], help="copper side(s) to isolate")
    ap.add_argument("--ops", default="iso,drill,outline", help="operations: iso,drill,outline (default all)")
    ap.add_argument("--no-preview", action="store_true", help="do not write the preview PNG")
    ap.add_argument("--board", dest="board_no", type=int, metavar="N",
                    help="board number in files with several boards (default: the first one with objects)")
    for opt, sec, key, typ, hlp in OVERRIDES:
        ap.add_argument("--" + opt, dest="ov_" + key + "_" + sec, type=typ, help=hlp + " (overrides settings file)")
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        ap.print_help(); return 0
    a = ap.parse_args(argv)
    L.SELECT = a.board_no
    if a.write_config:
        with open(a.write_config, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG)
        print("settings written to", a.write_config); return 0
    if not (a.board and a.outname):
        ap.error("board and output name are needed")
    cfg = load_config(a.config)
    for opt, sec, key, typ, hlp in OVERRIDES:
        v = getattr(a, "ov_" + key + "_" + sec)
        if v is not None:
            cfg.set(sec, key, str(v))
    fmt = cfg.get("machine", "format").strip().lower()
    if fmt not in ("rml", "gcode"):
        sys.exit("format must be rml or gcode")
    ext = ".rml" if fmt == "rml" else ".nc"
    ops = set(o.strip() for o in a.ops.split(","))

    data = open(a.board, "rb").read()
    if data[:4] != b"\x06\x33\xaa\xff":
        print("not a Sprint-Layout 6 file"); return 1
    B = read_board(data)
    board, how = board_shape(B)
    bx0, by0, bx1, by1 = board.bounds
    ox, oy = cfg.getfloat("machine", "offset_x"), cfg.getfloat("machine", "offset_y")
    top_x = lambda p: (p[0] - bx0 + ox, p[1] - by0 + oy)
    bot_x = lambda p: (bx1 - p[0] + ox, p[1] - by0 + oy)          # mirrored left-right
    print("board: %.2f x %.2f mm (%s)" % (bx1 - bx0, by1 - by0, how))
    w = cut_width(cfg)
    written, iso_prev, layers = [], {}, {}

    if "iso" in ops:
        sides = ["top", "bottom"] if a.side == "both" else [a.side]
        for side in sides:
            lay = TOP if side == "top" else BOT
            if not B["copper"].get(lay):
                print("  %s: no copper - skipped" % side); continue
            cu = unary_union(B["copper"][lay])
            if B["cut"].get(lay):
                cu = cu.difference(unary_union(B["cut"][lay]))
            layers[side] = cu
            paths, problems = isolation(cu, board, cfg)
            iso_prev[side] = paths
            W = Writer(cfg, top_x if side == "top" else bot_x)
            W.begin(cfg.getfloat("isolation", "rpm"), cfg.getfloat("isolation", "feed"),
                    cfg.getfloat("isolation", "plunge"), cfg.getfloat("isolation", "z_down"))
            for p in paths:
                W.path(p)
            W.end()
            fn = "%s_%s_iso%s" % (a.outname, side, ext)
            W.save(fn); written.append(fn)
            print("  %-7s isolation: %d paths, %d passes, cut width %.3f mm, %.0f mm of cutting, about %.0f min -> %s"
                  % (side, len(paths), cfg.getint("isolation", "passes"), w, W.length, W.time / 60 + 0.5, fn))
            for x, y, g in problems[:20]:
                print("          WARNING copper gap %.3f mm at Sprint X %.2f Y %.2f is narrower than the cut (%.3f mm): "
                      "these stay CONNECTED" % (g, x, y, w + 2 * cfg.getfloat("isolation", "clearance")))
            if len(problems) > 20:
                print("          ... %d more places" % (len(problems) - 20))

    if "drill" in ops and B["holes"]:
        tol = cfg.getfloat("drill", "group_tolerance")
        groups = defaultdict(list)
        for x, y, d, pl in B["holes"]:
            key = round(round(d / tol) * tol, 3) if tol > 0 else round(d, 3)
            groups[key].append((x, y))
        per = cfg.getboolean("drill", "file_per_size")
        sizes = sorted(groups)
        bundles = [[s] for s in sizes] if per else [sizes]
        for bundle in bundles:
            W = Writer(cfg, top_x)
            W.begin(cfg.getfloat("drill", "rpm"), cfg.getfloat("drill", "feed"),
                    cfg.getfloat("drill", "plunge"), cfg.getfloat("drill", "z_down"))
            n = 0
            for s in bundle:
                if not per and fmt == "gcode" and s != bundle[0]:
                    W.out += ["M5", "G0 Z%.3f" % W.safe, "(change to %.2f mm drill)" % s, "M0",
                              cfg.get("machine", "gcode_spindle_on").format(rpm=int(cfg.getfloat("drill", "rpm")))]
                for p in order([[q] for q in groups[s]]):
                    W.drill(p[0]); n += 1
            W.end()
            fn = "%s_drill_%s%s" % (a.outname, "%.2f" % bundle[0] if per else "all", ext)
            W.save(fn); written.append(fn)
            print("  drill %s mm: %d holes -> %s" % (", ".join("%.2f" % s for s in bundle), n, fn))
        if not per and fmt == "rml" and len(sizes) > 1:
            print("          note: RML has no tool-change pause - use file_per_size = yes for several drill sizes")

    out_prev = None
    if "outline" in ops:
        if not B["outline"]:
            print("  outline: no outline layer objects - skipped")
        else:
            contours, levels = outline_paths(board, cfg)
            out_prev = [c for _, c in contours]
            th = cfg.getfloat("outline", "tab_height")
            depth = abs(cfg.getfloat("outline", "depth"))
            W = Writer(cfg, top_x)
            W.begin(cfg.getfloat("outline", "rpm"), cfg.getfloat("outline", "feed"),
                    cfg.getfloat("outline", "plunge"), -levels[0])
            ntabs = 0
            for is_outer, pts in contours:                  # cut-outs first, the outer edge last
                tabs = tab_intervals(pts, cfg) if is_outer else []
                ntabs += len(tabs)
                for z in levels:
                    if tabs and z > depth - th:
                        W.path3d([(-(depth - th) if intab else -z, seg) for intab, seg in split_at_tabs(pts, tabs)])
                    elif fmt == "rml":
                        W.path3d([(-z, pts)])
                    else:
                        W.path(pts, zdown=-z)
            W.end()
            fn = "%s_outline%s" % (a.outname, ext)
            W.save(fn); written.append(fn)
            print("  outline: %d contour%s, %d passes to %.2f mm, %d tabs, %.0f mm of cutting -> %s"
                  % (len(contours), "s" if len(contours) > 1 else "", len(levels), depth, ntabs, W.length, fn))

    if not a.no_preview and (iso_prev or out_prev or B["holes"]):
        fn = a.outname + "_preview.png"
        preview(fn, board, layers, iso_prev, B["holes"] if "drill" in ops else [], out_prev, top_x, w)
        written.append(fn)
        print("  preview -> " + fn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
