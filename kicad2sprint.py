"""kicad2sprint.py - KiCad footprints (.kicad_mod / .pretty) -> Sprint-Layout 6 macros (.LMK).
Needs lay6.py next to it.  python kicad2sprint.py MyLib.pretty out_folder [--board] [--no-back-silk]
"""
import struct
from lay6 import *
from lay6 import _f
# =========================================================================================
#  KiCad footprint (.kicad_mod / .pretty) -> Sprint-Layout 6 macro (.LMK) converter
# =========================================================================================
import math, os, sys, argparse


def pad_rot(x, y, w, h, angle_deg, canvas_h=100.0, layer=COPPER):
    """SMD pad rotated by any angle (degrees, CCW). The app draws SMD pads from their corner
    list; for angles that are not multiples of 90 this is UNVERIFIED in the app."""
    r = bytearray(pad(x, y, w, h, canvas_h, layer))
    X, Y = _f(x), _f(y - canvas_h)
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    pts = []
    for dx, dy in ((-w / 2, h / 2), (w / 2, h / 2), (w / 2, -h / 2), (-w / 2, -h / 2)):
        pts += [X + _f(dx * c - dy * s), Y + _f(dx * s + dy * c)]
    struct.pack_into("<8f", r, 0x5D, *pts)
    return bytes(r)


# ------------------------------------------------------------------ S-expression reader
def sexp_parse(text):
    tokens, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "()":
            tokens.append(ch); i += 1
        elif ch.isspace():
            i += 1
        elif ch == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(text[j]); j += 1
            tokens.append(("str", "".join(buf))); i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            tokens.append(text[i:j]); i = j
    pos = 0

    def read():
        nonlocal pos
        t = tokens[pos]; pos += 1
        if t == "(":
            lst = []
            while tokens[pos] != ")":
                lst.append(read())
            pos += 1
            return lst
        return t[1] if isinstance(t, tuple) else t
    return read()


def kids(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def kid(node, name):
    k = kids(node, name)
    return k[0] if k else None


def nums(node, name, count=None):
    k = kid(node, name)
    if k is None:
        return None
    v = [float(t) for t in k[1:] if isinstance(t, str) and _isnum(t)]
    return v if count is None else (v + [0.0] * count)[:count]


def _isnum(t):
    try:
        float(t); return True
    except ValueError:
        return False


def layers_of(node):
    k = kid(node, "layers") or kid(node, "layer")
    return [t for t in k[1:] if isinstance(t, str)] if k else []


# ------------------------------------------------------------------ converter
SILK_LAYERS = {"F.SilkS": S1, "F.Silkscreen": S1, "B.SilkS": S2, "B.Silkscreen": S2,   # S2 = 4, verified
               "Edge.Cuts": OUTLINE}                 # outline = layer 7 (verified)
OUTLINE_W = 0.1                                      # line width for drawn holes / cut-outs


class Converter:
    def __init__(self, cx=50.0, cy=50.0, back_silk=True):
        self.cx, self.cy, self.back_silk = cx, cy, back_silk

    def P(self, x, y):                       # KiCad (Y down) -> Sprint UI (Y up), placed at cx, cy
        return self.cx + x, self.cy - y

    def convert(self, fp):
        self.notes, objs = [], []
        for p in kids(fp, "pad"):
            objs += self.pad(p)
        objs += self.merged_lines(kids(fp, "fp_line"))
        for tag in ("fp_rect", "fp_circle", "fp_arc", "fp_poly"):
            for g in kids(fp, tag):
                objs += self.graphic(tag, g)
        skipped_text = len(kids(fp, "fp_text")) + len(kids(fp, "property"))
        if skipped_text:
            self.notes.append("text/properties not converted (%d)" % skipped_text)
        if not objs:
            self.notes.append("nothing to convert - skipped")
            return None
        if not any(r[0] in (2, 8) for r in objs):
            self.notes.append("no copper pads: macro written unshifted (UNVERIFIED)")
        return objs

    # ---------------------------------------------------------------- pads
    def pad(self, p):
        num, kind, shape = p[1], p[2], p[3]
        at = nums(p, "at")
        x, y = at[0], at[1]
        ang = at[2] if len(at) > 2 else 0.0
        w, h = nums(p, "size", 2)
        lay = layers_of(p)
        X, Y = self.P(x, y)
        tag = "pad %s" % (num or "(unnamed)")
        if kind == "smd" or kind == "connect":
            cu = [l for l in lay if l in ("F.Cu", "B.Cu", "*.Cu")]
            if not cu:
                return []                                     # paste/mask-only aperture
            layer = C2 if cu == ["B.Cu"] else C1
            if shape in ("circle", "oval"):
                self.notes.append("%s: %s SMD pad -> rectangle" % (tag, shape))
            elif shape in ("trapezoid", "custom"):
                self.notes.append("%s: %s SMD pad -> bounding rectangle" % (tag, shape))
            a = ang % 360
            if abs(a % 90) < 1e-6:
                if round(a / 90) % 2:
                    w, h = h, w
                return [pad(X, Y, w, h, layer=layer)]
            self.notes.append("%s: rotated %g deg (non-90 rotation unverified in the app)" % (tag, a))
            return [pad_rot(X, Y, w, h, a, layer=layer)]
        # ---- through-hole
        dk = kid(p, "drill")
        dv = [t for t in (dk[1:] if dk else []) if isinstance(t, str)]
        if dv and dv[0] == "oval":
            dvals = [float(t) for t in dv[1:3] if _isnum(t)]
            drill = min(dvals)
            if kind != "np_thru_hole":
                self.notes.append("%s: plated slot %s -> round hole %.2f mm" % (tag, "x".join("%g" % v for v in dvals), drill))
        else:
            drill = float(dv[0]) if dv and _isnum(dv[0]) else 0.0
        plated = kind == "thru_hole"
        if kind == "np_thru_hole":
            # mounting hole without copper -> outline-layer drawing of the hole (how holes are
            # normally drawn in Sprint; a pad with ring = hole shows a big X in the app)
            if dv and dv[0] == "oval":
                dx_, dy_ = [float(t) for t in dv[1:3] if _isnum(t)]
                return self.slot_outline(X, Y, dx_, dy_, ang)
            return [arc(X, Y, drill / 2, OUTLINE_W, 0, 0, layer=OUTLINE)]
        a = ang % 360
        if shape == "circle" or (shape == "oval" and abs(w - h) < 1e-6):
            return [tht_pad(X, Y, w, drill, plated=plated)]
        if shape in ("rect", "roundrect") and abs(w - h) < 1e-6:
            if abs(a % 90) > 1e-6:
                self.notes.append("%s: square pad rotated %g deg -> unrotated" % (tag, a))
            return [tht_pad(X, Y, w, drill, plated=plated, shape="square")]
        # Oval or non-square rect. Sprint's oblong is ALWAYS 2:1 (the app rebuilds its two
        # points at +-radius as soon as the pad is touched), so pick the biggest Sprint shape
        # that fits inside the KiCad pad: round/square of the short side, or a 2:1 oblong.
        d, L = min(w, h), max(w, h)
        D = min(d, L / 2)                                    # oblong diameter that fits
        area_obl = D * D * (1 + math.pi / 4)
        if shape == "oval":
            area_one, one = math.pi * d * d / 4, "round"
        else:
            area_one, one = d * d, "square"
        if area_one >= area_obl or D < drill + 0.2:
            self.notes.append("%s: %gx%g %s through-hole pad -> %s %g mm" % (tag, w, h, shape, one, d))
            return [tht_pad(X, Y, d, drill, plated=plated, shape=one)]
        self.notes.append("%s: %gx%g %s through-hole pad -> 2:1 oblong %gx%g mm" % (tag, w, h, shape, 2 * D, D))
        # KiCad pad angles are counter-clockwise as seen on screen, same sense as Sprint
        return [tht_pad(X, Y, D, drill, plated=plated, shape="rounded",
                        angle=(0.0 if w >= h else 90.0) + a)]

    # ---------------------------------------------------------------- graphics
    def graphic(self, tag, g):
        lay = (layers_of(g) or [""])[0]
        if lay in ("F.Cu", "B.Cu") and tag == "fp_poly":
            pts = [self.P(*xy) for xy in self.poly_pts(g)]
            return [zone(pts, width=self.width(g), layer=C1 if lay == "F.Cu" else C2)]
        if lay not in SILK_LAYERS:
            return []                                        # fab, courtyard, user layers
        layer = SILK_LAYERS[lay]
        if layer == S2 and not self.back_silk:
            self.notes.append("back silkscreen skipped (--no-back-silk)")
            return []
        w = self.width(g)
        if tag == "fp_line":
            return [line([self.P(*nums(g, "start", 2)), self.P(*nums(g, "end", 2))], w, layer=layer)]
        if tag == "fp_rect":
            (x0, y0), (x1, y1) = self.P(*nums(g, "start", 2)), self.P(*nums(g, "end", 2))
            return rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), w, layer=layer)
        if tag == "fp_poly":
            pts = [self.P(*xy) for xy in self.poly_pts(g)]
            return [line(pts + pts[:1], w, layer=layer)]
        if tag == "fp_circle":
            cx, cy = self.P(*nums(g, "center", 2)); ex, ey = self.P(*nums(g, "end", 2))
            return [arc(cx, cy, math.hypot(ex - cx, ey - cy), w, 0, 0, layer=layer)]
        if tag == "fp_arc" and kid(g, "mid") is None:
            # KiCad 5 style: (start = centre) (end = arc start point) (angle = sweep, positive
            # = clockwise on screen). Sprint draws counter-clockwise from start to end.
            c = self.P(*nums(g, "start", 2)); p1 = self.P(*nums(g, "end", 2))
            sweep = (nums(g, "angle") or [0.0])[0]
            r = math.hypot(p1[0] - c[0], p1[1] - c[1])
            a1 = math.degrees(math.atan2(p1[1] - c[1], p1[0] - c[0])) % 360
            if abs(abs(sweep) - 360) < 1e-6:
                return [arc(c[0], c[1], r, w, 0, 0, layer=layer)]
            a0, a2 = (a1 - sweep, a1) if sweep > 0 else (a1, a1 - sweep)
            return [arc(c[0], c[1], r, w, a0 % 360, a2 % 360, layer=layer)]
        if tag == "fp_arc":
            s, m, e = (self.P(*nums(g, k, 2)) for k in ("start", "mid", "end"))
            c = circumcentre(s, m, e)
            if c is None:
                return [line([s, e], w, layer=layer)]
            r = math.hypot(s[0] - c[0], s[1] - c[1])
            a = lambda p: math.degrees(math.atan2(p[1] - c[1], p[0] - c[0])) % 360
            a0, am, a1 = a(s), a(m), a(e)
            if (am - a0) % 360 > (a1 - a0) % 360:             # mid not on CCW path s->e
                a0, a1 = a1, a0
            return [arc(c[0], c[1], r, w, a0, a1, layer=layer)]
        return []

    def merged_lines(self, segs):
        """Chain line segments that share end points (same layer and width) into polylines,
        and drop interior points that lie on a straight line. KiCad often draws one edge as
        two pieces meeting in the middle; in Sprint that looks like overlapping lines."""
        groups = {}
        for g in segs:
            lay = (layers_of(g) or [""])[0]
            if lay not in SILK_LAYERS:
                continue
            if SILK_LAYERS[lay] == S2 and not self.back_silk:
                self.notes.append("back silkscreen skipped (--no-back-silk)")
                continue
            key = (SILK_LAYERS[lay], round(self.width(g), 4))
            a = tuple(round(v, 4) for v in nums(g, "start", 2))
            b = tuple(round(v, 4) for v in nums(g, "end", 2))
            if a != b:
                groups.setdefault(key, []).append([a, b])
        out = []
        for (layer, w), pieces in groups.items():
            while pieces:
                chain = pieces.pop(0)
                grown = True
                while grown:
                    grown = False
                    for i, (p, q) in enumerate(pieces):
                        if p == chain[-1]:   chain.append(q)
                        elif q == chain[-1]: chain.append(p)
                        elif q == chain[0]:  chain.insert(0, p)
                        elif p == chain[0]:  chain.insert(0, q)
                        else: continue
                        pieces.pop(i); grown = True; break
                pts = [chain[0]]
                for i in range(1, len(chain) - 1):
                    (x0, y0), (x1, y1), (x2, y2) = pts[-1], chain[i], chain[i + 1]
                    if abs((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)) > 1e-6:
                        pts.append(chain[i])               # keep real corners only
                pts.append(chain[-1])
                out.append(line([self.P(*p) for p in pts], w, layer=layer))
        return out

    def slot_outline(self, X, Y, sx, sy, ang):
        """Obround cut-out (two half circles joined by straight lines) on the outline layer."""
        r, half = min(sx, sy) / 2, abs(sx - sy) / 2
        base = 0.0 if sx >= sy else 90.0
        t = math.radians(base + ang)
        ux, uy = math.cos(t), math.sin(t)                # slot axis
        nx, ny = -uy, ux                                 # normal
        c1 = (X - ux * half, Y - uy * half); c2 = (X + ux * half, Y + uy * half)
        a = math.degrees(t)
        return [arc(c2[0], c2[1], r, OUTLINE_W, a - 90, a + 90, layer=OUTLINE),
                arc(c1[0], c1[1], r, OUTLINE_W, a + 90, a + 270, layer=OUTLINE),
                line([(c1[0] + nx * r, c1[1] + ny * r), (c2[0] + nx * r, c2[1] + ny * r)], OUTLINE_W, layer=OUTLINE),
                line([(c1[0] - nx * r, c1[1] - ny * r), (c2[0] - nx * r, c2[1] - ny * r)], OUTLINE_W, layer=OUTLINE)]

    @staticmethod
    def width(g):
        st = kid(g, "stroke")
        v = nums(st, "width") if st else nums(g, "width")
        return v[0] if v else 0.15

    @staticmethod
    def poly_pts(g):
        pts = kid(g, "pts")
        return [tuple(float(t) for t in xy[1:3]) for xy in kids(pts, "xy")] if pts else []


def circumcentre(a, b, c):
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax*ax + ay*ay) * (by - cy) + (bx*bx + by*by) * (cy - ay) + (cx*cx + cy*cy) * (ay - by)) / d
    uy = ((ax*ax + ay*ay) * (cx - bx) + (bx*bx + by*by) * (ax - cx) + (cx*cx + cy*cy) * (bx - ax)) / d
    return ux, uy


def convert_file(path, out_dir, conv, also_board=False):
    with open(path, encoding="utf-8") as f:
        fp = sexp_parse(f.read())
    name = fp[1] if len(fp) > 1 and isinstance(fp[1], str) else os.path.splitext(os.path.basename(path))[0]
    objs = conv.convert(fp)
    safe = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in name)
    if objs is None:
        return name, None, conv.notes
    out = os.path.join(out_dir, safe + ".LMK")
    build_macro(out, objs)
    if also_board:
        build(os.path.join(out_dir, safe + ".lay6"), objs)
    return name, out, conv.notes


USAGE_EXAMPLES = """examples:
  python kicad2sprint.py Package_SO.pretty sprint_macros
  python kicad2sprint.py SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod out --board
  python kicad2sprint.py "C:\\KiCad\\footprints\\Package_DIP.pretty" D:\\Sprint\\Macros\\DIP --no-back-silk

output:
  each footprint is reported as ok, skip (nothing to convert) or FAILED with the reason,
  followed by every approximation made (changed pad shapes, text left out, ...).
  A failing footprint does not stop the rest of the library.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="kicad2sprint.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Convert KiCad footprints (.kicad_mod or a .pretty folder) to "
                    "Sprint-Layout 6 macros (.LMK), one macro per footprint.",
        epilog=USAGE_EXAMPLES)
    ap.add_argument("input", help=".kicad_mod file, or a .pretty folder (every .kicad_mod in it)")
    ap.add_argument("output", help="output folder for the .LMK files (created if missing)")
    ap.add_argument("--board", action="store_true",
                    help="also write a .lay6 board per footprint (100 x 100 mm canvas) for checking")
    ap.add_argument("--no-back-silk", action="store_true",
                    help="leave out back silkscreen (layer 4); included by default")
    if argv is None:
        argv = sys.argv[1:]
    if not argv:                                 # run without arguments: show the guide
        ap.print_help()
        return
    a = ap.parse_args(argv)
    os.makedirs(a.output, exist_ok=True)
    files = ([os.path.join(a.input, f) for f in sorted(os.listdir(a.input)) if f.endswith(".kicad_mod")]
             if os.path.isdir(a.input) else [a.input])
    conv = Converter(back_silk=not a.no_back_silk)
    ok = 0
    for f in files:
        try:
            name, out, notes = convert_file(f, a.output, conv, a.board)
        except Exception as e:                       # keep going through a library
            print("FAILED  %s: %s" % (os.path.basename(f), e)); continue
        if out:
            ok += 1
        print("%-7s %s" % ("ok" if out else "skip", name))
        for n in dict.fromkeys(notes):
            print("        - " + n)
    print("\n%d of %d footprints converted -> %s" % (ok, len(files), a.output))


if __name__ == "__main__":
    main()
