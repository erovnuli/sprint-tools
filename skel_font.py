"""Single-stroke glyphs from an outline font: render -> skeletonize -> trace -> simplify."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.morphology import skeletonize

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
PX = 400                      # font size in pixels for rendering
N8 = [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]


def rdp(pts, eps):
    if len(pts) < 3: return pts
    a, b = np.array(pts[0], float), np.array(pts[-1], float)
    ab = b - a; L = np.hypot(*ab)
    d = [abs(ab[0]*(p[1]-a[1]) - ab[1]*(p[0]-a[0])) / L if L else np.hypot(*(np.array(p) - a)) for p in pts]
    i = int(np.argmax(d))
    if d[i] > eps:
        return rdp(pts[:i+1], eps)[:-1] + rdp(pts[i:], eps)
    return [pts[0], pts[-1]]


def trace(sk):
    pix = set(zip(*np.nonzero(sk)))
    nb = lambda p: [(p[0]+dy, p[1]+dx) for dy, dx in N8 if (p[0]+dy, p[1]+dx) in pix]
    deg = {p: len(nb(p)) for p in pix}
    nodes = {p for p in pix if deg[p] != 2}
    used, paths = set(), []
    def walk(start, nxt):
        path = [start, nxt]; used.add(frozenset((start, nxt)))
        while path[-1] not in nodes:
            cand = [q for q in nb(path[-1]) if frozenset((path[-1], q)) not in used and q != path[-2]]
            if not cand: break
            used.add(frozenset((path[-1], cand[0]))); path.append(cand[0])
            if path[-1] == start: break
        return path
    for n in nodes:
        for q in nb(n):
            if frozenset((n, q)) not in used: paths.append(walk(n, q))
    for p in pix:                       # pure loops (no endpoints/junctions)
        for q in nb(p):
            if frozenset((p, q)) not in used:
                nodes.add(p); paths.append(walk(p, q)); nodes.discard(p)
    # prune skeleton spurs: short branches from a junction to a free end
    spur = PX * 0.02
    keep = [p for p in paths if not (len(p) < spur and
            ((deg.get(p[0], 2) == 1 and deg.get(p[-1], 2) >= 3) or
             (deg.get(p[-1], 2) == 1 and deg.get(p[0], 2) >= 3)))]
    return keep


def glyph(ch, font_path=FONT):
    f = ImageFont.truetype(font_path, PX)
    asc, _ = f.getmetrics()
    W = int(PX * 1.6); img = Image.new('L', (W, int(PX * 1.6)), 0)
    ImageDraw.Draw(img).text((PX * 0.2, PX * 0.2), ch, font=f, fill=255)
    a = np.array(img) > 128
    sk = skeletonize(a)
    paths = [rdp([(x, y) for y, x in p], PX * 0.012) for p in trace(sk)]
    plen = lambda p: sum(np.hypot(p[i+1][0]-p[i][0], p[i+1][1]-p[i][1]) for i in range(len(p)-1))
    paths = [p for p in paths if len(p) >= 2 and plen(p) > PX * 0.03]     # drop scraps by real length
    # isolated blobs (full stops, colons, i-dots) can skeletonise to a single pixel or a
    # tiny path: draw each as a short stroke so punctuation survives
    from scipy import ndimage
    lab, n = ndimage.label(a)
    covered = np.zeros(n + 1, bool)
    for p in paths:
        for x, y in p:
            covered[lab[int(round(y)), int(round(x))]] = True
    for i in range(1, n + 1):
        if not covered[i]:
            ys, xs = np.nonzero(lab == i)
            cx, cy = xs.mean(), ys.mean(); r = max(1.0, (xs.max() - xs.min()) / 4)
            paths.append([(cx - r, cy), (cx + r, cy)])
    base = PX * 0.2 + asc               # baseline in image y
    adv = f.getlength(ch)
    left = PX * 0.2
    # to font units of 'em = 1'
    return adv / PX, [[((x - left) / PX, (base - y) / PX) for x, y in p] for p in paths]


def text_strokes(s, x0, y0, height=2.0, font_path=FONT, ref='ხლდ'):
    """Single-stroke polylines (UI mm) for any script the font covers.
    Scaled so the tallest of `ref` letters equals the app's cap height (8/9 of height).
    For Latin use ref='HEL'."""
    from PIL import ImageFont
    f = ImageFont.truetype(font_path, PX)
    G = {c: glyph(c, font_path) for c in set(s + ref) if c != ' '}
    top = max(y for c in ref for p in G[c][1] for _, y in p)
    k = (height * 8 / 9) / top
    space = f.getlength(' ') / PX
    out, x = [], x0
    for c in s:
        if c == ' ':
            x += space * k; continue
        a, ps = G[c]
        out += [[(x + u * k, y0 + v * k) for u, v in p] for p in ps]
        x += a * k
    return out, x - x0
