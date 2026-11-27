"""Exercise the PySide6 controls, signals and timers."""
import time
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QEvent, QPoint, QSignalBlocker, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from main import App, render_target_card
from packaging_entry import run


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_stellarium_start_time_and_observation_date(self):
        for local, expected_time, expected_date in (
            ('2026-12-31T23:59:00', '23:59', date(2026, 12, 31)),
            ('2027-01-01T00:00:00', '24:00', date(2026, 12, 31)),
            ('2027-01-01T05:59:00', '29:59', date(2026, 12, 31)),
            ('2027-01-01T06:00:00', '06:00', date(2027, 1, 1)),
        ):
            with self.subTest(local=local):
                window = App({
                    'time': {'local': local},
                    'location': {'latitude': 35.68, 'longitude': 139.76},
                })
                window.startup_timer.stop()
                window.poll_timer.stop()
                self.assertEqual(window.v['start'].text(), expected_time)
                self.assertEqual(window.observation_date, expected_date)
                if expected_time.startswith(('24:', '29:')):
                    self.assertEqual(window.stellarium_plan_dates[window.v['url'].text()], expected_date)

    def test_executable_uses_shared_startup(self):
        with patch('sys.argv', ['SeestarPlanEditor.exe']), patch('main.run_application', return_value=0) as startup:
            self.assertEqual(run(), 0)
        startup.assert_called_once_with()


class FramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.app = App()
        self.app.startup_timer.stop()
        error_patch = patch.object(self.app, 'error', side_effect=lambda error: self.fail(str(error)))
        error_patch.start()
        self.addCleanup(error_patch.stop)
        self.app.hide()
        self.app.v['ra'].setText('5')
        self.app.v['dec'].setText('40')
        self.app.stellarium_frame_enabled = True

    def tearDown(self):
        self.app.frame_timer.stop()
        self.app.poll_timer.stop()
        self.app.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def sent_frame(self, change):
        with patch('main.draw_stellarium_frame') as draw:
            change()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                QApplication.processEvents()
                if draw.called and not self.app.busy:
                    self.sent_options = draw.call_args.kwargs
                    return draw.call_args.args
                time.sleep(0.01)
            self.fail('Framing change did not finish drawing')

    def test_angle_automatically_enables_rotation(self):
        self.assertFalse(self.app.framing.isChecked())
        args = self.sent_frame(lambda: self.app.v['angle'].setText('45'))
        self.assertTrue(self.app.framing.isChecked())
        self.assertEqual(args[3], 45)

    def test_startup_clears_previous_frames(self):
        with patch('main.clear_stellarium_frames') as clear:
            self.app.startup_timer.timeout.emit()
            deadline = time.monotonic() + 3
            while self.app.busy and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertFalse(self.app.busy)
            clear.assert_called_once_with('http://localhost:8090')

    def test_rotation_slider_and_text_stay_in_sync(self):
        args = self.sent_frame(lambda: self.app.angle_slider.setValue(-75))
        self.assertEqual(args[3], -75)
        self.assertEqual(self.app.v['angle'].text(), '-75')
        self.app.v['angle'].setText('120')
        self.assertEqual(self.app.angle_slider.value(), 120)

    def test_json_dialogs_round_trip_rotation_and_scale(self):
        self.app.v['angle'].setText('45')
        self.app.v['scale'].setText('1.5')
        self.add_target('M1', 30)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'plan.json')
            with patch('main.QFileDialog.getSaveFileName', return_value=(path, '')):
                self.app.save()
            saved = json.loads(Path(path).read_text(encoding='utf-8'))
            self.assertEqual(saved['list'][0]['mosaic'],
                             dict(angle=45, star_map_angle=45, scale=2.25))
            with patch('main.QFileDialog.getOpenFileName', return_value=(path, '')):
                self.app.load()
            self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
            self.assertEqual(self.app.v['scale'].text(), '1.5')
            self.assertEqual(self.app.angle_slider.value(), 45)
            self.assertFalse(self.app.form_dirty)

    def test_qr_dialog_has_image(self):
        self.app.show_qr('https://example.com/plan')
        dialog = self.app.findChild(QDialog)
        self.assertIsNotNone(dialog)
        labels = dialog.findChildren(QLabel)
        self.assertTrue(any(not label.pixmap().isNull() for label in labels))

    def test_scale_automatically_enables_framing(self):
        args = self.sent_frame(lambda: self.app.scale_slider.setValue(17))
        self.assertTrue(self.app.framing.isChecked())
        self.assertAlmostEqual(args[4], 2.89)
        self.assertEqual(self.app.v['scale'].text(), '1.7')
        self.app.v['scale'].setText('1.234')
        self.assertEqual(self.app.scale_slider.value(), 12)
        self.assertEqual(self.app.v['scale'].text(), '1.234')

    def test_disabling_resets_stellarium_rotation(self):
        self.sent_frame(lambda: self.app.v['angle'].setText('45'))
        args = self.sent_frame(lambda: self.app.framing.setChecked(False))
        self.assertEqual(args[3:5], (0, 1))

    def add_target(self, name, duration):
        self.app.v['name'].setText(name)
        self.app.v['ra'].setText('5')
        self.app.v['dec'].setText('40')
        self.app.v['duration'].setText(str(duration))
        self.app.commit()

    def test_commit_keeps_frame_after_clearing_form(self):
        self.app.v['name'].setText('M1')
        self.app.v['angle'].setText('45')
        self.app.v['scale'].setText('1.5')
        args = self.sent_frame(self.app.commit)
        self.assertEqual(args[1:5], (5, 40, 45, 2.25))
        self.assertEqual(self.app.v['ra'].text(), '')
        self.assertIsNone(self.app.editing)

    def test_clicking_empty_list_area_deselects_target(self):
        self.add_target('M1', 30)
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.show()
        QApplication.processEvents()
        QTest.mouseClick(self.app.tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(10, self.app.tree.viewport().height() - 5))
        self.assertEqual(self.app.tree.selectedItems(), [])
        self.assertIsNone(self.app.editing)

    def test_ctrl_and_shift_select_multiple_targets_and_remove_them(self):
        for number in range(1, 5):
            self.add_target(f'M{number}', 10 * number)
        self.app.show()
        QApplication.processEvents()
        tree = self.app.tree

        def click(row, modifier=Qt.KeyboardModifier.NoModifier):
            pos = tree.visualItemRect(tree.topLevelItem(row)).center()
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, modifier, pos)

        click(0)
        click(2, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(set(tree.selected_target_indices()), {0, 2})
        self.assertEqual(self.app.editing, 2)
        click(2)
        click(3, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(set(tree.selected_target_indices()), {2, 3})
        click(0, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(set(tree.selected_target_indices()), {0, 2, 3})
        self.app.remove()
        self.assertEqual([(t['target_name'], t['start_min']) for t in self.app.targets],
                         [('M2', 1260)])
        self.assertEqual(tree.selectedItems(), [])

    def test_fetch_starts_with_default_framing_after_editing_target(self):
        self.app.v['angle'].setText('45')
        self.app.v['scale'].setText('1.5')
        self.add_target('M1', 30)
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.assertEqual(self.app.angle_slider.value(), 45)
        self.assertEqual(self.app.v['scale'].text(), '1.5')

        def check_new_target():
            self.assertEqual(self.app.v['angle'].text(), '0')
            self.assertEqual(self.app.angle_slider.value(), 0)
            self.assertEqual(self.app.v['scale'].text(), '1.0')
            self.assertFalse(self.app.framing.isChecked())
            return QDialog.DialogCode.Rejected

        with patch.object(self.app, 'run_job', side_effect=lambda _, done: done(('M2', 6, 41))):
            with patch.object(self.app.target_dialog, 'exec', side_effect=check_new_target) as dialog:
                self.app.fetch()
                QApplication.processEvents()
        dialog.assert_called_once()
        self.assertEqual(self.app.targets[0]['mosaic'],
                         dict(angle=45, star_map_angle=45, scale=2.25))

    def test_fetch_previews_framing_changes_and_clears_frame_on_cancel(self):
        dialog = self.app.target_dialog
        drawn_frames = []

        def change_framing():
            if not dialog.isVisible() or self.app.busy or not drawn_frames:
                return
            if len(drawn_frames) == 1:
                self.app.angle_slider.setValue(45)
                self.app.v['scale'].setText('1.5')
            else:
                dialog.reject()

        timer = QTimer(self.app)
        timer.timeout.connect(change_framing)
        timer.start(25)
        timeout = QTimer(self.app)
        timeout.setSingleShot(True)
        timeout.timeout.connect(dialog.reject)
        timeout.start(3000)
        try:
            with patch('main.selected_object', return_value=('M1', 5, 40)):
                with patch('main.draw_stellarium_frame',
                           side_effect=lambda *args, **kwargs: drawn_frames.append(args[1:5])):
                    self.app.fetch()
                    deadline = time.monotonic() + 3
                    while len(drawn_frames) < 3 and time.monotonic() < deadline:
                        QApplication.processEvents()
                        QTest.qWait(10)
        finally:
            timer.stop()
            timeout.stop()

        self.assertEqual(drawn_frames, [(5, 40, 0, 1), (5, 40, 45, 2.25),
                                       (None, None, 0, 1)])

    def test_all_frames_off_does_not_redraw_deselected_target(self):
        self.add_target('M1', 30)
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.tree.clearSelection()
        self.app.frame_timer.stop()
        args = self.sent_frame(lambda: self.app.show_all.setChecked(True))
        self.assertEqual(args[1:3], (None, None))
        self.assertEqual(len(self.sent_options['frames']), 1)
        args = self.sent_frame(lambda: self.app.show_all.setChecked(False))
        self.assertEqual(args[1:3], (None, None))
        self.assertIsNone(self.sent_options['frames'])
        self.assertEqual(self.sent_options['previous_count'], 1)
        self.assertNotIn('start_min', self.sent_options)

    def test_loading_restores_suffixed_names_when_original_is_present(self):
        for name in ('Pleiadesx P1', 'Pleiadesx_2 P1', 'Other_2 P1'):
            self.add_target(name, 30)
        original = list(self.app.targets)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan.json'
            path.write_text(json.dumps(dict(plan_name='M1', list=original)), encoding='utf-8')
            with patch('main.QFileDialog.getOpenFileName', return_value=(str(path), '')), \
                    patch.object(self.app, 'confirm', return_value=True):
                self.app.load()
                path.write_text(json.dumps(dict(plan_name='M1', list=original[1:])), encoding='utf-8')
                self.app.append_plan()

        expected = ['Pleiadesx P1', 'Pleiadesx P1', 'Other_2 P1', 'Pleiadesx P1', 'Other_2 P1']
        self.assertEqual([self.app.tree.topLevelItem(i).text(0) for i in range(5)], expected)

    def test_appending_same_plan_twice_draws_only_the_selected_group(self):
        self.add_target('M1 P1', 30)
        self.add_target('M1 P2', 30)
        original = list(self.app.targets)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan.json'
            path.write_text(json.dumps(dict(plan_name='M1', list=original)), encoding='utf-8')
            with patch('main.QFileDialog.getOpenFileName', return_value=(str(path), '')):
                self.app.append_plan()
                self.app.append_plan()

        for first in (2, 4):
            self.sent_frame(lambda: self.app.tree.setCurrentItem(self.app.tree.topLevelItem(first + 1)))
            frames = self.sent_options['frames']
            self.assertEqual(frames, self.app.targets[first:first + 2])
            self.assertEqual([frame['target_name'] for frame in frames], ['M1 P1', 'M1 P2'])
            self.assertEqual(self.sent_options['selected_index'], 1)
            self.assertTrue(self.sent_options['dim_unselected'])

        self.sent_frame(lambda: self.app.show_all.setChecked(True))
        self.assertEqual(self.sent_options['frames'], self.app.targets)
        self.assertEqual(self.sent_options['selected_index'], 5)
        self.assertFalse(self.sent_options['dim_unselected'])

    def test_extended_mosaic_preview_matches_added_panels(self):
        dialog = self.app.target_dialog
        self.assertTrue(dialog.panel_overlap.isHidden())
        self.assertEqual(dialog.panel_overlap.value(), 20)
        self.app.v['name'].setText('M1')
        self.app.v['angle'].setText('20')
        self.app.v['scale'].setText('1.5')
        dialog.lp.setChecked(True)
        dialog.panel_rows.setValue(2)
        dialog.panel_columns.setValue(2)
        args = self.sent_frame(lambda: dialog.extended_mosaic.setChecked(True))
        self.assertFalse(dialog.panel_overlap.isHidden())
        self.assertEqual(args[1:3], (None, None))
        self.assertEqual(self.app.v['angle'].text(), '0')
        self.assertEqual(self.app.angle_slider.value(), 0)
        self.assertEqual(self.app.v['scale'].text(), '1.0')
        controls = (self.app.v['angle'], self.app.angle_slider, self.app.v['scale'], self.app.scale_slider)
        self.assertEqual(self.app.scale_slider.value(), 10)
        self.assertTrue(all(not control.isEnabled() for control in controls))
        self.assertEqual(len(self.sent_options['frames']), 4)
        self.sent_frame(lambda: dialog.panel_columns.setValue(3))
        self.assertEqual(self.sent_options['previous_count'], 4)
        default_preview = self.sent_options['frames']
        self.sent_frame(lambda: dialog.panel_overlap.setValue(40))
        preview = self.sent_options['frames']
        self.assertEqual(len(preview), 6)
        self.assertLess(abs(preview[0]['target_ra_dec'][1] - preview[1]['target_ra_dec'][1]),
                        abs(default_preview[0]['target_ra_dec'][1] - default_preview[1]['target_ra_dec'][1]))
        self.assertEqual([frame['panel_number'] for frame in preview], list(range(1, 7)))
        self.assertEqual(len({p['target_ra_dec'] for p in preview}), 6)

        self.sent_frame(self.app.commit)
        targets = self.app.targets
        self.assertEqual([t['target_name'] for t in targets],
                         [f'M1 P{i}' for i in range(1, 7)])
        self.assertEqual([t['start_min'] for t in targets], list(range(1260, 1440, 30)))
        self.assertEqual(len({t['target_id'] for t in targets}), 6)
        for frame, target in zip(preview, targets):
            self.assertEqual(tuple(target['target_ra_dec']), frame['target_ra_dec'])
            self.assertEqual(frame['mosaic'], dict(angle=0, scale=1))
            self.assertNotIn('mosaic', target)
            self.assertTrue(target['lp_filter'])
        self.assertFalse(dialog.extended_mosaic.isChecked())
        self.assertTrue(dialog.panel_overlap.isHidden())
        self.assertEqual(dialog.panel_overlap.value(), 20)
        self.assertTrue(all(control.isEnabled() for control in controls))
        self.assertEqual((dialog.panel_rows.value(), dialog.panel_columns.value()), (1, 1))

    def test_select_syncs_position_framing_and_target_start_without_fetch(self):
        self.add_target('M1', 30)
        self.add_target('M2', 45)
        self.app.targets[1]['mosaic'] = dict(angle=45, scale=2, star_map_angle=5)
        self.app.stellarium_frame_enabled = False
        def select():
            self.app.tree.setCurrentItem(self.app.tree.topLevelItem(1))
            self.app.select()
        args = self.sent_frame(select)
        self.assertEqual(args[1:4], (5, 40, 45))
        self.assertAlmostEqual(args[4], 2)
        self.assertEqual(args[5:7], (2.25, 4.0))
        self.assertNotIn('reference', self.app.v)
        self.assertEqual(self.sent_options['start_min'], 1290)
        self.assertIsNone(self.sent_options['plan_date'])
        self.assertEqual(self.app.v['start'].text(), '21:00')

    def test_schedule_updates_after_add_edit_start_change_and_delete(self):
        self.add_target('M1', 30)
        self.add_target('M2', 45)
        self.add_target('M3', 10)
        self.assertEqual([t['start_min'] for t in self.app.targets], [1260, 1290, 1335])
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.select()
        self.app.v['duration'].setText('60')
        self.app.commit()
        self.assertEqual([t['start_min'] for t in self.app.targets], [1260, 1320, 1365])
        self.app.v['start'].setText('23:30')
        self.assertEqual([t['start_min'] for t in self.app.targets], [1410, 1470, 1515])
        self.assertEqual(self.app.tree.topLevelItem(1).text(1), '24:30')
        self.assertFalse(self.app.form_dirty)
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(1))
        self.app.select()
        self.assertEqual(self.app.v['start'].text(), '23:30')
        self.app.remove()
        self.assertEqual([t['start_min'] for t in self.app.targets], [1410, 1470])

    def test_numeric_display_preserves_original_precision(self):
        self.add_target('M1', 30)
        self.app.targets[0]['target_ra_dec'] = [23.999999, -12.3456789]
        self.app.targets[0]['mosaic'] = dict(angle=12.3456789, scale=1, star_map_angle=0)
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.select()
        self.assertEqual(self.app.v['ra'].text(), "00° 00' 00''")
        self.assertEqual(self.app.v['dec'].text(), "-12° 20' 44''")
        self.assertEqual(self.app.v['angle'].text(), '12')
        self.app.commit()
        self.assertEqual(self.app.targets[0]['target_ra_dec'], [23.999999, -12.3456789])
        self.assertEqual(self.app.targets[0]['mosaic']['angle'], 12.3456789)
        self.assertEqual(self.app.tree.topLevelItem(0).text(4), '12')

    def test_sexagesimal_input_is_sent_and_saved_in_original_units(self):
        self.app.v['name'].setText('M1')
        def change():
            self.app.v['ra'].setText('12:30:00.000')
            self.app.v['dec'].setText('-00:30:00.000')
        args = self.sent_frame(change)
        self.assertEqual(args[1:3], (12.5, -0.5))
        self.app.commit()
        self.assertEqual(self.app.targets[0]['target_ra_dec'], [12.5, -0.5])

    def test_lp_filter_column_precedes_rotation_and_updates(self):
        self.add_target('M1', 30)
        self.app.lp.setChecked(True)
        self.add_target('M2', 45)
        columns = [self.app.tree.headerItem().text(i) for i in range(self.app.tree.columnCount())]
        self.assertEqual(columns.index('LPフィルター') + 1, columns.index('回転°'))
        self.assertEqual(self.app.tree.topLevelItem(0).text(3), 'OFF')
        self.assertEqual(self.app.tree.topLevelItem(1).text(3), 'ON')
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.select()
        self.assertFalse(self.app.lp.isChecked())
        self.app.lp.setChecked(True)
        self.app.commit()
        self.assertEqual(self.app.tree.topLevelItem(0).text(3), 'ON')

    def test_drag_reorders_times_and_preserves_unsaved_form(self):
        self.add_target('M1', 30)
        self.add_target('M2', 45)
        self.add_target('M3', 10)
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.tree.topLevelItem(1).setSelected(True)
        self.app.select()
        self.app.v['duration'].setText('60')
        selected = self.app.tree.selected_target_indices()
        with QSignalBlocker(self.app.tree):
            item = self.app.tree.takeTopLevelItem(0)
            self.app.tree.insertTopLevelItem(2, item)
            self.app.tree.setCurrentItem(item)
        self.app.reorder_targets(selected)
        QApplication.processEvents()
        self.assertEqual([t['target_name'] for t in self.app.targets], ['M2', 'M3', 'M1'])
        self.assertEqual([t['start_min'] for t in self.app.targets], [1260, 1305, 1315])
        self.assertEqual(self.app.editing, 2)
        self.assertEqual(self.app.tree.indexOfTopLevelItem(self.app.tree.currentItem()), 2)
        self.assertEqual(set(self.app.tree.selected_target_indices()), {0, 2})
        self.assertTrue(self.app.form_dirty)
        self.assertEqual(self.app.v['duration'].text(), '60')
        self.app.commit()
        self.assertEqual(self.app.targets[2]['duration_min'], 60)

    def test_all_frames_toggle_and_intensity_without_selected_position(self):
        self.add_target('M1', 30)
        self.add_target('M2', 45)
        def enable():
            self.app.show_all.setChecked(True)
            self.app.toggle_all_frames()
        self.sent_frame(enable)
        self.assertEqual(len(self.sent_options['frames']), 2)
        self.assertEqual(self.app.stellarium_frame_count, 2)
        self.sent_frame(lambda: self.app.v['intensity'].setText('80'))
        self.assertEqual(self.sent_options['intensity'], 80)
        def disable():
            self.app.show_all.setChecked(False)
            self.app.toggle_all_frames()
        self.sent_frame(disable)
        self.assertIsNone(self.sent_options['frames'])
        self.assertEqual(self.sent_options['previous_count'], 2)
        self.assertEqual(self.app.stellarium_frame_count, 0)

    def test_frame_style_sliders(self):
        self.assertEqual(self.app.intensity_slider.minimum(), 50)
        self.assertEqual(self.app.intensity_slider.maximum(), 100)
        self.sent_frame(lambda: self.app.intensity_slider.setValue(100))
        self.assertEqual(self.sent_options['intensity'], 100)
        self.sent_frame(lambda: self.app.thickness_slider.setValue(8))
        self.assertEqual(self.sent_options['thickness'], 8)

    def test_target_card_rendering_and_icon_updates(self):
        self.add_target('M1', 30)
        item = self.app.tree.topLevelItem(0)
        self.assertFalse(item.icon(0).isNull())
        pixmap = render_target_card(self.app.targets[0], date(2026, 11, 16))
        self.assertEqual(pixmap.width(), 75)
        self.assertEqual(pixmap.height(), 50)

        # Changing start time updates the icon
        self.app.v['start'].setText('22:00')
        item_updated = self.app.tree.topLevelItem(0)
        self.assertFalse(item_updated.icon(0).isNull())


if __name__ == '__main__':
    unittest.main()
