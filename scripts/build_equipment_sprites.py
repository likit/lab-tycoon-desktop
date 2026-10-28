"""Rebuild eight-frame activity atlases from the equipment artwork."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFilter, ImageChops
from app.system.equipment_animation import activity_frame, FRAME_COUNT, report_screen_content, project_report_screen

ASSETS = Path(__file__).resolve().parents[1] / 'app' / 'assets'
SOURCES = {
    'centrifuge': ('centrifuge/centrifuge_closed.png', 1),
    'chemistry_analyzer': ('analyzer/chemistry_analyzer.png', 5),
    'specimens_reception_station': ('reception/specimens_reception_station.png', 1),
}


def refined_analyzer_atlas(base):
    """Import the illustrated sampling cycle with the exterior locked in place."""
    from build_compact_analyzer import build
    return build()


def refined_centrifuge_atlas(base):
    """Animate shaded rotor artwork while keeping the closed lid and body fixed."""
    atlas = Image.new('RGBA', (base.width * FRAME_COUNT, base.height))
    mask = Image.new('L', base.size)
    ImageDraw.Draw(mask).ellipse((38, 80, 87, 105), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(.6))
    bounds = base.getchannel('A').point(lambda alpha: 255 if alpha > 64 else 0).getbbox()
    left, top, right, bottom = bounds
    with Image.open(ASSETS / 'centrifuge/animation-source/running_grid.png') as grid:
        for index in range(FRAME_COUNT):
            col, row = index % 4, index // 4
            cell = grid.crop((col * grid.width // 4, row * grid.height // 2,
                              (col + 1) * grid.width // 4, (row + 1) * grid.height // 2)).convert('RGBA')
            # Extract only the inspection window; casing variations in the
            # generated sheet must never leak into the animated rotor patch.
            rotor = cell.crop((round(cell.width * .30), round(cell.height * .16),
                               round(cell.width * .72), round(cell.height * .41)))
            rotor = rotor.resize((58, 34), Image.Resampling.LANCZOS)
            aligned = base.copy()
            aligned.alpha_composite(rotor, (34, 75))
            frame = Image.composite(aligned, base, mask)
            atlas.alpha_composite(frame, (index * base.width, 0))
    return atlas


def empty_station_base(folder, size):
    filename = 'blank_monitor_station.png' if folder == 'reporting' else 'empty_station.png'
    with Image.open(ASSETS / folder / 'animation-source' / filename) as source:
        body = source.convert('RGBA')
    body = body.crop(body.getchannel('A').point(lambda a: 255 if a > 64 else 0).getbbox())
    body.thumbnail((size[0] - 20, size[1] - 20), Image.Resampling.LANCZOS)
    base = Image.new('RGBA', size)
    base.alpha_composite(body, ((size[0] - body.width) // 2, size[1] - body.height))
    return base


def staffed_reception_atlas():
    """Build the compact reception stand with independently rendered staff."""
    from build_reception_station import build
    return build()


def clean_staff_alpha(image):
    """Remove isolated background specks without trimming the hands or padding."""
    alpha = image.getchannel('A')
    pixels = alpha.load()
    remaining = {(x, y) for y in range(image.height) for x in range(image.width) if pixels[x, y] > 32}
    components = []
    while remaining:
        seed = remaining.pop()
        found, pending = {seed}, [seed]
        while pending:
            x, y = pending.pop()
            for point in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if point in remaining:
                    remaining.remove(point)
                    found.add(point)
                    pending.append(point)
        components.append(found)
    mask = Image.new('L', image.size)
    draw = ImageDraw.Draw(mask)
    for component in components:
        if len(component) >= 16:
            for point in component:
                draw.point(point, fill=255)
    image.putalpha(ImageChops.multiply(alpha, mask.filter(ImageFilter.MaxFilter(3))))
    return image


def staff_atlases():
    sources = {'processing': 'working_hands_grid.png', 'registration': 'registration_grid.png',
               'reporting': 'reporting_grid.png', 'validating': 'validating_grid.png'}
    for activity, filename in sources.items():
        with Image.open(ASSETS / 'staff/animation-source' / filename) as grid:
            for row, gender in enumerate(('female', 'male')):
                cells = [grid.crop((col * grid.width // 4, row * grid.height // 2,
                                    (col + 1) * grid.width // 4, (row + 1) * grid.height // 2)).convert('RGBA')
                         for col in range(4)]
                bounds = [cell.getchannel('A').point(lambda a: 255 if a > 64 else 0).getbbox() for cell in cells]
                box = (min(b[0] for b in bounds), min(b[1] for b in bounds),
                       max(b[2] for b in bounds), max(b[3] for b in bounds))
                size = (round((box[2] - box[0]) * 168 / (box[3] - box[1])), 168)
                frames = []
                for cell in cells:
                    frame = Image.new('RGBA', (100, 176))
                    body = clean_staff_alpha(cell.crop(box).resize(size, Image.Resampling.LANCZOS))
                    frame.alpha_composite(body, ((100 - size[0]) // 2, 176 - size[1]))
                    frames.append(frame)
                atlas = Image.new('RGBA', (100 * FRAME_COUNT, 176))
                for index, pose in enumerate((0, 1, 2, 3, 2, 1, 0, 1)):
                    atlas.alpha_composite(frames[pose], (index * 100, 0))
                atlas.save(ASSETS / 'staff' / f'{gender}_{activity}.png')


def reporting_station_atlas():
    """Build the standing computer with a scrolling screen and no printer."""
    from build_reporting_station import build
    return build()


def build():
    destination = ASSETS / 'animation'
    destination.mkdir(exist_ok=True)
    staff_atlases()
    reporting_station_atlas().save(destination / "reporting_station_running.png")
    for kind, (filename, scale) in SOURCES.items():
        if kind == 'specimens_reception_station':
            staffed_reception_atlas().save(destination / f'{kind}_running.png')
            continue
        with Image.open(ASSETS / filename) as source:
            base = source.convert('RGBA')
            base = base.resize(((base.width + scale - 1) // scale,
                                (base.height + scale - 1) // scale), Image.Resampling.NEAREST)
        if kind == 'centrifuge':
            atlas = refined_centrifuge_atlas(base)
        elif kind == 'chemistry_analyzer':
            atlas = refined_analyzer_atlas(base)
        else:
            atlas = Image.new('RGBA', (base.width * FRAME_COUNT, base.height))
            for frame in range(FRAME_COUNT):
                atlas.alpha_composite(activity_frame(base, kind, frame), (frame * base.width, 0))
        atlas.save(destination / f'{kind}_running.png')


if __name__ == '__main__':
    build()
