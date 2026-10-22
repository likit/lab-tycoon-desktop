# Pneumatic specimen reception

Rebuilt from scratch with built-in ImageGen artwork. Source components:
`animation-source/pneumatic_reception.png` and `animation-source/specimen_carrier.png`.
The computer, scanner, tube rack and pneumatic terminal share a compact base.
The specimen carrier contains three upright blood tubes side by side.

Run `.venv/bin/python scripts/build_reception_station.py`. The runtime idle
sprite is `specimens_reception_station_empty.png` (220 x 230 pixels), with a
matching 32-frame atlas. Reception runs at 16 fps over two seconds while the
other equipment and staff retain their current slower rate. Only the carrier
and monitor content change: upward travel uses smooth acceleration/deceleration,
then a docking dwell and collection fade. Aperture masks keep the carrier inside
the clear pipe and behind the terminal casing. Idle pipe is empty.

Live animation follows reception arrival time, freezes with the simulation clock
on pause, and clamps at the final pose rather than looping deliveries forever.
Artwork remains illustrative; this does not add transport delay or specimens to
the simulation. Existing equipment IDs, one-tile footprint and workflow behavior
are retained. Staff positions are aligned with the new keyboard tray.

## Workstation generation prompt

Use case: stylized-concept. Production sprite for an isometric laboratory management game. Design from scratch ONE compact minimal specimen reception workstation: standing-height computer on slender white metal pedestal with blue details and small stable base, monitor with blank pale-blue quadrilateral screen, narrow keyboard/mouse shelf on LEFT for operator approaching lower LEFT, small white rack of six red/purple capped blood tubes and handheld barcode scanner dock on small shelf. Immediately to its RIGHT integrated into same small base: a pneumatic tube reception terminal, a straight vertical clear cylindrical pipe rises FROM FLOOR and ends in a transparent receiving chamber at waist level beside tube rack. Clear empty vertical pipe must have a straight front-facing transparent window with uniform clear pale blue interior, small top docking cap and very thin side metal rails, no crossbars in central window. Pipe bottom is visibly attached at floor level, upper receiving chamber adjacent to scanner shelf, clear pipe and chamber are EMPTY with absolutely no specimen carrier inside so we can animate one later. This transparent terminal occupies the rightmost quarter of sprite, clearly separated from typing area, open visual access from floor to dock. No desk cabinets, no large table, no chair, no printer, no humans, no labels/text, no floor, no shadows beyond object. Realistically shaded polished 3D medical game illustration with subtle outlines, matching ivory/blue lab equipment art. Orthographic isometric camera show front of computer and right side; no perspective convergence. Whole machine centered with small transparent margins, true transparent RGBA background. Terminal floor pipe and dock occupy rightmost quarter, computer LEFT. Compact standing workstation, about square footprint, taller than wide.

## Final carrier generation prompt

Create a replacement pneumatic specimen carrier sprite ONLY. Same realistic shaded ivory/blue 3D isometric medical game style as reference. It must be SHORT AND WIDE: a transparent cylindrical capsule with blue end sealing rings, overall height EXACTLY twice its width. Inside are THREE BLOOD COLLECTION VIALS SIDE BY SIDE, ALL THREE CAPS AT THE SAME HEIGHT, upright parallel, each vial runs the full interior height. Red cap on left, purple middle, red right, white barcode labels. Do NOT stack vials vertically. A carrier sized for three side-by-side vials, not three vials end-to-end. Vertical cylinder axis, slight overhead view, transparent background, no workstation/pipe/floor/shadow/labels outside vials/handles. Whole standalone capsule centered with small margins. Precisely one top sealing ring and one bottom sealing ring. Transparent wall revealing all three vials side by side.
