"""Equipment sprite assets and playback frame selection."""
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from PIL import Image, ImageDraw, ImageChops
from app.system.equipment_animation import (animation_frame, FRAME_COUNT, report_screen_content, project_report_screen,
    equipment_frame_count, equipment_animation_frame, reception_delivery_pose)
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
        self.assertEqual(frame, 1)
        playback.pause()
        playback.advance(10)
        self.assertEqual(animation_frame(playback.state, playback.elapsed), frame)
        playback.play()
        playback.advance(.25)
        self.assertEqual(animation_frame(playback.state, playback.elapsed), 2)
        playback.stop()
        self.assertIsNone(animation_frame(playback.state, playback.elapsed))

    def test_all_atlases_have_distinct_anchored_transparent_frames(self):
        for kind, equipment in EQUIPMENT_CATALOG.items():
            with self.subTest(kind=kind), Image.open(ASSETS / equipment.sprite) as idle:
                scale = equipment.sprite_subsample
                size = ((idle.width + scale - 1) // scale, (idle.height + scale - 1) // scale)
                with Image.open(ASSETS / 'animation' / f'{kind}_running.png') as atlas:
                    self.assertEqual(atlas.size, (size[0] * equipment_frame_count(kind), size[1]))
                    self.assertEqual(atlas.mode, 'RGBA')
                    frames = [atlas.crop((i*size[0], 0, (i+1)*size[0], size[1])) for i in range(equipment_frame_count(kind))]
                    self.assertGreater(len({frame.tobytes() for frame in frames}), 3)
                    for frame in frames:
                        self.assertEqual(frame.getchannel('A').getextrema()[0], 0)

    def test_analyzer_exterior_stays_fixed_between_frames(self):
        with Image.open(ASSETS / 'animation/chemistry_analyzer_running.png') as atlas:
            width = atlas.width // FRAME_COUNT
            # Lid, cabinet doors and plinth remain fixed around the pipette.
            regions = [(0, 0, width, 68), (0, 154, width, atlas.height),
                       (0, 0, 50, atlas.height), (200, 0, width, atlas.height)]
            for region in regions:
                original = atlas.crop(region).tobytes()
                for frame in range(1, FRAME_COUNT):
                    left, top, right, bottom = region
                    self.assertEqual(atlas.crop((left + frame * width, top,
                                                 right + frame * width, bottom)).tobytes(), original)

    def test_analyzer_arm_travels_between_decks_and_dips(self):
        with Image.open(ASSETS / 'animation/chemistry_analyzer_running.png') as atlas:
            width = atlas.width // FRAME_COUNT
            frames = [atlas.crop((i * width, 0, (i + 1) * width, atlas.height)).convert('RGBA')
                      for i in range(FRAME_COUNT)]
            travel = ImageChops.difference(frames[0], frames[4]).convert('RGB').getbbox()
            self.assertIsNotNone(travel)
            self.assertGreater(travel[2] - travel[0], 45)
            self.assertGreater(travel[3] - travel[1], 15)
            for raised, lowered in ((0, 1), (4, 5)):
                dip = ImageChops.difference(frames[raised], frames[lowered]).convert('RGB').getbbox()
                self.assertIsNotNone(dip)
                self.assertGreater(dip[3] - dip[1], 6)

    def test_pneumatic_reception_moves_only_carrier_and_screen(self):
        with Image.open(ASSETS / 'reception/specimens_reception_station_empty.png') as idle:
            self.assertEqual(idle.size, (220, 230))
            with Image.open(ASSETS / 'animation/specimens_reception_station_running.png') as atlas:
                frames = [atlas.crop((index * 220, 0, (index + 1) * 220, 230)) for index in range(32)]
                for frame in frames:
                    # Keyboard, rack, scanner and pedestal are fixed.
                    for region in ((40, 81, 140, 120), (60, 122, 140, 218), (0, 220, 220, 230)):
                        self.assertEqual(frame.crop(region).tobytes(), idle.crop(region).tobytes())
                self.assertGreater(len({frame.crop((150, 89, 165, 196)).tobytes() for frame in frames}), 20)
                self.assertGreater(len({frame.crop((70, 20, 112, 61)).tobytes() for frame in frames}), 3)

    def test_pneumatic_delivery_rises_smoothly_and_holds_when_paused(self):
        poses = [reception_delivery_pose(frame) for frame in range(32)]
        self.assertEqual(poses[0][0], 0)
        self.assertEqual(poses[21][0], 1)
        self.assertTrue(all(poses[i][0] <= poses[i + 1][0] for i in range(21)))
        self.assertLess(poses[1][0] - poses[0][0], poses[10][0] - poses[9][0])
        self.assertEqual(poses[31][1], 0)
        kind = 'specimens_reception_station'
        self.assertEqual(equipment_animation_frame(kind, 'playing', .5), 8)
        self.assertEqual(equipment_animation_frame(kind, 'paused', .5), 8)
        self.assertIsNone(equipment_animation_frame(kind, 'stopped', .5))
        self.assertEqual(animation_frame('playing', .5), 2)

    def test_reception_uses_32_frame_atlas_and_arrival_relative_clock(self):
        from app.system.floor_view import FloorPlan, EquipmentInstance
        from app.system.model import Workflow
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.assets_dir = ASSETS
        renderer.canvas = SimpleNamespace(master=object())
        renderer._activity_images = {}
        equipment = EQUIPMENT_CATALOG['specimens_reception_station']
        with patch('app.system.floor_view.ImageTk.PhotoImage', side_effect=lambda image, **kw: image.copy()):
            self.assertEqual(renderer._activity_image(equipment, 31, False).size, (220, 230))
        renderer.floor_plan = FloorPlan()
        instance = EquipmentInstance('RECP-001', 'specimens_reception_station', 3, 3)
        renderer.floor_plan.equipment_instances.append(instance)
        renderer.workflow = Workflow()
        node = renderer.workflow.create_node('Reception', kind='equipment',
                                              metadata={'equipment_id': 'RECP-001'})
        renderer.live_counts = {node.node_id: {'active': 0, 'last_arrived': 0}}
        renderer.playback = FloorPlayback()
        renderer.playback.state = 'paused'
        renderer.playback.elapsed = 100  # Reopening must not restart or loop delivery.
        renderer.live_session = Mock(speed=.05, equipment_types={'RECP-001': 'specimens_reception_station'})
        renderer.live_session.snapshot.return_value = {'state': 'paused', 'time': .05}
        renderer._activity_image = Mock(return_value='arrival-frame')
        self.assertEqual(renderer._instance_image(instance), 'arrival-frame')
        renderer._activity_image.assert_called_once_with(equipment, 16, False)

    def test_only_report_rows_scroll_up_with_seamless_loop(self):
        frames = [report_screen_content(index) for index in range(FRAME_COUNT)]
        for index, frame in enumerate(frames):
            self.assertEqual(frame.crop((0, 0, 180, 30)).tobytes(), frames[0].crop((0, 0, 180, 30)).tobytes())
            next_frame = frames[(index + 1) % FRAME_COUNT]
            self.assertEqual(frame.crop((27, 33, 172, 111)).tobytes(),
                             next_frame.crop((27, 31, 172, 109)).tobytes())

    def test_standing_reporting_station_animates_only_its_screen(self):
        with Image.open(ASSETS / 'reporting/reporting_station_empty.png') as idle, Image.open(
                ASSETS / 'animation/reporting_station_running.png') as atlas:
            self.assertEqual(idle.size, (160, 210))
            frames = [atlas.crop((index * 160, 0, (index + 1) * 160, 210))
                      for index in range(FRAME_COUNT)]
            self.assertGreater(len({frame.crop((40, 15, 95, 78)).tobytes() for frame in frames}), 3)
            for frame in frames:
                self.assertEqual(frame.getbbox(), idle.getbbox())
                # Keyboard, pedestal and floor base do not animate or emit paper.
                self.assertEqual(frame.crop((0, 85, 160, 210)).tobytes(),
                                 idle.crop((0, 85, 160, 210)).tobytes())

    def test_staff_sprites_are_separate_transparent_working_poses(self):
        for gender in ('female', 'male'):
            for activity in ('registration', 'processing', 'reporting', 'validating'):
                self._check_staff_atlas(gender, activity)

    def _check_staff_atlas(self, gender, activity):
            with Image.open(ASSETS / 'staff' / f'{gender}_{activity}.png') as atlas:
                self.assertEqual(atlas.mode, 'RGBA')
                self.assertEqual(atlas.size, (100 * FRAME_COUNT, 176))
                frames = [atlas.crop((index * 100, 0, (index + 1) * 100, 176)) for index in range(FRAME_COUNT)]
                self.assertGreater(len({frame.tobytes() for frame in frames}), 2)
                self.assertEqual(frames[0].getchannel('A').getextrema()[0], 0)

    def test_reception_registration_alternates_typing_and_scanning(self):
        from app.system.floor_view import FloorPlan, EquipmentInstance
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.floor_plan = FloorPlan()
        renderer.floor_plan.equipment_instances.append(
            EquipmentInstance('RECP-001', 'specimens_reception_station', 3, 3))
        renderer.live_staff = [dict(equipment_id='RECP-001', activity='registration',
                                   staff_count=1, gender='female')]
        renderer.canvas = Mock()
        renderer.origin_x, renderer.origin_y = 500, 28
        renderer.playback = FloorPlayback()
        renderer.playback.state = 'paused'
        typing, scanning = object(), object()
        renderer._staff_images = {('female', 'reporting', 0): typing,
                                  ('female', 'registration', 4): scanning}
        renderer.render_staff()
        self.assertIs(renderer.canvas.create_image.call_args.kwargs['image'], typing)
        renderer.playback.elapsed = 1.0
        renderer.render_staff()
        self.assertIs(renderer.canvas.create_image.call_args.kwargs['image'], scanning)

    def test_staff_badge_groups_actual_allocations_at_shared_station(self):
        from app.system.floor_view import FloorPlan, EquipmentInstance
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.floor_plan = FloorPlan()
        renderer.floor_plan.equipment_instances.append(EquipmentInstance('REPORT-001', 'reporting_station', 3, 3))
        renderer.live_staff = [dict(equipment_id='REPORT-001', node_id='reporting', staff_count=2, gender='female'),
                               dict(equipment_id='REPORT-001', node_id='validating', staff_count=1, gender='male')]
        renderer.canvas = Mock()
        renderer.origin_x, renderer.origin_y = 500, 28
        renderer.playback = FloorPlayback()
        renderer.playback.state = 'paused'
        renderer._staff_images = {('female', 'processing', 0): object()}
        renderer.render_staff()
        renderer.canvas.create_image.assert_called_once()
        self.assertEqual(renderer.canvas.create_text.call_args_list[0].kwargs['text'], '3')
        renderer.live_staff = []
        renderer.canvas.reset_mock()
        renderer.render_staff()
        renderer.canvas.delete.assert_called_once_with('attending-staff')
        renderer.canvas.create_image.assert_not_called()

    def test_centrifuge_size_is_consistent_across_display_states(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.assets_dir = ASSETS
        renderer.canvas = SimpleNamespace(master=object())
        renderer._equipment_images = {}
        renderer._activity_images = {}
        renderer._selected_images = {}
        renderer._preview_images = {}
        equipment = EQUIPMENT_CATALOG['centrifuge']
        with Image.open(ASSETS / equipment.sprite) as raw:
            expected = (round(raw.width * .8), round(raw.height * .8))
        with patch('app.system.floor_view.ImageTk.PhotoImage', side_effect=lambda image, **kw: image.copy()):
            images = [renderer._equipment_image(equipment), renderer._selected_image(equipment),
                      renderer._preview_image(equipment), renderer._activity_image(equipment, 2, False),
                      renderer._activity_image(equipment, 2, True)]
        self.assertTrue(all(image.size == expected for image in images))

    def test_shared_station_renders_distinct_reporting_and_validating_staff(self):
        from app.system.floor_view import FloorPlan, EquipmentInstance
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.floor_plan = FloorPlan()
        renderer.floor_plan.equipment_instances.append(EquipmentInstance('REPORT-001', 'reporting_station', 3, 3))
        renderer.live_staff = [dict(equipment_id='REPORT-001', node_id='reporting', staff_count=1, gender='female', activity='reporting'),
                               dict(equipment_id='REPORT-001', node_id='validating', staff_count=1, gender='female', activity='validating')]
        renderer.canvas = Mock()
        renderer.origin_x, renderer.origin_y = 500, 28
        renderer.playback = FloorPlayback()
        renderer.playback.state = 'paused'
        reporting_sprite, validating_sprite = object(), object()
        renderer._staff_images = {('female', 'reporting', 0): reporting_sprite,
                                  ('female', 'validating', 0): validating_sprite}
        renderer.render_staff()
        calls = renderer.canvas.create_image.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0].kwargs['image'], reporting_sprite)
        self.assertIs(calls[1].kwargs['image'], validating_sprite)
        self.assertNotEqual(calls[0].args, calls[1].args)

    def test_queue_counts_use_green_text_on_dark_backing(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.canvas = Mock()
        renderer.canvas.create_text.return_value = 7
        renderer.canvas.bbox.return_value = (10, 20, 180, 35)
        renderer._draw_queue_badge(95, 28, {'queued': 3, 'active': 1, 'finished': 2})
        self.assertEqual(renderer.canvas.create_text.call_args.kwargs['fill'], '#4ade80')
        self.assertIn('Queue 3', renderer.canvas.create_text.call_args.kwargs['text'])
        self.assertEqual(renderer.canvas.create_rectangle.call_args.kwargs['fill'], '#172b27')
        renderer.canvas.tag_raise.assert_called_once_with(7)

    def test_projected_report_content_never_escapes_screen_polygon(self):
        corners = [(118, 33), (185, 54), (182, 99), (115, 78)]
        mask = Image.new('L', (280, 270))
        ImageDraw.Draw(mask).polygon(corners, fill=255)
        for frame in range(FRAME_COUNT):
            layer = project_report_screen(frame, (280, 270), corners)
            outside = ImageChops.multiply(layer.getchannel('A'), ImageChops.invert(mask))
            self.assertIsNone(outside.getbbox())
            self.assertGreater(layer.getchannel('A').getextrema()[1], 0)

    def test_specimen_asset_and_digital_pulse_cache_tk_images(self):
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

    def test_data_pulse_has_luminous_core_and_does_not_load_report_artwork(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.canvas = SimpleNamespace(master=object())
        renderer._flow_icons = {}
        with patch('app.system.floor_view.Image.open', side_effect=AssertionError('No paper asset')), \
             patch('app.system.floor_view.ImageTk.PhotoImage', side_effect=lambda image, **kw: image.copy()):
            pulse = renderer._flow_icon('data')
        self.assertEqual(pulse.getpixel((14, 14)), (240, 253, 255, 255))
        self.assertGreater(pulse.getpixel((7, 14))[3], 0)
        self.assertLess(pulse.getpixel((7, 14))[3], 255)

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
