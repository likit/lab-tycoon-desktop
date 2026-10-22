# Standing reporting station

Built-in ImageGen artwork: `animation-source/standing_computer.png`.
Runtime sprite: `reporting_station_empty.png`, 160 x 210 pixels. The station
occupies one tile with front clearance. Existing equipment IDs and workflow
reporting behavior are retained. Staff remain separate, shown only while
allocated, with their hands aligned to the standing keyboard tray.

Run `.venv/bin/python scripts/build_reporting_station.py` to rebuild the idle
sprite and eight-frame atlas. Only the projected screen content changes; the
keyboard, pedestal and base remain fixed. There is no printing animation.

## Generation prompt

Use case: stylized-concept. Asset: isometric laboratory management game sprite. One compact standing-height computer workstation ONLY: a monitor with a blank pale blue screen mounted on a slim adjustable metal pole, a small narrow keyboard-and-mouse tray at standing typing height, a single slender ivory/light-gray pedestal column below and a stable compact H-shaped dark metal floor base. No table, no broad desk, no drawers, no cabinet, no printer, no chair, no person, no extra equipment, no words or logos. It should occupy one square floor tile. Orthographic isometric view from above, show front of monitor and front-left edge of keyboard tray; visible screen is a clean simple quadrilateral suitable for projected UI animation. Polished realistically shaded 3D game illustration with subtle dark outlines, white metal and blue accents matching medical laboratory game art. Narrow workstation approx 60cm wide, monitor 24 inches, height suited to standing adult. The keyboard tray faces lower-left toward an approaching operator. Whole freestanding station centered with small transparent margins, true transparent RGBA background, no floor or external shadow. Tall slim silhouette, roughly 3:4 width-to-height.
