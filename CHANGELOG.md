# Changelog – lay6-tools

## 1.2 – 2026-10-30
New tool
- `lay6png.py`: board layers to PNG at an exact print size, for toner transfer, photo films and solder-mask
  films. One file per layer or layers combined; adjustable resolution; black-and-white (1-bit) or colours;
  mirroring for toner transfer; negative; centring holes; copper width compensation; solder-mask openings;
  registration marks.

Text, exactly as Sprint-Layout draws it
- `sprint_font.json`: Sprint-Layout's built-in label font, all 95 glyphs, captured from the program.
- `lay6.latin_text()` uses it for any text the font covers, with all of the program's text settings:
  height, thickness (thin / normal / bold), character width (narrow / normal / wide), horizontal and
  vertical mirroring, and rotation. Generated texts match records saved by the program (identical
  headers, same stroke widths, points within 0.001 mm), so editing them in Sprint-Layout changes nothing.
- Fixed: word spacing - a space was about 0.4 mm too wide on 1 mm text.
- `kpcb2sprint.py`: board and reference texts use Sprint-Layout's font.

Fixes
- `lay6svg.py`: through-hole pads, vias and SMD pads were drawn 1 mm too large (they inherited a 1 mm outline
  from their layer group); they now have their exact size.

Library (`lay6.py`)
- `set_name()` for the hidden element-name field (max. 255 bytes); `build()` takes the canvas size and the
  board name; `text()` takes stroke width, thickness, width, mirroring and rotation; `text_stroke_width()`.

FORMAT.md
- Text records: thickness (+0x0D), character width (+0x11), mirror flags (+0x32 / +0x33), rotation (+0x35,
  clockwise degrees), stroke widths per thickness, letter and space spacing, the full font.
- Hidden data: the element-name field survives Sprint-Layout (max. 255 bytes); data appended after the
  trailer is dropped when Sprint-Layout saves.

## 1.1 – 2026-10-26
- **Files with several boards** (Sprint-Layout's tabs) are read correctly. Every reading tool works on the first
  board that has objects; `--board N` chooses another one (`lay6svg`, `lay6_crossings`, `lay6_vias`,
  `lay6_padcheck`, `lay6cam`). Marked copies (`--mark`) put their rings into the right board.
- Each board's pad list follows its own objects (found with a 14-board file); files whose first board has
  pads no longer fail.
- `lay6.py`: new `boards()`, `board()`, `canvas()`, `count_ofs()` and `record_length()`.
- FORMAT.md:
  - the u32 at offset 4 is the number of boards (not a version number);
  - the multi-board layout and per-board pad lists;
  - the hidden per-object "element name" string, which Sprint-Layout 6.0 keeps unchanged through editing.

## 1.0 – 2026-10-25
First public version.
- `lay6svg.py`: board to SVG at 1:1 scale.
- `lay6_crossings.py`, `lay6_vias.py`, `lay6_padcheck.py`: board checks.
- `kicad2sprint.py`: KiCad footprints to Sprint macros. `kpcb2sprint.py`: KiCad board to Sprint board.
- `net2sprint.py`: LTspice netlist to Sprint board.
- `lay6cam.py`: isolation milling, drilling and outline for CNC (Roland RML-1 or G-code).
- Files from other Sprint-Layout versions that store a name string in every object are read correctly.
