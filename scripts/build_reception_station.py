"""Build a fixed reception workstation with a smoothly rising pneumatic carrier."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageChops
from app.system.equipment_animation import RECEPTION_FRAME_COUNT, reception_delivery_pose, project_report_screen

ASSETS = Path(__file__).resolve().parents[1] / 'app' / 'assets'
SIZE = (220, 230)


def build():
    with Image.open(ASSETS / 'reception/animation-source/pneumatic_reception.png') as source:
        source = source.convert('RGBA')
    box = source.getchannel('A').point(lambda alpha: 255 if alpha > 64 else 0).getbbox()
    body = source.crop(box)
    body.thumbnail((200, 220), Image.Resampling.LANCZOS)
    offset = ((SIZE[0] - body.width) // 2, SIZE[1] - body.height)
    sx, sy = body.width / (box[2] - box[0]), body.height / (box[3] - box[1])
    def point(x, y):
        return (offset[0] + (x - box[0]) * sx, offset[1] + (y - box[1]) * sy)
    def fitted(image):
        result = Image.new('RGBA', SIZE)
        resized = image.crop(box).resize(body.size, Image.Resampling.LANCZOS)
        result.alpha_composite(resized, offset)
        return result
    corners = [point(x, y) for x, y in ((366, 64), (625, 167), (599, 397), (343, 287))]
    # Carrier moves behind the fixed receiving chamber's casing and guide rails.
    window = Image.new('L', source.size)
    aperture = ImageDraw.Draw(window)
    aperture.rounded_rectangle((836, 615, 894, 1080), radius=12, fill=255)
    aperture.rounded_rectangle((817, 447, 905, 582), radius=16, fill=255)
    with Image.open(ASSETS / 'reception/animation-source/specimen_carrier.png') as carrier:
        carrier = carrier.convert('RGBA')
    carrier = carrier.crop(carrier.getchannel('A').point(lambda alpha: 255 if alpha > 64 else 0).getbbox())
    carrier.thumbnail((64, 126), Image.Resampling.LANCZOS)
    atlas = Image.new('RGBA', (SIZE[0] * RECEPTION_FRAME_COUNT, SIZE[1]))
    idle = fitted(source)
    idle.alpha_composite(project_report_screen(0, SIZE, corners))
    for index in range(RECEPTION_FRAME_COUNT):
        progress, opacity = reception_delivery_pose(index)
        top = round(1100 - (1100 - 452) * progress)
        layer = Image.new('RGBA', source.size)
        layer.alpha_composite(carrier, (round(865 - carrier.width / 2), top))
        alpha = ImageChops.multiply(layer.getchannel('A'), window)
        layer.putalpha(alpha.point(lambda value: round(value * opacity)))
        composed = source.copy()
        composed.alpha_composite(layer)
        frame = fitted(composed)
        frame.alpha_composite(project_report_screen(index // 4, SIZE, corners))
        atlas.alpha_composite(frame, (index * SIZE[0], 0))
    idle.save(ASSETS / 'reception/specimens_reception_station_empty.png')
    atlas.save(ASSETS / 'animation/specimens_reception_station_running.png')
    return atlas


if __name__ == '__main__':
    build()
