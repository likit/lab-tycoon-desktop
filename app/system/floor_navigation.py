"""Canvas panning that preserves equipment clicks and placement gestures."""
from math import hypot
from collections import OrderedDict
import base64
from io import BytesIO

from PIL import Image, ImageTk


class ZoomCanvas:
    """Present logical floor coordinates while scaling Tk drawing and hit tests."""
    MIN_ZOOM = .05
    MAX_ZOOM = 2.0

    def __init__(self, widget):
        self.widget = widget
        self.zoom = 1.0
        self._images = OrderedDict()
        self._visible_images = {}

    def __getattr__(self, name):
        return getattr(self.widget, name)

    def set_zoom(self, value):
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(value)))
        self._images.clear()

    def _coordinates(self, values):
        return tuple(self._coordinates(value) if isinstance(value, (list, tuple)) else value * self.zoom
                     for value in values)

    def _options(self, options):
        result = dict(options)
        if 'width' in result:
            result['width'] = max(1, result['width'] * self.zoom)
        font = result.get('font')
        if isinstance(font, (tuple, list)) and len(font) > 1 and isinstance(font[1], int):
            size = max(8, round(abs(font[1]) * self.zoom)) * (-1 if font[1] < 0 else 1)
            result['font'] = (font[0], size, *font[2:])
        return result

    def _image(self, source):
        if self.zoom == 1 or not source:
            return source
        key = str(source)
        if key in self._images:
            self._images.move_to_end(key)
            return self._images[key]
        data = self.widget.tk.call(key, 'data', '-format', 'png')
        if isinstance(data, str):
            data = base64.b64decode(data)
        with Image.open(BytesIO(data)) as image:
            scaled = image.convert('RGBA').resize(
                (max(1, round(image.width * self.zoom)), max(1, round(image.height * self.zoom))),
                Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(scaled, master=self.widget)
        self._images[key] = photo
        if len(self._images) > 256:
            self._images.popitem(last=False)
        return photo

    def create_image(self, *coordinates, **options):
        options = dict(options)
        if 'image' in options:
            options['image'] = self._image(options['image'])
        item = self.widget.create_image(*self._coordinates(coordinates), **options)
        self._visible_images[item] = options.get('image')
        return item

    def itemconfigure(self, item, **options):
        if 'image' in options:
            options['image'] = self._image(options['image'])
            self._visible_images[item] = options['image']
        return self.widget.itemconfigure(item, **self._options(options))

    def create_text(self, *coordinates, **options):
        return self.widget.create_text(*self._coordinates(coordinates), **self._options(options))

    def create_line(self, *coordinates, **options):
        return self.widget.create_line(*self._coordinates(coordinates), **self._options(options))

    def create_rectangle(self, *coordinates, **options):
        return self.widget.create_rectangle(*self._coordinates(coordinates), **self._options(options))

    def create_oval(self, *coordinates, **options):
        return self.widget.create_oval(*self._coordinates(coordinates), **self._options(options))

    def create_polygon(self, *coordinates, **options):
        return self.widget.create_polygon(*self._coordinates(coordinates), **self._options(options))

    def configure(self, **options):
        if 'scrollregion' in options:
            options['scrollregion'] = self._coordinates(options['scrollregion'])
        return self.widget.configure(**options)

    def bbox(self, *items):
        bounds = self.widget.bbox(*items)
        return tuple(value / self.zoom for value in bounds) if bounds else None

    def canvasx(self, x):
        return self.widget.canvasx(x) / self.zoom

    def canvasy(self, y):
        return self.widget.canvasy(y) / self.zoom

    def find_overlapping(self, *coordinates):
        return self.widget.find_overlapping(*self._coordinates(coordinates))

    def delete(self, *items):
        for item in items:
            for item_id in self.widget.find_withtag(item):
                self._visible_images.pop(item_id, None)
        return self.widget.delete(*items)


class FloorPanController:
    DRAG_THRESHOLD = 5

    def __init__(self, canvas, on_click, can_pan):
        self.canvas = canvas
        self.on_click = on_click
        self.can_pan = can_pan
        self._start = None
        self._enabled = False
        self.dragging = False
        self._cursor = ''

    def press(self, event):
        self.cancel()
        self._start = (event.x, event.y)
        self._enabled = self.can_pan()
        if not self._enabled:
            # Placement/move mode retains its original click-to-place behavior.
            self.on_click(event)
        return 'break'

    def drag(self, event):
        if self._start is None or not self._enabled:
            return 'break'
        if not self.dragging:
            if hypot(event.x - self._start[0], event.y - self._start[1]) < self.DRAG_THRESHOLD:
                return 'break'
            self._cursor = self.canvas.cget('cursor')
            self.canvas.scan_mark(*self._start)
            self.canvas.configure(cursor='fleur')
            self.dragging = True
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        return 'break'

    def release(self, event):
        if self._start is None:
            return 'break'
        self.drag(event)
        click = self._enabled and not self.dragging
        self.cancel()
        if click:
            self.on_click(event)
        return 'break'

    def cancel(self):
        pressed = self._start is not None
        if self.dragging:
            self.canvas.configure(cursor=self._cursor)
        self._start = None
        self._enabled = False
        self.dragging = False
        return pressed
