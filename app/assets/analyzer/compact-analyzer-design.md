# Integrated analyzer sampling cycle

Rebuilt from scratch with the built-in ImageGen tool as a matched eight-frame
illustrated sampling cycle. Source: `animation-source/compact_sampling_cycle.png`
(four columns by two rows). Retains the 2 x 2 footprint and equipment IDs.

The rigid boom, spindle, needle, lighting and shadows belong to the illustrated
machine. No procedural arm is overlaid. The import pipeline locks the exterior
to frame zero and changes only the work deck. Every frame uses the same crop,
scale and anchor. Actual equipment activity still controls playback, and pause
holds the current pose.

Run `.venv/bin/python scripts/build_compact_analyzer.py` to rebuild idle artwork
and the eight-frame atlas. Runtime frames remain 256 x 256 pixels.

## Generation prompt

Use case: stylized-concept. Asset: production isometric game animation spritesheet. Design FROM SCRATCH a compact realistic clinical chemistry analyzer, ivory cabinet, dark blue plinth, two lower cabinet doors, open raised lid, exposed circular sample and reagent carousels, one integrated motorized pipetting arm mounted on a fixed vertical spindle between carousels. Polished realistically shaded 3D illustration with subtle outlines, orthographic isometric front and right side, same camera as laboratory management game. IMPORTANT deliver exactly EIGHT complete machine sprites arranged in a precise 4 columns by 2 rows grid, evenly spaced, equal cell dimensions, each identical machine scale and floor anchor, no dividers, no text, no labels, no people, no background, no floor shadows. Canvas landscape 2:1, each cell square. Machine fills each cell but with 8% transparent margin. The machine itself is compact square cabinet not long modular analyzer. Animate the actual built-in solid ivory probe boom and thin metal needle, not an extra overlay. Frame sequence reading left to right then next row: 1 arm over left sample carousel raised; 2 same arm lowers needle into a cup; 3 needle withdrawn; 4 fixed-length arm rotates across deck; 5 arm over right reagent carousel raised; 6 needle lowers into right cup; 7 needle withdrawn; 8 arm rotates back. Mechanically credible one rigid fixed-length boom rotates around same spindle, vertical probe only drops when stationary, no stretching arm, no multiple arms, no floating joints. Keep lid cabinet plinth carousels spindle hardware absolutely identical position shape shading across all eight frames; ONLY arm rotation and needle height change. Moving needle/arm entirely within central work deck around 30-55% of each sprite height, never cross lid or cabinet door. Clean detailed compact lab instrument artwork; no schematic lines. True transparent RGBA.
