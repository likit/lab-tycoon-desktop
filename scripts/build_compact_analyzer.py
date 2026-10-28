"""Import a matched, illustrated sampling cycle without drawing extra machinery."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFilter
from app.system.equipment_animation import FRAME_COUNT

ASSETS = Path(__file__).resolve().parents[1] / 'app' / 'assets'
FRAME_SIZE = (256, 256)


def build():
    with Image.open(ASSETS / 'analyzer/animation-source/compact_sampling_cycle.png') as source:
        sheet = source.convert('RGBA')
    frames = []
    for index in range(FRAME_COUNT):
        col, row = index % 4, index // 4
        frame = sheet.crop((col * sheet.width // 4, row * sheet.height // 2,
                            (col + 1) * sheet.width // 4, (row + 1) * sheet.height // 2))
        # Identical crop/scale/anchor for every frame; never fit moving parts.
        frame = frame.resize((224, 224), Image.Resampling.LANCZOS)
        aligned = Image.new('RGBA', FRAME_SIZE)
        aligned.alpha_composite(frame, (16, 24))
        frames.append(aligned)
    base = frames[0]
    # Lock the entire exterior to frame zero. Only the illustrated work deck
    # changes, including the arm's own lighting, shading and attached needle.
    mask = Image.new('L', FRAME_SIZE)
    ImageDraw.Draw(mask).polygon([(56, 82), (102, 72), (155, 79), (194, 106),
                                 (194, 139), (135, 151), (55, 124)], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(.7))
    atlas = Image.new('RGBA', (FRAME_SIZE[0] * FRAME_COUNT, FRAME_SIZE[1]))
    for index, frame in enumerate(frames):
        composed = Image.composite(frame, base, mask)
        atlas.alpha_composite(composed, (index * FRAME_SIZE[0], 0))
    # The catalog's subsampling keeps idle, preview and running frames at 256px.
    base.resize((1280, 1280), Image.Resampling.LANCZOS).save(ASSETS / 'analyzer/chemistry_analyzer.png')
    atlas.save(ASSETS / 'animation/chemistry_analyzer_running.png')
    return atlas


if __name__ == '__main__':
    build()
