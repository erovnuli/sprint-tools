LAY6 TOOLS - Sprint-Layout 6 (.lay6 / .LMK) utilities
=====================================================

Keep all files in one folder: most tools load lay6.py (and some kicad2sprint.py /
skel_font.py) from the folder they are in. Python 3.8 or newer.
Run any tool without arguments (or with -h) to see its full help.
No tool changes your original board: results are always written to a new file.
Files with several boards (Sprint's tabs): the tools use the first board that has objects;
the reading tools (lay6svg, lay6_crossings, lay6_vias, lay6_padcheck, lay6cam) take --board N.

Extra Python packages:
    lay6cam:                      pip install shapely pillow
    labels in kpcb2sprint/net2sprint:  pip install numpy pillow scikit-image scipy
Everything else uses the standard library only.


lay6svg.py - board to SVG (1:1, millimetres)
---------------------------------------------
    python lay6svg.py board.lay6 out.svg [options]

    --layers LIST    layers to include (default all): c1 c2 i1 i2 s1 s2 o drill
    --no-fill        draw zones as outlines only
    --frame          add the canvas border as a size reference
    --holes CSV      also write every hole as x;y;diameter;plated (mm)
    --yup            CSV only: Y measured upwards from the bottom edge
    --as-layers      mark each Sprint layer as an SVG layer (Corel may lock them)

Each Sprint layer becomes a plain, unlocked group. Works on its own (needs no other file).

    python lay6svg.py board.lay6 board.svg --layers "c1 s1 o" --frame


lay6_crossings.py - tracks crossing each other by mistake
----------------------------------------------------------
    python lay6_crossings.py board.lay6 [--mark OUT.lay6] [--all] [--tol MM]

Reports tracks on the same copper layer that cross as an X. T and L joints
(a track ending on another) are connections and are not reported.

    --mark OUT.lay6  write a copy with a ring (top silkscreen) at every crossing
    --all            also list crossings between tracks already on the same net
    --tol MM         how close to a track end still counts as a T joint (default 0.02)

Exit code 2 when crossings are found, 0 when clean.


lay6_vias.py - vias that do not join both copper layers
--------------------------------------------------------
    python lay6_vias.py board.lay6 [--via DRILL/OUTER] [--mark OUT.lay6] [options]

    --via D/O        via size in mm (default 0.4/0.7)
    --tol MM         size tolerance (default 0.01)
    --mark OUT.lay6  write a copy with a ring around every single-layer via
    --mark-floating  also ring vias touching no copper at all
    --ring MM        ring diameter (default 1.0)
    --ring-layer N   ring layer: 2 = S1, 4 = S2 (default 4)

    python lay6_vias.py board.lay6 --via 0.4/0.7 --mark board_vias.lay6


lay6_padcheck.py - pad-to-pad connection rules
-----------------------------------------------
    python lay6_padcheck.py board.lay6 --from KIND --to KIND [options]
    python lay6_padcheck.py board.lay6 --list

Checks that every pad of one kind reaches pads of another kind through copper.
Pad kinds (sizes in mm, * = any):
    tht:1.0/2.0   through-hole pad DRILL/OUTER (a via is a small tht pad)
    tht:1.0/*     any outer size;   tht:*/2.0  any drill
    smd:1.5x1.0   SMD pad W x H (either orientation);   smd:*  any SMD pad

    --list           list the pad sizes found on the board and exit
    --from KIND      pads to check, e.g. tht:1.0/2.0
    --to KIND        pads they must reach, e.g. tht:0.5/0.8
    --min N          at least N --to pads (default 1)
    --max N          at most N --to pads (--max 1 = exactly one)
    --shared         also report --to pads that serve more than one --from pad
    --tol MM         size tolerance (default 0.01)
    --mark OUT.lay6  write a copy with failing pads ringed

    python lay6_padcheck.py board.lay6 --from tht:1.0/2.0 --to tht:0.5/0.8 --max 1 --shared


kicad2sprint.py - KiCad footprints to Sprint macros
----------------------------------------------------
    python kicad2sprint.py INPUT OUTPUT_FOLDER [--board] [--no-back-silk]

INPUT is one .kicad_mod file or a whole .pretty folder; one .LMK macro is written per
footprint. Each footprint is reported as ok / skip / FAILED, with any approximations.

    --board          also write a .lay6 board per footprint (100 x 100 mm) for checking
    --no-back-silk   leave out back silkscreen

    python kicad2sprint.py Package_SO.pretty sprint_macros


kpcb2sprint.py - KiCad board to Sprint board
---------------------------------------------
    python kpcb2sprint.py board.kicad_pcb out.lay6 [--no-text] [--no-zones] [--margin MM]

KiCad 6 or newer. Needs lay6.py, kicad2sprint.py (and skel_font.py for text).

    --no-text        leave out references and board text
    --no-zones       leave out copper zones
    --margin MM      space around the board outline (default 5)


net2sprint.py - netlist to Sprint board
----------------------------------------
    python net2sprint.py NETLIST.net PARTS.txt out.lay6 [options]

Netlist in ExpressPCB format (LTspice: export netlist). Places the parts, moves/rotates
them to reduce crossings, draws pad-to-pad tracks, puts unavoidable crossings on the
other layer with vias. Needs lay6.py, kicad2sprint.py (and skel_font.py for labels).

Parts file, one line per part ('#' starts a comment):
    REF   FOOTPRINT                                [PINMAP]
    Q1    fp/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod    1=5,6,7,8 2=4 3=1,2,3
    C3    TH2          (built in: two through-hole pads, 2.54 mm apart)
    R*    fp/R_0805_2012Metric.kicad_mod           (wildcard on the name)
    V1    -            ('-' leaves the part out)
PINMAP maps netlist pin -> footprint pad(s); without it pin n goes to pad n.

    --width MM            track width (default 0.3)
    --layer top|bottom    track layer
    --clearance MM        minimum gap between a track and other copper (default 0.2)
    --via OUTER DRILL     via size (default 0.7 0.4)
    --no-vias             keep every track on one layer
    --no-place            keep the simple grid (no move/rotate)
    --tries N, --iters N  placement effort (default 4 attempts x 6000 steps)
    --segments            one straight track per pad pair instead of polylines


lay6cam.py - board to CNC files (isolation milling, drilling, outline)
-----------------------------------------------------------------------
    python lay6cam.py --write-config egx350.ini      (settings file, all options explained)
    python lay6cam.py board.lay6 OUTNAME --config egx350.ini [--side top|bottom|both]
                      [--ops iso,drill,outline] [--no-preview] [overrides]

Writes OUTNAME_top_iso, OUTNAME_bottom_iso (mirrored), OUTNAME_drill_<size> (one per drill
size), OUTNAME_outline (several depth passes, tabs, cut-outs first) and OUTNAME_preview.png.
Format: Roland RML-1 (.rml, EGX/MDX) or G-code (.nc), chosen in the settings file.
Warns where copper areas are closer than the cutter width (they would stay connected).
Origin (0,0) = lower-left corner of the board outline, X right, Y up, mm.

Overrides (otherwise taken from the settings file):
    --format rml|gcode   --steps-per-mm N   --tool-tip MM   --tool-angle DEG
    --cut-depth MM       --z-down MM        --passes N      --overlap 0..0.9
    --clearance MM       --feed MM/S        --plunge MM/S   --rpm N

    python lay6cam.py board.lay6 board --config egx350.ini --side bottom --ops iso --passes 3

Before the first real cut, run a file with the cutter raised above the board to check scale
and speeds. With a nose cone, z_down only presses the cone on; the cone sets the depth.


Library / data files (not run directly)
---------------------------------------
lay6.py           read/write library for .lay6 and .LMK (records, groups, macros, mask)
skel_font.py      single-stroke letters from a TrueType font, for labels
sprint_font.json  Sprint's own built-in label font (95 glyphs), captured from the app
