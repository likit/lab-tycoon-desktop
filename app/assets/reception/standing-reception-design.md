# Superseded by pneumatic reception

See `pneumatic-reception-design.md` for the current source and build instructions.

# Standing specimen reception

Built-in ImageGen source: `animation-source/standing_reception.png`.
The compact workstation has a monitor and keyboard tray, scanner in its cradle,
and a small side shelf with a blood-tube rack. No large desk or printer.
Runtime artwork is `specimens_reception_station_empty.png` (180 x 210 pixels).
The station uses one tile plus front clearance, retaining its equipment IDs.

Run `.venv/bin/python scripts/build_reception_station.py` to rebuild the idle
sprite and eight-frame atlas. The screen updates and scanner indicator lights
while active. Staff remain separate, alternating keyboard entry and tube
scanning during registration; pause holds the current pose.

## Generation prompt

Use case: stylized-concept. Create a compact specimen reception workstation for an isometric medical laboratory game. Reference image is the matching standing computer station; use SAME camera, materials, monitor, keyboard tray, slender pedestal column and H-shaped floor base, realistically shaded 3D illustration. Redesign as a specimen registration version with ONE small side shelf on the RIGHT of keyboard holding a handheld barcode scanner resting upright in a charging cradle and a small white rack of six blood tubes with red and purple caps and white barcode labels. Rack and scanner clearly visible, keyboard remains unobstructed for a standing operator approaching from lower LEFT. Keep workstation narrow and minimal: no large desk, no cabinets, drawers, printer, chair, people, words or logos. Monitor is a simple blank pale-blue quadrilateral for animated UI. Whole standalone object, small transparent margin, true transparent background, no floor or external shadow. Maintain standing keyboard height, stable compact base, minimal clean silhouette and blue accent edges. The tube rack is immediately beside the scanner on that same small shelf, not on the floor.
