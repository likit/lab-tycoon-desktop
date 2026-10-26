"""Zoom must keep sprites, geometry, labels and click coordinates aligned."""
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from app.system.floor_navigation import ZoomCanvas
from app.system.floor_view import FloorViewRenderer, FloorPlan


class Viewport:
    def __init__(self):
        self.x, self.y = 200, 100
        self.region = (0, 0, 1340, 914)

    def configure(self, **options):
        self.region = options.get('scrollregion', self.region)

    def cget(self, key):
        return ' '.join(str(value) for value in self.region)

    def canvasx(self, x):
        return x + self.x

    def canvasy(self, y):
        return y + self.y

    def winfo_width(self):
        return 1000

    def winfo_height(self):
        return 700

    def xview_moveto(self, fraction):
        self.x = self.region[0] + fraction * (self.region[2] - self.region[0])

    def yview_moveto(self, fraction):
        self.y = self.region[1] + fraction * (self.region[3] - self.region[1])


class FloorZoomTests(unittest.TestCase):
    def test_zoom_keeps_the_same_logical_point_under_the_pointer(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.canvas = ZoomCanvas(Viewport())
        renderer.floor_plan = FloorPlan()
        renderer.origin_y = 202
        renderer.render = Mock()
        renderer.resize(1000)
        anchor = (350, 300)
        before = (renderer.canvas.canvasx(anchor[0]) - renderer.origin_x,
                  renderer.canvas.canvasy(anchor[1]) - renderer.origin_y)
        cell = renderer.cell_at_canvas_point(*anchor)
        for zoom in (.5, 2, 1):
            renderer.set_zoom(zoom, anchor=anchor)
            self.assertAlmostEqual(renderer.canvas.canvasx(anchor[0]) - renderer.origin_x, before[0])
            self.assertAlmostEqual(renderer.canvas.canvasy(anchor[1]) - renderer.origin_y, before[1])
            self.assertEqual(renderer.cell_at_canvas_point(*anchor), cell)

    def test_sprites_are_resampled_and_cached_at_the_current_zoom(self):
        data = BytesIO()
        Image.new('RGBA', (20, 10), '#ff000080').save(data, format='PNG')
        native = Mock()
        native.tk.call.return_value = data.getvalue()
        native.create_image.side_effect = [1, 2, 3]
        canvas = ZoomCanvas(native)
        canvas.set_zoom(2)
        with patch('app.system.floor_navigation.ImageTk.PhotoImage', side_effect=lambda image, **kw: image.copy()) as photo:
            canvas.create_image(10, 20, image='sprite')
            canvas.create_image(20, 30, image='sprite')
            photo.assert_called_once()
            self.assertEqual(canvas._visible_images[1].size, (40, 20))
            self.assertIs(canvas._visible_images[1], canvas._visible_images[2])
            canvas.set_zoom(.5)
            canvas.create_image(10, 20, image='sprite')
            self.assertEqual(canvas._visible_images[3].size, (10, 5))
        self.assertEqual(native.create_image.call_args.args, (5, 10))

    def test_hit_testing_and_bounds_use_logical_coordinates_after_zoom(self):
        native = Mock()
        native.canvasx.return_value = 400
        native.canvasy.return_value = 200
        native.bbox.return_value = (50, 100, 150, 200)
        canvas = ZoomCanvas(native)
        canvas.set_zoom(.5)
        self.assertEqual(canvas.canvasx(10), 800)
        self.assertEqual(canvas.canvasy(10), 400)
        self.assertEqual(canvas.bbox(1), (100, 200, 300, 400))
        canvas.find_overlapping(100, 200, 300, 400)
        native.find_overlapping.assert_called_once_with(50, 100, 150, 200)

    def test_small_zoom_keeps_live_labels_readable(self):
        native = Mock()
        canvas = ZoomCanvas(native)
        canvas.set_zoom(.1)
        canvas.create_text(100, 200, text='Waiting: 8', font=('Arial', 10, 'bold'))
        native.create_text.assert_called_once_with(10, 20, text='Waiting: 8', font=('Arial', 8, 'bold'))

    def test_rectangular_floor_extents_do_not_clip_the_leftmost_tiles(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.canvas = Mock()
        renderer.floor_plan = FloorPlan(rows=20, columns=4)
        renderer.origin_y = 202
        renderer.render = Mock()
        renderer.resize(1000)
        self.assertGreaterEqual(renderer.origin_x - 20 * 64, 0)
