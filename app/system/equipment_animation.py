"""Cached sprite-frame composition for illustrative equipment activity."""
from PIL import Image, ImageDraw

FRAME_COUNT = 8
FRAMES_PER_SECOND = 8


def animation_frame(state: str, elapsed: float) -> int | None:
    return None if state == "stopped" else int(elapsed * FRAMES_PER_SECOND) % FRAME_COUNT


def activity_frame(base: Image.Image, equipment_type: str, frame: int) -> Image.Image:
    """Compose activity on fixed-size sprites, preserving their floor anchors."""
    image = base.convert("RGBA").copy()
    draw = ImageDraw.Draw(image)
    width, height = image.size

    def point(x, y):
        return round(x * width), round(y * height)

    if equipment_type == "specimens_reception_station":
        # Scanner beam pulses over the specimen tray.
        if frame < 4:
            draw.line((*point(.73, .40 + frame * .006), *point(.88, .46 + frame * .006)),
                      fill="#f87171", width=2)
        # Label printer feeds a short strip, then starts a new label.
        length = .018 + (frame % 4) * .009
        draw.polygon([point(.637, .39), point(.674, .40),
                      point(.674, .40 + length), point(.637, .39 + length)],
                     fill="white", outline="#94a3b8")
    return image


def report_screen_content(frame: int) -> Image.Image:
    """A fixed application window with only its result rows scrolling upward."""
    image = Image.new('RGBA', (180, 120), '#edf3f8')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 179, 15), fill='#2877a8')
    draw.rectangle((9, 5, 83, 9), fill='#e0f2fe')
    draw.rectangle((0, 16, 20, 119), fill='#d8e3ec')
    draw.rectangle((27, 20, 171, 25), fill='#91a7ba')
    body = Image.new('RGBA', (145, 80), '#ffffff')
    rows = ImageDraw.Draw(body)
    offset = (frame % FRAME_COUNT) * 2
    # Repeat every 16 pixels so wrapping frame 7 to 0 continues upward.
    for y in range(-16 - offset, 96, 16):
        rows.rectangle((5, y + 4, 66, y + 6), fill='#879caf')
        rows.rectangle((78, y + 4, 106, y + 6), fill='#a5b4c2')
        rows.rectangle((120, y + 3, 137, y + 8), fill='#55a6a4')
        rows.line((4, y + 13, 140, y + 13), fill='#e2e8f0')
    image.alpha_composite(body, (27, 31))
    draw.rectangle((174, 30, 176, 111), fill='#c4d0db')
    return image


def project_report_screen(frame, size, corners):
    """Map content into the monitor's inner quadrilateral and clip every pixel."""
    import numpy as np
    from PIL import ImageChops
    content = report_screen_content(frame)
    source = [(0, 0), (content.width - 1, 0),
              (content.width - 1, content.height - 1), (0, content.height - 1)]
    matrix, values = [], []
    for (x, y), (u, v) in zip(corners, source):
        matrix.extend(((x, y, 1, 0, 0, 0, -u*x, -u*y),
                       (0, 0, 0, x, y, 1, -v*x, -v*y)))
        values.extend((u, v))
    coefficients = np.linalg.solve(np.array(matrix, dtype=float), np.array(values, dtype=float))
    layer = content.transform(size, Image.Transform.PERSPECTIVE, coefficients,
                              resample=Image.Resampling.BICUBIC)
    clip = Image.new('L', size)
    ImageDraw.Draw(clip).polygon(corners, fill=255)
    layer.putalpha(ImageChops.multiply(layer.getchannel('A'), clip))
    return layer
