# lay6-tools - utilities for Sprint-Layout 6 files
# Copyright (C) 2026 Alex
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version. It is distributed
# WITHOUT ANY WARRANTY; see the LICENSE file for details.
"""lay6_crossings.py - find copper tracks that cross each other by mistake in a Sprint-Layout 6 board.

    python lay6_crossings.py board.lay6 [--mark marked.lay6] [--tol 0.02]

Rule: two track pieces on the SAME copper layer that form an X - each one passes through the
other and continues - are reported as a crossing (a short circuit made by mistake). If the meeting
point is at the end of either piece (a T or an L), it is a deliberate connection and is not
reported. Pieces lying on top of each other in the same direction are not crossings either.
A crossing between two tracks that are already connected elsewhere (through T/L joints or
shared pads) is the same net and harmless; it is counted separately (--all lists them).

--mark writes a COPY of the board with a ring on the top silkscreen at every crossing. All rings
are one group, so one click selects them all for deleting. The original file is never changed.
"""
import argparse, math, os, struct, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lay6 as L

LAYER_NAMES = {1: "top copper (C1)", 3: "bottom copper (C2)", 5: "inner 1 (I1)", 6: "inner 2 (I2)"}
COPPER = set(LAYER_NAMES)


def read_tracks(data):
    """-> canvas height (mm), [(object number, layer, width mm, [(x, y) UI mm, ...])]"""
    canvas_h = struct.unpack_from("<I", data, 0x2F)[0] / 10000.0
    out = []
    for n, (o, t, ln) in enumerate(L.walk(data), 1):
        if t != 6 or data[o + 0x16] not in COPPER:
            continue
        k = struct.unpack_from("<I", data, o + 0x55 + struct.unpack_from("<I", data, o + 0x51)[0])[0]
        p = o + 0x59 + struct.unpack_from("<I", data, o + 0x51)[0] + 4 * k
        m = struct.unpack_from("<I", data, p)[0]
        c = struct.unpack_from("<%df" % (2 * m), data, p + 4)
        pts = [(c[2 * i] / 10000.0, c[2 * i + 1] / 10000.0 + canvas_h) for i in range(m)]
        width = struct.unpack_from("<I", data, o + 0x11)[0] / 10000.0
        out.append((n, data[o + 0x16], width, pts))
    return canvas_h, out


def read_pads(data, canvas_h):
    """-> [(layers set, kind, geometry)]: SMD pad = polygon on its layer, through-hole = circle on all."""
    out = []
    for o, t, ln in L.walk(data):
        if t not in (2, 8):
            continue
        k = struct.unpack_from("<I", data, o + 0x55 + struct.unpack_from("<I", data, o + 0x51)[0])[0]
        p = o + 0x59 + struct.unpack_from("<I", data, o + 0x51)[0] + 4 * k
        m = struct.unpack_from("<I", data, p)[0]
        c = struct.unpack_from("<%df" % (2 * m), data, p + 4)
        pts = [(c[2 * i] / 10000.0, c[2 * i + 1] / 10000.0 + canvas_h) for i in range(m)]
        if t == 8:
            out.append(({data[o + 0x16]}, "poly", pts))
        else:
            r = struct.unpack_from("<f", data, o + 0x09)[0] / 10000.0
            if data[o + 0x17] in (3, 2) or len(pts) >= 4:     # square / octagon: use outline
                out.append((COPPER, "poly", pts))
            else:                                             # round / oblong: segment swept by r
                out.append((COPPER, "cap", (pts[0], pts[-1], r)))
    return out


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _in_poly(p, poly):
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def nets_by_connection(tracks, pads):
    """Group tracks that are connected to each other through T/L joints or shared pads.
    Returns a group number per track (same number = same copper, i.e. same net)."""
    parent = list(range(len(tracks) + len(pads)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i

    def join(i, j):
        parent[root(i)] = root(j)
    ends = [(ti, pts[0]) for ti, (_, _, _, pts) in enumerate(tracks)] + \
           [(ti, pts[-1]) for ti, (_, _, _, pts) in enumerate(tracks)]
    for ti, e in ends:
        layer, w = tracks[ti][1], tracks[ti][2]
        for pj, (lays, kind, g) in enumerate(pads):        # end on a pad
            if layer in lays and ((kind == "poly" and _in_poly(e, g)) or
                                  (kind == "cap" and _seg_dist(e, g[0], g[1]) <= g[2])):
                join(ti, len(tracks) + pj)
        for tj, (_, lay2, w2, pts2) in enumerate(tracks):  # end on another track (T or L joint)
            if tj != ti and lay2 == layer and any(_seg_dist(e, a, b) <= (w + w2) / 4 + 1e-6
                                                  for a, b in zip(pts2, pts2[1:])):
                join(ti, tj)
    return [root(i) for i in range(len(tracks))]


def x_crossing(a, b, c, d, tol):
    """Point where segments a-b and c-d cross as an X (meeting point inside both, more than tol
    from every end), else None."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    la, lc = math.hypot(rx, ry), math.hypot(sx, sy)
    if la == 0 or lc == 0 or abs(den) < 1e-12 * la * lc:
        return None                                   # parallel or overlapping: not an X
    t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / den
    u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / den
    if tol / la < t < 1 - tol / la and tol / lc < u < 1 - tol / lc:
        return a[0] + t * rx, a[1] + t * ry
    return None


def find(tracks, tol=0.02, cell=5.0):
    segs = []                                         # (track index, piece index, layer, a, b)
    for ti, (_, layer, _, pts) in enumerate(tracks):
        for si, (a, b) in enumerate(zip(pts, pts[1:])):
            if a != b:
                segs.append((ti, si, layer, a, b))
    grid = defaultdict(list)                          # spatial grid so big boards stay fast
    for k, (_, _, layer, a, b) in enumerate(segs):
        for gx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
            for gy in range(int(min(a[1], b[1]) // cell), int(max(a[1], b[1]) // cell) + 1):
                grid[(layer, gx, gy)].append(k)
    found, seen = [], set()
    for bucket in grid.values():
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                k1, k2 = sorted((bucket[i], bucket[j]))
                if (k1, k2) in seen:
                    continue
                seen.add((k1, k2))
                t1, s1, lay, a, b = segs[k1]
                t2, s2, _, c, d = segs[k2]
                if t1 == t2 and abs(s1 - s2) <= 1:
                    continue                          # neighbouring pieces of one track share an end
                p = x_crossing(a, b, c, d, tol)
                if p:
                    found.append((lay, p, t1, t2))
    return sorted(found, key=lambda f: (f[0], -f[1][1], f[1][0]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lay6_crossings.py", description="Find tracks on the same copper "
                                 "layer that cross each other as an X (T and L joints are connections, not errors).")
    ap.add_argument("board", help="Sprint-Layout 6 board (.lay6)")
    ap.add_argument("--mark", metavar="OUT.lay6", help="write a copy with a ring around every crossing")
    ap.add_argument("--all", action="store_true",
                    help="also list crossings between tracks that are already connected elsewhere (same net)")
    ap.add_argument("--tol", type=float, default=0.02,
                    help="how close to a track end (mm) still counts as a T joint (default 0.02)")
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        ap.print_help(); return 0
    a = ap.parse_args(argv)
    data = open(a.board, "rb").read()
    if data[:4] != b"\x06\x33\xaa\xff":
        print("not a Sprint-Layout 6 file"); return 1
    canvas_h, tracks = read_tracks(data)
    found_all = find(tracks, a.tol)
    net = nets_by_connection(tracks, read_pads(data, canvas_h)) if found_all else []
    found = [f for f in found_all if net[f[2]] != net[f[3]]]
    same = [f for f in found_all if net[f[2]] == net[f[3]]]
    print("%d copper tracks checked" % len(tracks))
    for lay, (x, y), t1, t2 in found:
        print("  CROSSING on %-20s at X %8.3f  Y %8.3f mm   (tracks %.2f and %.2f mm wide)"
              % (LAYER_NAMES[lay], x, y, tracks[t1][2], tracks[t2][2]))
    if same and a.all:
        for lay, (x, y), t1, t2 in same:
            print("  same net  %-20s at X %8.3f  Y %8.3f mm   (harmless: already connected elsewhere)"
                  % (LAYER_NAMES[lay], x, y))
    print("%d crossing(s)%s" % (len(found), "" if not same else
          ", plus %d where both tracks already belong to the same connected copper (harmless%s)"
          % (len(same), "" if a.all else "; --all lists them")))
    if a.mark and found:
        n = struct.unpack_from("<I", data, L.COUNT_OFS)[0]
        rings = L.group([L.arc(x, y, 1.0, 0.2, 0, 0, canvas_h=canvas_h, layer=L.S1) for _, (x, y), _, _ in found],
                        9999)
        end = L.COUNT_OFS + 4
        for o, t, ln in L.walk(data):
            end = o + ln
        out = (data[:L.COUNT_OFS] + struct.pack("<I", n + len(rings)) + data[L.COUNT_OFS + 4:end]
               + b"".join(rings) + data[end:])        # rings add nothing to the per-pad list
        open(a.mark, "wb").write(out)
        print("marked copy -> " + a.mark)
    return 2 if found else 0


if __name__ == "__main__":
    sys.exit(main())
