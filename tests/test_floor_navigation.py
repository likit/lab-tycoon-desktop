"""Panning moves the viewport without turning a drag into an equipment click."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.system.floor_navigation import FloorPanController


class CanvasViewport:
    def __init__(self):
        self.x, self.y = 200, 100
        self.cursor = 'arrow'

    def cget(self, key):
        return self.cursor

    def configure(self, *, cursor):
        self.cursor = cursor

    def scan_mark(self, x, y):
        self.mark = (x, y, self.x, self.y)

    def scan_dragto(self, x, y, *, gain):
        mark_x, mark_y, start_x, start_y = self.mark
        self.x = max(0, start_x + (mark_x - x) * gain)
        self.y = max(0, start_y + (mark_y - y) * gain)


class FloorNavigationTests(unittest.TestCase):
    def setUp(self):
        self.canvas = CanvasViewport()
        self.click = Mock()
        self.can_pan = True
        self.pan = FloorPanController(self.canvas, self.click, lambda: self.can_pan)

    def event(self, x, y):
        return SimpleNamespace(x=x, y=y)

    def test_drag_moves_both_axes_at_pointer_speed_without_clicking(self):
        self.pan.press(self.event(300, 200))
        self.pan.drag(self.event(240, 160))
        self.assertEqual((self.canvas.x, self.canvas.y), (260, 140))
        self.assertEqual(self.canvas.cursor, 'fleur')
        self.pan.release(self.event(220, 150))
        self.assertEqual((self.canvas.x, self.canvas.y), (280, 150))
        self.assertEqual(self.canvas.cursor, 'arrow')
        self.click.assert_not_called()

    def test_click_with_small_pointer_jitter_still_inspects_equipment(self):
        self.pan.press(self.event(300, 200))
        self.pan.drag(self.event(302, 201))
        release = self.event(302, 201)
        self.pan.release(release)
        self.click.assert_called_once_with(release)
        self.assertEqual((self.canvas.x, self.canvas.y), (200, 100))

    def test_placement_or_move_mode_keeps_click_to_place_and_does_not_pan(self):
        self.can_pan = False
        press = self.event(300, 200)
        self.pan.press(press)
        self.can_pan = True  # Placing an item may finish placement immediately.
        self.pan.drag(self.event(200, 150))
        self.pan.release(self.event(200, 150))
        self.click.assert_called_once_with(press)
        self.assertEqual((self.canvas.x, self.canvas.y), (200, 100))

    def test_cancel_restores_cursor_and_suppresses_release_click(self):
        self.pan.press(self.event(300, 200))
        self.pan.drag(self.event(250, 160))
        self.assertTrue(self.pan.cancel())
        self.assertEqual(self.canvas.cursor, 'arrow')
        self.pan.release(self.event(250, 160))
        self.click.assert_not_called()

    def test_release_alone_with_large_displacement_counts_as_drag(self):
        self.pan.press(self.event(300, 200))
        self.pan.release(self.event(250, 160))
        self.assertEqual((self.canvas.x, self.canvas.y), (250, 140))
        self.click.assert_not_called()
