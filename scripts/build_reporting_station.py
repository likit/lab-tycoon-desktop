"""Build a printer-free standing workstation and its scrolling screen atlas."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from app.system.equipment_animation import FRAME_COUNT, project_report_screen

ASSETS = Path(__file__).resolve().parents[1] / 'app' / 'assets'
SIZE = (160, 210)


def build():
    with Image.open(ASSETS / 'reporting/animation-source/standing_computer.png') as source:
        source = source.convert('RGBA')
    box = source.getchannel('A').point(lambda alpha: 255 if alpha > 64 else 0).getbbox()
    body = source.crop(box)
    body.thumbnail((140, 200), Image.Resampling.LANCZOS)
    offset = ((SIZE[0] - body.width) // 2, SIZE[1] - body.height)
    base = Image.new('RGBA', SIZE)
    base.alpha_composite(body, offset)
    sx, sy = body.width / (box[2] - box[0]), body.height / (box[3] - box[1])
    corners = [(offset[0] + (x - box[0]) * sx, offset[1] + (y - box[1]) * sy)
               for x, y in ((329, 97), (645, 233), (645, 477), (329, 326))]
    atlas = Image.new('RGBA', (SIZE[0] * FRAME_COUNT, SIZE[1]))
    idle = None
    for index in range(FRAME_COUNT):
        frame = base.copy()
        frame.alpha_composite(project_report_screen(index, SIZE, corners))
        if idle is None:
            idle = frame.copy()
        atlas.alpha_composite(frame, (index * SIZE[0], 0))
    idle.save(ASSETS / 'reporting/reporting_station_empty.png')
    atlas.save(ASSETS / 'animation/reporting_station_running.png')
    return atlas


if __name__ == '__main__':
    build()
