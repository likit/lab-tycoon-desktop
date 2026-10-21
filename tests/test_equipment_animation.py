"""Equipment sprite assets and playback frame selection."""
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image
from app.system.equipment_animation import animation_frame, FRAME_COUNT, report_screen_content
from app.system.floor_playback import FloorPlayback
from app.system.floor_view import EQUIPMENT_CATALOG, FloorViewRenderer

ASSETS = Path(__file__).resolve().parents[1] / 'app' / 'assets'


class EquipmentAnimationTests(unittest.TestCase):
    def test_play_pause_resume_stop_frame_selection(self):
        playback = FloorPlayback()
        self.assertIsNone(animation_frame(playback.state, playback.elapsed))
        playback.play()
        playback.advance(.3)
        frame = animation_frame(playback.state, playback.elapsed)
        self.assertEqual(frame, 2)
        playback.pause()
        playback.advance(10)
        self.assertEqual(animation_frame(playback.state, playback.elapsed), frame)
        playback.play()
        playback.advance(.25)
        self.assertEqual(animation_frame(playback.state, playback.elapsed), 4)
        playback.stop()
        self.assertIsNone(animation_frame(playback.state, playback.elapsed))

    def test_all_atlases_have_eight_distinct_anchored_transparent_frames(self):
        for kind, equipment in EQUIPMENT_CATALOG.items():
            with self.subTest(kind=kind), Image.open(ASSETS / equipment.sprite) as idle:
                scale = equipment.sprite_subsample
                size = ((idle.width + scale - 1) // scale, (idle.height + scale - 1) // scale)
                with Image.open(ASSETS / 'animation' / f'{kind}_running.png') as atlas:
                    self.assertEqual(atlas.size, (size[0] * FRAME_COUNT, size[1]))
                    self.assertEqual(atlas.mode, 'RGBA')
                    frames = [atlas.crop((i*size[0], 0, (i+1)*size[0], size[1])) for i in range(FRAME_COUNT)]
                    self.assertGreater(len({frame.tobytes() for frame in frames}), 3)
                    for frame in frames:
                        self.assertEqual(frame.getchannel('A').getextrema()[0], 0)

    def test_analyzer_exterior_stays_fixed_between_frames(self):
        with Image.open(ASSETS / 'animation/chemistry_analyzer_running.png') as atlas:
            width = atlas.width // FRAME_COUNT
            # Computer, intake tray and lower casing are outside moving panels.
            regions = [(0, 0, 110, 75), (265, 135, 380, 250), (70, 180, 260, 250)]
            for region in regions:
                original = atlas.crop(region).tobytes()
                for frame in range(1, FRAME_COUNT):
                    left, top, right, bottom = region
                    self.assertEqual(atlas.crop((left + frame * width, top,
                                                 right + frame * width, bottom)).tobytes(), original)

    def test_staffed_reception_idle_and_head_are_stable(self):
        with Image.open(ASSETS / 'reception/specimens_reception_station_staff.png') as idle:
            self.assertEqual(idle.size, (300, 270))
            with Image.open(ASSETS / 'animation/specimens_reception_station_running.png') as atlas:
                self.assertEqual(atlas.crop((0, 0, 300, 270)).tobytes(), idle.tobytes())
                for index in range(FRAME_COUNT):
                    frame = atlas.crop((index * 300, 0, (index + 1) * 300, 270))
                    for region in ((0, 0, 300, 75), (0, 190, 300, 270)):
                        self.assertEqual(frame.crop(region).tobytes(), idle.crop(region).tobytes())

    def test_only_report_rows_scroll_up_with_seamless_loop(self):
        frames = [report_screen_content(index) for index in range(FRAME_COUNT)]
        for index, frame in enumerate(frames):
            self.assertEqual(frame.crop((0, 0, 180, 30)).tobytes(), frames[0].crop((0, 0, 180, 30)).tobytes())
            next_frame = frames[(index + 1) % FRAME_COUNT]
            self.assertEqual(frame.crop((27, 33, 172, 111)).tobytes(),
                             next_frame.crop((27, 31, 172, 109)).tobytes())

    def test_reporting_station_types_scrolls_and_prints_with_fixed_exterior(self):
        with Image.open(ASSETS / 'animation/reporting_station_running.png') as atlas:
            frames = [atlas.crop((index * 280, 0, (index + 1) * 280, 270)) for index in range(FRAME_COUNT)]
            for region in ((110, 100, 161, 133), (135, 60, 180, 80), (190, 85, 265, 150)):
                self.assertGreater(len({frame.crop(region).tobytes() for frame in frames}), 1)
            for region in ((0, 0, 280, 30), (0, 175, 280, 270)):
                self.assertEqual(len({frame.crop(region).tobytes() for frame in frames}), 1)

    def test_reporting_station_printer_body_and_anchor_do_not_shift(self):
        with Image.open(ASSETS / 'reporting/reporting_station.png') as idle, Image.open(
                ASSETS / 'animation/reporting_station_running.png') as atlas:
            for index in range(FRAME_COUNT):
                frame = atlas.crop((index * 280, 0, (index + 1) * 280, 270))
                self.assertEqual(frame.getbbox(), idle.getbbox())
                # Printer casing, monitor top border, cabinet and chair stay fixed.
                for region in ((200, 124, 265, 154), (120, 20, 210, 34), (0, 175, 280, 270)):
                    self.assertEqual(frame.crop(region).tobytes(), idle.crop(region).tobytes())

    def test_flow_icons_load_detailed_assets_and_cache_tk_images(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.assets_dir = ASSETS
        renderer.canvas = SimpleNamespace(master=object())
        renderer._flow_icons = {}
        with patch('app.system.floor_view.ImageTk.PhotoImage', side_effect=lambda image, **kw: image.copy()) as photo:
            for kind in ('specimens', 'data'):
                icon = renderer._flow_icon(kind)
                self.assertEqual(icon.mode, 'RGBA')
                self.assertLessEqual(icon.width, 32)
                self.assertLessEqual(icon.height, 44)
                self.assertEqual(icon.getchannel('A').getextrema()[0], 0)
                self.assertIs(renderer._flow_icon(kind), icon)
            self.assertEqual(photo.call_count, 2)

    def test_rendered_frames_cache_selected_and_normal_variants(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.assets_dir = ASSETS
        renderer.canvas = SimpleNamespace(master=object())
        renderer._activity_images = {}
        with patch('app.system.floor_view.ImageTk.PhotoImage', side_effect=lambda image, **kw: image.copy()) as photo:
            normal = renderer._activity_image(EQUIPMENT_CATALOG['centrifuge'], 2, False)
            selected = renderer._activity_image(EQUIPMENT_CATALOG['centrifuge'], 2, True)
            self.assertIs(renderer._activity_image(EQUIPMENT_CATALOG['centrifuge'], 2, False), normal)
            self.assertNotEqual(normal.tobytes(), selected.tobytes())
            self.assertEqual(photo.call_count, 2)
