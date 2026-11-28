"""Exercise the PySide6 controls, signals and timers."""
import time
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QEvent, QPoint, QSignalBlocker, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel, QMenu

from main import App, render_target_card, run_application
from packaging_entry import run


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    # 旧INIのプランファイルパスを親フォルダーとして使い、保存先の変更が終了後のINIと次回起動に反映されること。
    def test_last_plan_directory_is_migrated_and_remembered_after_save(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'APPDATA': folder}):
            root = Path(folder)
            settings = root / 'SeestarPlanEditor' / 'settings.ini'
            settings.parent.mkdir()
            previous = root / 'previous'
            previous.mkdir()
            settings.write_text(f'[General]\nlast_plan_path={(previous / "old.json").as_posix()}\n', encoding='utf-8')
            window = App()
            window.startup_timer.stop()
            self.addCleanup(window.deleteLater)
            self.assertEqual(Path(window.last_plan_directory), previous)
            window.v['plan'].setText('M1')
            saved = root / 'M1.json'
            with (patch('main.build_plan', return_value={'plan_name': 'M1'}),
                  patch('main.QFileDialog.getSaveFileName', return_value=(str(saved), '')) as save_dialog):
                window.save()
            self.assertEqual(Path(save_dialog.call_args.args[2]), previous / 'M1.json')
            self.assertTrue(saved.is_file())
            self.assertTrue(window.close())
            reopened = App()
            reopened.startup_timer.stop()
            reopened.poll_timer.stop()
            self.addCleanup(reopened.deleteLater)
            self.assertEqual(Path(reopened.settings.value('last_plan_path', type=str)), root)
            self.assertEqual(Path(reopened.last_plan_directory), root)

    # Stellarium の日付またぎを確認する。00:00〜05:59 は前日の24:00〜29:59になり、撮影枠の基準日も前日になること。23:59 と06:00 は当日扱いになること。
    def test_stellarium_start_time_and_observation_date(self):
        for local, expected_time, expected_date in (
            ('2026-12-31T23:59:00', '23:59', date(2026, 12, 31)),
            ('2027-01-01T00:00:00', '24:00', date(2026, 12, 31)),
            ('2027-01-01T05:59:00', '29:59', date(2026, 12, 31)),
            ('2027-01-01T06:00:00', '06:00', date(2027, 1, 1)),
        ):
            with self.subTest(local=local), patch('main.sync_stellarium_time_enabled', return_value=True):
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

    # EXE の通常起動を確認する。共通起動処理が1回呼ばれ、その終了コード0が返ること。
    def test_executable_uses_shared_startup(self):
        with patch('sys.argv', ['SeestarPlanEditor.exe']), patch('main.run_application', return_value=0) as startup:
            self.assertEqual(run(), 0)
        startup.assert_called_once_with()

    # 同期設定の保存と起動時の反映を確認する。初期値はONで、キャンセル後もON、OKでOFFとONが保存されること。
    # ONは25:30、OFFは21:00で始まり、どちらも観測日と観測地点を反映すること。接続失敗時は警告を1回表示し、画面を作らず終了コード1を返すこと。
    def test_option_persists_only_when_accepted_and_controls_next_startup(self):
        status = {
            'time': {'local': '2027-01-01T01:30:00'},
            'location': {'latitude': 34.69, 'longitude': 135.50},
        }
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'APPDATA': folder}):
            window = App()
            window.startup_timer.stop()
            window.poll_timer.stop()
            self.addCleanup(window.deleteLater)
            other_menu = next(menu for menu in window.menuBar().findChildren(QMenu) if menu.title() == 'その他')
            self.assertNotIn('OPTION', [action.text() for action in other_menu.actions()])
            option = next(action for action in other_menu.actions() if action.text() == 'オプション')
            initial_states = []

            def toggle_option(button):
                dialog = QApplication.activeModalWidget()
                checkbox = dialog.findChild(QCheckBox)
                initial_states.append(checkbox.isChecked())
                checkbox.toggle()
                dialog.findChild(QDialogButtonBox).button(button).click()

            for button, sync_enabled in ((QDialogButtonBox.StandardButton.Cancel, True),
                                         (QDialogButtonBox.StandardButton.Ok, False),
                                         (QDialogButtonBox.StandardButton.Ok, True)):
                QTimer.singleShot(0, lambda: toggle_option(button))
                option.trigger()
                with (patch('main.QApplication') as application,
                      patch('main.App') as create_window,
                      patch('main.urlopen', return_value=io.StringIO(json.dumps(status))) as request):
                    application.return_value.exec.return_value = 0
                    self.assertEqual(run_application(), 0)
                    create_window.assert_called_once_with(status)
                    request.assert_called_once()
                started_window = App(status)
                started_window.startup_timer.stop()
                started_window.poll_timer.stop()
                self.addCleanup(started_window.deleteLater)
                self.assertEqual(started_window.v['start'].text(), '25:30' if sync_enabled else '21:00')
                self.assertEqual(started_window.observation_date, date(2026, 12, 31))
                self.assertEqual(started_window.observation_latitude, 34.69)
                self.assertEqual(started_window.observation_longitude, 135.50)
                with (patch('main.QApplication'),
                      patch('main.App') as create_window,
                      patch('main.QMessageBox.warning') as warning,
                      patch('main.urlopen', side_effect=OSError('Stellarium unavailable'))):
                    self.assertEqual(run_application(), 1)
                    warning.assert_called_once()
                    create_window.assert_not_called()
            self.assertEqual(initial_states, [True, True, False])


class FramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        settings_folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {'APPDATA': settings_folder}))
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

    # 角度の手入力を確認する。45°を入力すると回転が自動でONになり、撮影枠の描画に45°が渡ること。
    def test_angle_automatically_enables_rotation(self):
        self.assertFalse(self.app.framing.isChecked())
        args = self.sent_frame(lambda: self.app.v['angle'].setText('45'))
        self.assertTrue(self.app.framing.isChecked())
        self.assertEqual(args[3], 45)

    # 起動時の撮影枠消去を確認する。ローカルの Stellarium に消去処理を1回呼び出し、処理中の状態が解除されること。
    def test_startup_clears_previous_frames(self):
        with patch('main.clear_stellarium_frames') as clear:
            self.app.startup_timer.timeout.emit()
            deadline = time.monotonic() + 3
            while self.app.busy and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertFalse(self.app.busy)
            clear.assert_called_once_with('http://localhost:8090')

    # 回転スライダーと数値欄の連動を確認する。−75°が数値欄と描画に反映され、範囲は−90〜90°で、120°を入力してもスライダーは90°に収まること。
    def test_rotation_slider_and_text_stay_in_sync(self):
        args = self.sent_frame(lambda: self.app.angle_slider.setValue(-75))
        self.assertEqual(args[3], -75)
        self.assertEqual(self.app.v['angle'].text(), '-75')
        self.assertEqual(self.app.angle_slider.minimum(), -90)
        self.assertEqual(self.app.angle_slider.maximum(), 90)
        self.app.v['angle'].setText('90')
        self.assertEqual(self.app.angle_slider.value(), 90)
        self.app.v['angle'].setText('120')
        self.assertEqual(self.app.angle_slider.value(), 90)

    # 撮影プランの保存と再読み込みを確認する。45°・1.5倍がJSONに45°・面積倍率2.25として保存され、読み込み後に45°・1.5倍へ復元されて未保存扱いにならないこと。
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

    # 共有QRの表示を確認する。ダイアログが1つ開き、画像付きのラベルが表示されること。
    def test_qr_dialog_has_image(self):
        self.app.show_qr('https://example.com/plan')
        visible_dialogs = [dialog for dialog in self.app.findChildren(QDialog) if dialog.isVisible()]
        self.assertEqual(len(visible_dialogs), 1)
        labels = visible_dialogs[0].findChildren(QLabel)
        self.assertTrue(any(not label.pixmap().isNull() for label in labels))

    # 倍率を変更すると撮影枠が自動でONになり、1.7倍は面積倍率2.89として描画に渡ること。1.234倍を手入力すると入力精度を保ち、スライダーは1.2倍相当になること。
    def test_scale_automatically_enables_framing(self):
        args = self.sent_frame(lambda: self.app.scale_slider.setValue(17))
        self.assertTrue(self.app.framing.isChecked())
        self.assertAlmostEqual(args[4], 2.89)
        self.assertEqual(self.app.v['scale'].text(), '1.7')
        self.app.v['scale'].setText('1.234')
        self.assertEqual(self.app.scale_slider.value(), 12)
        self.assertEqual(self.app.v['scale'].text(), '1.234')

    # 撮影枠設定を無効にしたときの描画を確認する。回転角が0°、倍率が1倍に戻ること。
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

    # 対象を登録した直後の撮影枠を確認する。フォームの赤経欄と編集状態がクリアされても、登録した座標・角度・倍率で描画すること。
    def test_commit_keeps_frame_after_clearing_form(self):
        self.app.v['name'].setText('M1')
        self.app.v['angle'].setText('45')
        self.app.v['scale'].setText('1.5')
        args = self.sent_frame(self.app.commit)
        self.assertEqual(args[1:5], (5, 40, 45, 2.25))
        self.assertEqual(self.app.v['ra'].text(), '')
        self.assertIsNone(self.app.editing)

    # 一覧の空白クリックを確認する。対象の選択がすべて解除され、編集状態も解除されること。
    def test_clicking_empty_list_area_deselects_target(self):
        self.add_target('M1', 30)
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.show()
        QApplication.processEvents()
        QTest.mouseClick(self.app.tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(10, self.app.tree.viewport().height() - 5))
        self.assertEqual(self.app.tree.selectedItems(), [])
        self.assertIsNone(self.app.editing)

    # Ctrlクリックで選択を追加し、Shiftクリックで範囲選択できること。選択した3件を削除するとM2だけが21:00開始で残り、選択が解除されること。
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

    # 編集後に別の天体を取得する場面を確認する。新規ダイアログが1回開き、回転0°・倍率1倍・回転OFFで始まり、キャンセル後も既存対象の設定が残ること。
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

    # 天体取得ダイアログのプレビューを確認する。初期枠、45°・1.5倍に変更した枠、キャンセル後の消去がこの順に描画へ渡ること。
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

    # 選択解除済みの対象で全表示を切り替える。ONでは1件の枠と終了時刻が渡り、OFFではその枠を消去し、個別枠や時刻を指定しないこと。
    def test_all_frames_off_does_not_redraw_deselected_target(self):
        self.add_target('M1', 30)
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.tree.clearSelection()
        self.app.frame_timer.stop()
        args = self.sent_frame(lambda: self.app.show_all.setChecked(True))
        self.assertEqual(args[1:3], (5, 40))
        self.assertEqual(len(self.sent_options['frames']), 1)
        self.assertEqual(self.sent_options['start_min'], 1290)
        args = self.sent_frame(lambda: self.app.show_all.setChecked(False))
        self.assertEqual(args[1:3], (None, None))
        self.assertIsNone(self.sent_options['frames'])
        self.assertEqual(self.sent_options['previous_count'], 1)
        self.assertNotIn('start_min', self.sent_options)

    # プランの読み込みと追加時の表示名を確認する。元の名前と対応する連番付きの名前は元名で表示し、対応する元名のない Other_2 P1 はそのまま表示すること。
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

    # 同じプランを2回追加したときの枠表示を確認する。通常は選択したグループの2枠を表示して未選択枠を薄くし、全表示では全枠を薄くせずに描画すること。
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

    # 拡張モザイクのプレビューと登録結果を確認する。4枠から6枠に増やせて、重なりを増やすと枠間隔が狭まり、番号と座標が重複しないこと。
    # 登録後はプレビューと同じ座標の6件が10分間隔で並び、LP設定を引き継ぐこと。編集欄は通常状態に戻り、撮影時間と重なり率は設定から復元できること。
    def test_extended_mosaic_preview_matches_added_panels(self):
        self.app.v['duration'].setText('10')
        self.app.save_settings()
        self.app.v['duration'].clear()
        self.app.restore_settings()
        self.assertEqual(self.app.v['duration'].text(), '10')
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
        self.assertEqual([t['start_min'] for t in targets], list(range(1260, 1320, 10)))
        self.assertEqual([t['duration_min'] for t in targets], [10] * 6)
        self.assertEqual(len({t['target_id'] for t in targets}), 6)
        for frame, target in zip(preview, targets):
            self.assertEqual(tuple(target['target_ra_dec']), frame['target_ra_dec'])
            self.assertEqual(frame['mosaic'], dict(angle=0, scale=1))
            self.assertNotIn('mosaic', target)
            self.assertTrue(target['lp_filter'])
        self.assertFalse(dialog.extended_mosaic.isChecked())
        self.assertTrue(dialog.panel_overlap.isHidden())
        self.assertEqual(dialog.panel_overlap.value(), 40)
        self.assertTrue(all(control.isEnabled() for control in controls))
        self.assertEqual((dialog.panel_rows.value(), dialog.panel_columns.value()), (1, 1))

        self.app.save_settings()
        dialog.panel_overlap.setValue(20)
        self.app.restore_settings()
        self.assertEqual(dialog.panel_overlap.value(), 40)

    # 登録済みの行を選択すると、天体を再取得せずに座標・角度・倍率・S30 Proの画角と対象の開始時刻が描画に渡ること。基準日は未指定で、プランの開始時刻欄は21:00のままであること。
    def test_select_syncs_position_framing_and_target_start_without_fetch(self):
        self.app.model_combo.setCurrentText('S30 Pro')
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
        self.assertEqual(args[5:7], (2.24, 3.99))
        self.assertNotIn('reference', self.app.v)
        self.assertEqual(self.sent_options['start_min'], 1335)
        self.assertIsNone(self.sent_options['plan_date'])
        self.assertEqual(self.app.v['start'].text(), '21:00')

    # 追加・撮影時間の編集・開始時刻の変更・削除で各行の開始時刻が再計算されること。翌日00:30は24:30と表示し、行選択や時刻の再計算でフォームが未保存扱いにならないこと。
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

    # 座標と角度の表示精度を確認する。表示は丸められても、値を編集せずに再登録したときは元の精度で保存されること。
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

    # 六十進法の座標入力を確認する。赤経12:30と赤緯−00:30が12.5時と−0.5°に変換され、その値で描画・保存されること。
    def test_sexagesimal_input_is_sent_and_saved_in_original_units(self):
        self.app.v['name'].setText('M1')
        def change():
            self.app.v['ra'].setText('12:30:00.000')
            self.app.v['dec'].setText('-00:30:00.000')
        args = self.sent_frame(change)
        self.assertEqual(args[1:3], (12.5, -0.5))
        self.app.commit()
        self.assertEqual(self.app.targets[0]['target_ra_dec'], [12.5, -0.5])

    # LP列が回転列の直前にあり、対象ごとのON／OFFが表示されること。行を選ぶと設定がフォームに反映され、変更して登録すると一覧も更新されること。
    def test_lp_filter_column_precedes_rotation_and_updates(self):
        self.add_target('M1', 30)
        self.app.lp.setChecked(True)
        self.add_target('M2', 45)
        columns = [self.app.tree.headerItem().text(i) for i in range(self.app.tree.columnCount())]
        self.assertEqual(columns.index('LP') + 1, columns.index('回転'))
        self.assertEqual(self.app.tree.topLevelItem(0).text(3), 'OFF')
        self.assertEqual(self.app.tree.topLevelItem(1).text(3), 'ON')
        self.app.tree.setCurrentItem(self.app.tree.topLevelItem(0))
        self.app.select()
        self.assertFalse(self.app.lp.isChecked())
        self.app.lp.setChecked(True)
        self.app.commit()
        self.assertEqual(self.app.tree.topLevelItem(0).text(3), 'ON')

    # 並べ替え後に対象順と開始時刻が更新され、複数選択・編集中の行・未保存の60分入力が移動先で保持されること。登録すると移動先の対象に60分が保存されること。
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

    # 対象位置を選択せずに全枠表示と濃さ変更ができること。ONで2枠、濃さ変更で80が描画に渡り、OFFで2枠を消去して表示数が0になること。
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

    # 撮影枠の濃さ・太さスライダーを確認する。濃さは50〜100で初期値100、変更した濃さ75と太さ8がそれぞれ描画に渡ること。
    def test_frame_style_sliders(self):
        self.assertEqual(self.app.intensity_slider.minimum(), 50)
        self.assertEqual(self.app.intensity_slider.maximum(), 100)
        self.assertEqual(self.app.intensity_slider.value(), 100)
        self.sent_frame(lambda: self.app.intensity_slider.setValue(75))
        self.assertEqual(self.sent_options['intensity'], 75)
        self.sent_frame(lambda: self.app.thickness_slider.setValue(8))
        self.assertEqual(self.sent_options['thickness'], 8)

    # 対象カードの描画を確認する。登録直後と開始時刻変更後に一覧アイコンがあり、カードの画像サイズが75×50ピクセルになること。
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
