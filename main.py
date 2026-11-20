"""Seestar telephoto plan editor using PySide6."""
from __future__ import annotations

import copy
import io
import json
import math
import queue
import secrets
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QPointF, QRectF, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from target_dialog import ROTATION_RANGE, TargetDialog
from frame_dialog import FrameDialog
from seestar_client import SeestarClient

from planner import (build_plan, calculate_altitude, clear_stellarium_frames, clock, coordinate_text,
                     draw_stellarium_frame, mosaic_panel_centers, mosaic_panel_number, number, parse_coordinate,
                     schedule_targets, selected_object)


# Approximate telephoto field of view (short edge, long edge), in degrees.
# Pro/S50: https://us.seestar.com/blogs/review/s50-pro-s30-pro-vs-s50-has-a-new-star-king-been-born
# S30: same pixel dimensions/pitch as S50, with a 150 mm rather than 250 mm lens.
MODEL_FOV = {
    'S30': (1.20, 2.13),
    'S50': (0.72, 1.28),
    'S30 Pro': (2.24, 3.99),
    'S50 Pro': (1.38, 2.45),
}


def render_target_card(target: dict, plan_date: date, width: int = 75, height: int = 50,
                       lat_deg: float = 35.68, lon_deg: float = 139.76) -> QPixmap:
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 1. 外枠カード
    rect = QRectF(2, 2, width - 4, height - 4)
    painter.setBrush(QColor(24, 28, 36))
    painter.setPen(QPen(QColor(100, 110, 130, 160), 1.0))
    painter.drawRoundedRect(rect, 6, 6)

    graph_left = rect.left() + 4
    graph_right = rect.right() - 4
    graph_bottom = rect.bottom() - 4
    graph_height = rect.height() - 8

    # 2. 基準線の描画（0°地平線・30°境界線・60°基準線）
    # 0°基準線（地平線：クランプされたラインが接地するベースライン）
    painter.setPen(QPen(QColor(80, 90, 110, 140), 1.0))
    painter.drawLine(QPointF(graph_left, graph_bottom), QPointF(graph_right, graph_bottom))

    # 30°基準線（観測安全高度の目安）
    y_30 = graph_bottom - (graph_height * (30.0 / 90.0))
    painter.setPen(QPen(QColor(180, 120, 80, 120), 1.0, Qt.PenStyle.DashLine))
    painter.drawLine(QPointF(graph_left, y_30), QPointF(graph_right, y_30))

    # 60°基準線
    y_60 = graph_bottom - (graph_height * (60.0 / 90.0))
    painter.setPen(QPen(QColor(120, 130, 150, 80), 1.0, Qt.PenStyle.DashLine))
    painter.drawLine(QPointF(graph_left, y_60), QPointF(graph_right, y_60))

    # 3. 高度計算（0°未満は0°にクランプ）
    start_min = target['start_min']
    duration = target['duration_min']
    ra, dec = target['target_ra_dec']

    samples = 10
    points = []
    is_below_horizon = []

    for i in range(samples + 1):
        m = start_min + (duration * i / samples)
        moment = datetime.combine(plan_date, datetime.min.time()) + timedelta(minutes=m)
        alt = calculate_altitude(ra, dec, moment, lat_deg, lon_deg)

        # 0度未満は0度、90度超は90度に制限
        alt_clamped = max(0.0, min(90.0, alt))
        is_below_horizon.append(alt < 0.0)

        x = graph_left + (graph_right - graph_left) * (i / samples)
        y = graph_bottom - (graph_height * (alt_clamped / 90.0))
        points.append(QPointF(x, y))

    # 4. 高度推移ラインの描画
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        if is_below_horizon[i] and is_below_horizon[i + 1]:
            # 地平線下に留まっている区間（撮影不適）
            painter.setPen(QPen(QColor(220, 80, 80, 180), 1.5))
        else:
            # 観測可能な通常区間
            painter.setPen(QPen(QColor(100, 210, 255), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(p1, p2)

    # 5. 開始・終了マーカー
    start_color = QColor(255, 90, 90) if is_below_horizon[0] else QColor(255, 255, 255)
    end_color = QColor(255, 90, 90) if is_below_horizon[-1] else QColor(255, 255, 255)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(start_color)
    painter.drawEllipse(points[0], 2.0, 2.0)
    painter.setBrush(end_color)
    painter.drawEllipse(points[-1], 2.0, 2.0)

    painter.end()
    return pixmap


class TargetList(QTreeWidget):
    reordered = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(['対象', '開始', '分', 'LP', '回転', 'No.'])
        self.header().moveSection(5, 0)
        self.setIconSize(QSize(75, 50))
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((100, 40, 40, 40, 40)):
            self.setColumnWidth(column, width)
        self.header().setMinimumSectionSize(20)
        self.setColumnWidth(5, 25)

    def dropEvent(self, event):
        selected = self.selected_target_indices()
        blocker = QSignalBlocker(self)
        super().dropEvent(event)
        del blocker
        if event.isAccepted():
            self.reordered.emit(selected)

    def selected_target_indices(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.selectedItems()]

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.itemAt(event.position().toPoint()) is None):
            self.clearSelection()
            self.setCurrentItem(None)
        super().mousePressEvent(event)


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Seestar Plan Editor')
        self.resize(350, 450)
        self.setMinimumSize(350, 400)
        self.targets = []
        self.editing = None
        self.dirty = False
        self.form_dirty = False
        self.jobs = queue.Queue()
        self.busy = False
        self.seestar = None
        self.seestar_host = '10.0.0.1'
        self.stellarium_frame_enabled = False
        self.stellarium_selection_start = None
        self.stellarium_plan_dates = {}
        self.stellarium_frame_count = 0
        self.committed_frames = []
        self.frame_deselected = False
        self.numeric_values = {}
        self.v = {key: QLineEdit(value) for key, value in {
            'url': 'http://localhost:8090', 'plan': date.today().strftime('%Y%m%d'),
            'name': '', 'ra': '', 'dec': '', 'start': '21:00', 'duration': '30',
            'angle': '0', 'scale': '1.0',
            'fov_width': str(MODEL_FOV['S50 Pro'][0]),
            'fov_height': str(MODEL_FOV['S50 Pro'][1]),
            'intensity': '100',
        }.items()}
        self.target_dialog = TargetDialog(self)
        self.v.update(self.target_dialog.v)
        self.lp = self.target_dialog.lp
        self.angle_slider = self.target_dialog.angle_slider
        self.scale_slider = self.target_dialog.scale_slider
        self.target_dialog.buttons.accepted.connect(self.commit)
        self.framing = QCheckBox('回転フレーミングを有効化（実機未検証）')
        self.show_all = QPushButton('全表示')
        self.show_all.setCheckable(True)
        self.status = QLabel('Stellariumで対象を選び「選択天体を取得」を押してください。')
        self.status.setWordWrap(True)
        self.frame_timer = QTimer(self)
        self.frame_timer.setSingleShot(True)
        self.frame_timer.timeout.connect(self.update_stellarium_frame)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        outer = QWidget()
        self.setCentralWidget(outer)
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(16, 0, 16, 16)
        self.model_combo = QComboBox()
        self.model_combo.addItems(list(MODEL_FOV))
        self.model_combo.setCurrentText('S50 Pro')
        title = self.model_combo
        font = title.font()
        font.setPointSize(10)
        font.setBold(True)
        title.setFont(font)
        model_row = QHBoxLayout()
        model_row.addWidget(title)
        self.fov_label = QLabel()
        model_row.addWidget(self.fov_label)
        model_row.addStretch()
        layout.addLayout(model_row)
        top = QHBoxLayout()
        fetch_button = self.add_button(top, '選択天体を取得', self.fetch)
        fetch_font = fetch_button.font()
        fetch_font.setPointSize(12)
        fetch_font.setBold(True)
        fetch_button.setFont(fetch_font)
        fetch_button.setStyleSheet('background-color: #d9edff; padding: 6px 12px;')
        top.addWidget(QLabel('カメラ：望遠固定'))
        top.addStretch()
        layout.addLayout(top)
        plan = QHBoxLayout()
        plan.addWidget(QLabel('プラン名'))
        self.v['plan'].setMaximumWidth(100)
        plan.addWidget(self.v['plan'])
        self.today_label = QLabel(date.today().strftime('%Y%m%d'))
        #plan.addWidget(self.today_label)
        plan.addWidget(QLabel('開始時刻'))
        self.v['start'].setMaximumWidth(75)
        plan.addWidget(self.v['start'])
        plan.addStretch()
        layout.addLayout(plan)
        body = QHBoxLayout()
        layout.addLayout(body, 1)
        self.frame_dialog = FrameDialog(self)
        self.intensity_slider = self.frame_dialog.intensity_slider
        self.thickness_slider = self.frame_dialog.thickness_slider
        for control in (self.v['url'], self.v['fov_width'], self.v['fov_height'], self.framing):
            control.setParent(outer)
            control.hide()
        right = QVBoxLayout()
        body.addLayout(right, 1)
        self.tree = TargetList()
        right.addWidget(self.tree, 1)
        list_actions = QHBoxLayout()
        list_actions.addStretch()
        list_actions.addWidget(self.show_all)
        self.add_button(list_actions, '選択対象を削除', self.remove)
        right.addLayout(list_actions)
        self.guide_label = QLabel('共有後、QRコードをSeestarアプリで読み込みます。\n'
                      '撮影開始時刻・日付はアプリ側でも確認してください。\n'
                      'Seestarメニューから本体への接続・プラン転送ができます。')
        self.guide_label.setWordWrap(True)
        right.addWidget(self.guide_label)
        self.guide_label.hide()
        layout.addWidget(self.status)
        self.status.hide()
        self.connection_status = QLabel('Seestar：未接続')
        self.statusBar().addPermanentWidget(self.connection_status)
        file_menu = self.menuBar().addMenu('ファイル')
        file_menu.addAction('保存', self.save).setShortcut('Ctrl+S')
        file_menu.addAction('開く', self.load).setShortcut('Ctrl+O')
        file_menu.addSeparator()
        self.always_on_top_action = file_menu.addAction('常に手前に表示')
        self.always_on_top_action.setCheckable(True)
        self.always_on_top_action.toggled.connect(self.set_always_on_top)
        self.always_on_top_action.setChecked(True)
        seestar_menu = self.menuBar().addMenu('Seestar')
        self.connect_action = seestar_menu.addAction('接続', self.connect_seestar)
        self.share_action = seestar_menu.addAction('共有QR', self.share)
        self.transfer_action = seestar_menu.addAction('プラン転送', self.transfer_plan)
        self.transfer_action.setEnabled(False)
        other_menu = self.menuBar().addMenu('その他')
        other_menu.addAction('撮影枠', self.frame_dialog.edit)
        self.guide_action = other_menu.addAction('ガイド表示')
        self.guide_action.setCheckable(True)
        self.guide_action.toggled.connect(self.guide_label.setVisible)
        self.guide_action.toggled.connect(self.status.setVisible)
        self.tree.itemSelectionChanged.connect(self.select)
        self.tree.reordered.connect(self.reorder_targets)
        self.tree.itemDoubleClicked.connect(self.edit_target)
        self.angle_slider.valueChanged.connect(self.rotation_slid)
        self.v['angle'].textChanged.connect(self.sync_angle_slider)
        self.scale_slider.valueChanged.connect(lambda value: self.v['scale'].setText(f'{value / 10:.1f}'))
        self.v['scale'].textChanged.connect(self.sync_scale_slider)
        self.intensity_slider.valueChanged.connect(lambda value: self.v['intensity'].setText(str(value)))
        self.v['intensity'].textChanged.connect(self.sync_intensity_slider)
        self.thickness_slider.valueChanged.connect(self.schedule_stellarium_frame)
        for key in ('ra', 'dec', 'angle'):
            self.v[key].editingFinished.connect(lambda k=key: self.format_number(k))
        for key in ('angle', 'scale'):
            self.v[key].textChanged.connect(self.framing_changed)
        self.target_dialog.extended_mosaic.toggled.connect(self.extended_mosaic_changed)
        for field in (self.target_dialog.panel_rows, self.target_dialog.panel_columns):
            field.valueChanged.connect(self.schedule_stellarium_frame)
        self.framing.toggled.connect(self.draw)
        self.show_all.toggled.connect(self.toggle_all_frames)
        for key in ('ra', 'dec', 'fov_width', 'fov_height', 'url', 'intensity'):
            self.v[key].textChanged.connect(self.schedule_stellarium_frame)
        self.v['plan'].textChanged.connect(self.mark_dirty)
        self.v['start'].textChanged.connect(self.start_changed)
        for key in ('name', 'ra', 'dec', 'duration', 'angle', 'scale'):
            self.v[key].textChanged.connect(self.mark_form_dirty)
        self.lp.toggled.connect(self.mark_form_dirty)
        self.framing.toggled.connect(self.mark_form_dirty)
        self.model_combo.currentTextChanged.connect(self.model_changed)
        self.model_changed(self.model_combo.currentText())
        self.poll_timer.start(100)
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(self.clear_startup_frames)
        self.startup_timer.start(0)

    def model_changed(self, model):
        width, height = MODEL_FOV[model]
        # Update both dimensions together before scheduling the new frame.
        with QSignalBlocker(self.v['fov_width']), QSignalBlocker(self.v['fov_height']):
            self.v['fov_width'].setText(str(width))
            self.v['fov_height'].setText(str(height))
        self.model_combo.setToolTip(f'望遠画角：横 {width:.2f}° × 縦 {height:.2f}°')
        self.fov_label.setText(f'画角：横 {width:.2f}° × 縦 {height:.2f}°')
        self.schedule_stellarium_frame()

    def select_connected_model(self, model):
        key = ''.join(str(model).casefold().split()).removeprefix('seestar')
        for choice in MODEL_FOV:
            if key == ''.join(choice.casefold().split()):
                self.model_combo.setCurrentText(choice)
                return

    def set_always_on_top(self, enabled):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:
            self.show()

    def clear_startup_frames(self):
        url = self.v['url'].text()
        def work():
            try:
                clear_stellarium_frames(url)
            except Exception as exc:
                return str(exc)
            return None
        def done(error):
            if error:
                self.status.setText(f'起動時の撮影枠消去ができませんでした: {error}')
            else:
                self.stellarium_frame_count = 0
                self.status.setText('前回の撮影枠を消去しました。Stellariumで対象を選び「選択天体を取得」を押してください。')
        self.run_job(work, done)

    @staticmethod
    def add_button(layout, label, action):
        button = QPushButton(label)
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def sync_angle_slider(self):
        try:
            value = round(number(self.v['angle'].text(), *ROTATION_RANGE, '回転角'))
        except ValueError:
            return
        blocker = QSignalBlocker(self.angle_slider)
        self.angle_slider.setValue(value)

    def sync_scale_slider(self):
        try:
            value = round(number(self.v['scale'].text(), 1, 2, '辺の倍率') * 10)
        except ValueError:
            return
        with QSignalBlocker(self.scale_slider):
            self.scale_slider.setValue(value)

    def sync_intensity_slider(self):
        try:
            value = round(number(self.v['intensity'].text(), 50, 100, '枠の濃さ'))
        except ValueError:
            return
        blocker = QSignalBlocker(self.intensity_slider)
        self.intensity_slider.setValue(value)

    def mark_dirty(self, *_):
        self.dirty = True

    def rotation_slid(self, value):
        self.set_number('angle', int(float(value)))
        self.stellarium_frame_enabled = True
        self.schedule_stellarium_frame()

    def set_number(self, key, value):
        text = coordinate_text(value, ra=key == 'ra') if key in ('ra', 'dec') else f'{value:.0f}'
        self.numeric_values[key] = (text, value)
        if self.v[key].text() != text:
            self.v[key].setText(text)

    def input_number(self, key, low, high, label):
        text = self.v[key].text()
        saved = self.numeric_values.get(key)
        if saved and saved[0] == text:
            value = saved[1]
        else:
            value = parse_coordinate(text, ra=key == 'ra') if key in ('ra', 'dec') else text
        return number(value, low, high, label)

    def format_number(self, key):
        try:
            low, high = {'ra': (0, 24), 'dec': (-90, 90), 'angle': ROTATION_RANGE}[key]
            self.set_number(key, self.input_number(key, low, high, key))
        except ValueError:
            pass

    def toggle_all_frames(self, *_):
        self.stellarium_frame_enabled = True
        self.schedule_stellarium_frame()

    def reorder_targets(self, selected=None):
        if selected is None:
            selected = self.tree.selected_target_indices()
        order = [self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                 for i in range(self.tree.topLevelItemCount())]
        try:
            targets = schedule_targets([self.targets[i] for i in order], self.v['start'].text())
        except ValueError as exc:
            self.refresh()
            self.error(exc)
            return
        editing = order.index(self.editing) if self.editing is not None else None
        self.targets = targets
        self.editing = editing
        self.refresh()
        selected = set(selected)
        with QSignalBlocker(self.tree):
            for position, index in enumerate(order):
                if index in selected:
                    self.tree.topLevelItem(position).setSelected(True)
            if editing is not None:
                self.tree.setCurrentItem(self.tree.topLevelItem(editing), 0,
                                         QItemSelectionModel.SelectionFlag.NoUpdate)
        if editing is not None:
            self.stellarium_selection_start = targets[editing]['start_min']
        self.dirty = True
        self.schedule_stellarium_frame()

    def start_changed(self, *_):
        self.dirty = True
        try:
            targets = schedule_targets(self.targets, self.v['start'].text())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.targets = targets
        plan_date = date.today()
        for i, t in enumerate(self.targets):
            item = self.tree.topLevelItem(i)
            if item is not None:
                item.setText(1, clock(t['start_min']))
                pixmap = render_target_card(t, plan_date)
                item.setIcon(0, QIcon(pixmap))
        if self.editing is not None:
            self.stellarium_selection_start = self.targets[self.editing]['start_min']
            self.schedule_stellarium_frame()
        self.status.setText('開始時刻と撮影時間の積算から各対象の開始を更新しました。')

    def framing_changed(self, *_):
        # Moving the slider or entering framing values must take effect even
        # when the initially unchecked framing option has not been selected.
        if not self.framing.isChecked():
            self.framing.setChecked(True)
        else:
            self.draw()

    def extended_mosaic_changed(self, enabled):
        if enabled:
            self.set_number('angle', 0)
            self.v['scale'].setText('1.0')
            self.framing.setChecked(False)
        for control in (self.v['angle'], self.angle_slider, self.v['scale'], self.scale_slider):
            control.setEnabled(not enabled)
        self.schedule_stellarium_frame()

    def mark_form_dirty(self, *_):
        self.frame_deselected = False
        self.form_dirty = True

    def guard_form(self):
        if self.form_dirty:
            raise ValueError('対象設定ダイアログの「反映」または「キャンセル」で編集を終了してください。')

    def draw(self, *_):
        self.schedule_stellarium_frame()

    def schedule_stellarium_frame(self, *_):
        self.frame_timer.stop()
        if self.stellarium_frame_enabled:
            self.frame_timer.start(250)

    def update_stellarium_frame(self):
        if self.busy:
            self.frame_timer.start(100)
            return
        panel_number = None
        if self.editing is not None:
            panel_number = mosaic_panel_number(self.targets[self.editing])
        try:
            has_position = bool(self.v['ra'].text().strip() and self.v['dec'].text().strip())
            angle = self.input_number('angle', *ROTATION_RANGE, '回転角') if self.framing.isChecked() else 0
            scale = number(self.v['scale'].text(), 1, 2, '辺の倍率') ** 2 if self.framing.isChecked() else 1
            args = (self.v['url'].text(), self.input_number('ra', 0, 24, '赤経') if has_position else None,
                    self.input_number('dec', -90, 90, '赤緯') if has_position else None, angle, scale,
                    number(self.v['fov_width'].text(), 0.01, 30, '横画角'),
                    number(self.v['fov_height'].text(), 0.01, 30, '縦画角'))
            if not has_position and len(self.committed_frames) == 1 and not self.show_all.isChecked():
                target = self.committed_frames[0]
                panel_number = mosaic_panel_number(target)
                mosaic = target.get('mosaic') or {}
                args = (args[0], *target['target_ra_dec'], mosaic.get('angle', 0),
                        mosaic.get('scale', 1), *args[5:])
            if self.frame_deselected and self.editing is None:
                args = (args[0], None, None, *args[3:])
            intensity = number(self.v['intensity'].text(), 50, 100, '枠の濃さ')
        except ValueError:
            return  # Allow incomplete values while typing.
        start = self.stellarium_selection_start
        self.stellarium_selection_start = None
        options = {} if start is None else dict(
            start_min=start, plan_date=self.stellarium_plan_dates.get(args[0]))
        frames = copy.deepcopy(self.targets) if self.show_all.isChecked() else None
        if args[1] is not None and self.editing is None and self.target_dialog.extended_mosaic.isChecked():
            _, ra, dec, angle, scale, width, height = args
            frames = (frames or []) + [
                dict(target_ra_dec=(panel_ra, panel_dec), mosaic=dict(angle=angle, scale=scale),
                     panel_number=panel)
                for panel, (panel_ra, panel_dec) in enumerate(
                    self.panel_centers(ra, dec, angle, scale, width, height), start=1)
            ]
            args = (args[0], None, None, *args[3:])
        elif not has_position and not self.frame_deselected and frames is None and len(self.committed_frames) > 1:
            frames = self.committed_frames
        options.update(intensity=intensity, thickness=self.thickness_slider.value(), frames=frames,
                       previous_count=self.stellarium_frame_count, selected_index=self.editing,
                       panel_number=panel_number)
        def work():
            try:
                plan_date = draw_stellarium_frame(*args, **options)
                return None, plan_date
            except Exception as exc:
                return str(exc), None
        def done(result):
            error, plan_date = result
            if not error and start is not None:
                self.stellarium_plan_dates[args[0]] = plan_date
            if not error:
                self.stellarium_frame_count = len(frames) if frames is not None else 0
            self.status.setText(f'Stellariumの表示を更新できませんでした: {error}' if error
                            else (f'Stellariumを対象の位置・開始時刻 {clock(start)} に変更しました。'
                                  if start is not None else 'Stellariumに撮影範囲を描画しました。'))
        self.run_job(work, done)

    def run_job(self, fn, done):
        if self.busy:
            return
        self.busy = True
        self.update_seestar_actions()
        self.status.setText('通信中…')
        def work():
            try:
                self.jobs.put((done, fn(), None))
            except Exception as exc:
                self.jobs.put((done, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            done, result, error = self.jobs.get_nowait()
            self.busy = False
            self.update_seestar_actions()
            if error:
                self.error(error)
            else:
                try:
                    done(result)
                except Exception as exc:
                    self.error(str(exc))
        except queue.Empty:
            pass

    def error(self, text):
        self.status.setText('操作を完了できませんでした。')
        QMessageBox.critical(self, '確認してください', str(text))

    def fetch(self):
        url = self.v['url'].text()
        def done(obj):
            self.reset()
            self.stellarium_selection_start = None
            for key, value in zip(('name', 'ra', 'dec'), obj):
                if key == 'name':
                    self.v[key].setText(str(value))
                else:
                    self.set_number(key, value)
            self.status.setText('撮影対象を設定してください。')
            self.stellarium_frame_enabled = True
            self.schedule_stellarium_frame()
            # Return from the polling timer before entering the modal event loop.
            # The timer must keep collecting frame updates while the dialog is open.
            QTimer.singleShot(0, self, self.open_target_dialog)
        self.run_job(lambda: selected_object(url), done)

    def edit_target(self, item, _column=0):
        self.tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.select()
        if self.editing == item.data(0, Qt.ItemDataRole.UserRole):
            self.open_target_dialog()

    def open_target_dialog(self):
        # Restore the selected target and preview if the dialog is cancelled.
        values = {key: self.v[key].text() for key in self.target_dialog.v}
        numeric_values = self.numeric_values.copy()
        lp, framing = self.lp.isChecked(), self.framing.isChecked()
        adding = self.editing is None
        self.target_dialog.extended_mosaic.setVisible(adding)
        self.target_dialog.setWindowTitle(
            '撮影対象を追加' if adding else '撮影対象を編集')
        if self.target_dialog.exec() != QDialog.DialogCode.Accepted:
            if adding:
                self.reset()
            else:
                for key, value in values.items():
                    self.v[key].setText(value)
                self.numeric_values = numeric_values
                self.lp.setChecked(lp)
                self.framing.setChecked(framing)
                self.form_dirty = False
            self.schedule_stellarium_frame()

    def panel_centers(self, ra, dec, angle, scale, width, height):
        return mosaic_panel_centers(ra, dec, angle, scale, width, height,
                                    self.target_dialog.panel_rows.value(),
                                    self.target_dialog.panel_columns.value())

    def commit(self):
        try:
            # Keep additional imported target settings when editing.
            t = copy.deepcopy(self.targets[self.editing]) if self.editing is not None else {}
            t.update(target_id=t.get('target_id', secrets.randbelow(900000000)+100000000),
                     target_name=self.v['name'].text().strip(), alias_name=t.get('alias_name', ''),
                     target_ra_dec=[self.input_number('ra', 0, 24-1e-10, '赤経'), self.input_number('dec', -90, 90, '赤緯')],
                     duration_min=int(self.v['duration'].text()),
                     lp_filter=self.lp.isChecked(), camera_mode='tele')
            if self.framing.isChecked():
                mosaic = copy.deepcopy(t.get('mosaic') or {})
                mosaic.update(scale=number(self.v['scale'].text(), 1, 2, '辺の倍率') ** 2,
                              angle=self.input_number('angle', *ROTATION_RANGE, '回転角'))
                mosaic['star_map_angle'] = mosaic['angle']
                t['mosaic'] = mosaic
            else:
                t.pop('mosaic', None)
            candidates = copy.deepcopy(self.targets)
            committed = [t]
            if self.editing is None and self.target_dialog.extended_mosaic.isChecked():
                if not t['target_name']:
                    raise ValueError('天体名を入力してください。')
                mosaic = t.get('mosaic') or {}
                width = number(self.v['fov_width'].text(), 0.01, 30, '横画角')
                height = number(self.v['fov_height'].text(), 0.01, 30, '縦画角')
                centers = self.panel_centers(*t['target_ra_dec'], mosaic.get('angle', 0),
                                             mosaic.get('scale', 1), width, height)
                used_ids = {target['target_id'] for target in candidates}
                committed = []
                for panel, (ra, dec) in enumerate(centers, start=1):
                    target_id = secrets.randbelow(900000000) + 100000000
                    while target_id in used_ids:
                        target_id = secrets.randbelow(900000000) + 100000000
                    used_ids.add(target_id)
                    committed.append(dict(t, target_id=target_id, target_ra_dec=[ra, dec],
                                          target_name=f"{t['target_name']} P{panel}"))
            if self.editing is None:
                candidates.extend(committed)
            else:
                candidates[self.editing] = t
            self.targets = build_plan(self.v['plan'].text(), candidates, self.v['start'].text())['list']
            self.dirty = True
            self.refresh()
            self.reset()
            self.committed_frames = copy.deepcopy(committed)
            self.status.setText('撮影リストを更新しました。')
            self.target_dialog.accept()
        except (ValueError, KeyError, TypeError) as exc:
            self.error(exc)

    def refresh(self):
        blocker = QSignalBlocker(self.tree)
        self.tree.clear()
        plan_date = date.today()
        for i, t in enumerate(self.targets):
            angle = (t.get('mosaic') or {}).get('angle')
            item = QTreeWidgetItem([t['target_name'], clock(t['start_min']), str(t['duration_min']),
                                    'ON' if t['lp_filter'] else 'OFF',
                                    f'{angle:.0f}' if angle is not None else '—', str(i + 1)])
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            item.setTextAlignment(5, Qt.AlignmentFlag.AlignCenter)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            pixmap = render_target_card(t, plan_date)
            item.setIcon(0, QIcon(pixmap))
            self.tree.addTopLevelItem(item)
        self.schedule_stellarium_frame()

    def select(self, _=None):
        selection = self.tree.selectedItems()
        if not selection:
            self.editing = None
            self.frame_deselected = True
            self.stellarium_selection_start = None
            return
        current = self.tree.currentItem()
        active = current if current in selection else selection[0]
        index = active.data(0, Qt.ItemDataRole.UserRole)
        if self.editing == index:
            return
        if self.form_dirty and not self.confirm('編集内容', '未反映の編集を破棄して対象を切り替えますか？'):
            self.tree.clearSelection()
            return
        self.editing = index
        self.frame_deselected = False
        t = self.targets[self.editing]
        m = t.get('mosaic') or {}
        values = dict(name=t['target_name'], ra=t['target_ra_dec'][0], dec=t['target_ra_dec'][1],
                      duration=t['duration_min'], angle=m.get('angle', 0),
                      scale=math.sqrt(m.get('scale', 1)))
        for k, v in values.items():
            if k in ('ra', 'dec', 'angle'):
                self.set_number(k, v)
            else:
                self.v[k].setText(str(v))
        self.lp.setChecked(t['lp_filter'])
        self.framing.setChecked(bool(m))
        self.stellarium_frame_enabled = True
        self.stellarium_selection_start = t['start_min']
        self.draw()
        self.form_dirty = False

    def reset(self):
        self.committed_frames = []
        self.editing = None
        self.stellarium_selection_start = None
        self.tree.clearSelection()
        for k in ('name', 'ra', 'dec'):
            self.v[k].setText('')
        self.set_number('angle', 0)
        self.v['scale'].setText('1.0')
        self.framing.setChecked(False)
        self.target_dialog.extended_mosaic.setChecked(False)
        self.target_dialog.panel_rows.setValue(1)
        self.target_dialog.panel_columns.setValue(1)
        self.frame_deselected = False
        self.form_dirty = False

    def remove(self):
        selected = set(self.tree.selected_target_indices())
        if selected:
            candidates = [target for index, target in enumerate(self.targets) if index not in selected]
            try:
                self.targets = schedule_targets(candidates, self.v['start'].text())
            except ValueError as exc:
                self.error(exc)
                return
            self.reset()
            self.refresh()
            self.dirty = True

    def save(self):
        try:
            self.guard_form()
            p = build_plan(self.v['plan'].text(), self.targets, self.v['start'].text())
            filename = p['plan_name']
            if not filename.lower().endswith('.json'):
                filename += '.json'
            path = QFileDialog.getSaveFileName(self, 'プランJSONを保存', filename, '撮影プラン (*.json)')[0]
            if path:
                Path(path).write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding='utf-8')
                self.dirty = False
                self.status.setText(f'保存しました：{path}')
        except Exception as exc:
            self.error(exc)

    def load(self):
        if (self.dirty or self.form_dirty) and not self.confirm('編集内容', '現在の編集を破棄して開きますか？'):
            return
        path = QFileDialog.getOpenFileName(self, 'プランを開く', '', '撮影プラン (*.json)')[0]
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            p = build_plan(data['plan_name'], data['list'])
            start = clock(p['list'][0]['start_min'])
            targets = schedule_targets(p['list'], start)
            self.v['start'].setText(start)
            self.targets = targets
            self.stellarium_plan_dates.clear()
            self.v['plan'].setText(p['plan_name'])
            self.reset()
            self.refresh()
            self.dirty = False
            self.status.setText('読み込みました。カメラは望遠に固定されます。')
        except Exception as exc:
            self.error(exc)

    def share(self):
        if self.busy:
            return
        try:
            self.guard_form()
            p = build_plan(self.v['plan'].text(), self.targets, self.v['start'].text())
        except Exception as exc:
            self.error(exc)
            return
        if not self.confirm('共有プランを作成', 'このプランをZWOクラウドへアップロードして共有リンクを作成します。\nリンクを知る人はプランを取得でき、削除機能はありません。\n' + ('\n回転指定は実機未検証です。取込後に公式アプリで構図を確認してください。\n' if any(t.get('mosaic') for t in p['list']) else '') + '\n共有しますか？'):
            return
        def work():
            from seestarpy import plan
            return plan.share_plan(p, timeout=25)['share_link']
        self.run_job(work, self.show_qr)

    def update_seestar_actions(self):
        self.connect_action.setEnabled(not self.busy)
        self.share_action.setEnabled(not self.busy)
        self.transfer_action.setEnabled(not self.busy and self.seestar is not None)

    def connect_seestar(self):
        if self.busy:
            return
        def work():
            try:
                return SeestarClient.discover_devices(), None
            except Exception as exc:
                return [], str(exc)

        def done(result):
            addresses, error = result
            self.status.setText('自動検出できませんでした。IPアドレスを直接入力できます。' if error
                                else '接続するSeestarの機種名を選択してください。')
            self.choose_seestar(addresses)

        self.run_job(work, done)

    def choose_seestar(self, addresses):
        devices = list(addresses)
        choices = []
        for i, device in enumerate(devices):
            model = device['model']
            repeated = sum(d['model'] == model for d in devices) > 1
            choices.append(f'{model} ({i + 1})' if repeated else model)
        manual = 'IPアドレスを直接入力…'
        choices.append(manual)
        current = next((i for i, d in enumerate(devices)
                        if d['host'] == self.seestar_host), 0)
        selected, accepted = QInputDialog.getItem(
            self, 'Seestarに接続',
            '接続する機種を選択してください',
            choices, current, False)
        if not accepted:
            return
        if selected == manual:
            host, accepted = QInputDialog.getText(
                self, 'Seestarに接続', 'IPアドレスを入力してください', text=self.seestar_host)
            if not accepted:
                return
        else:
            host = devices[choices.index(selected)]['host']
        try:
            client = SeestarClient(host)
        except ValueError:
            self.error('有効なIPv4アドレスを入力してください。例：10.0.0.1')
            return
        self.seestar = None
        self.seestar_host = client.host
        self.connection_status.setText('Seestar：接続中')

        def work():
            try:
                return client.connect(), None
            except Exception as exc:
                return None, str(exc)

        def done(result):
            state, error = result
            if error:
                self.connection_status.setText('Seestar：未接続')
                self.update_seestar_actions()
                self.error(f'Seestarへの接続に失敗しました：{error}')
                return
            self.seestar = client
            device = state.get('device') or {}
            model = (device.get('product_model') or device.get('model')
                     or state.get('product_model') or state.get('model'))
            if model:
                self.select_connected_model(model)
            model_name = str(model) if model else '機種名不明'
            self.connection_status.setText(f'Seestar：{model_name} 接続確認済み')
            self.update_seestar_actions()
            self.status.setText(f'Seestarに接続しました：{model_name}')

        self.run_job(work, done)

    def transfer_plan(self):
        if self.busy:
            return
        client = self.seestar
        if client is None:
            self.error('Seestarメニューの「接続」で本体に接続してください。')
            return
        try:
            self.guard_form()
            payload = build_plan(self.v['plan'].text(), self.targets, self.v['start'].text())
        except Exception as exc:
            self.error(exc)
            return
        if not self.confirm(
                'プランを転送・実行',
                f"接続先：{client.host}\nプラン：{payload['plan_name']}\n"
                f"対象数：{len(payload['list'])}\n開始時刻：{clock(payload['list'][0]['start_min'])}\n\n"
                '転送すると、本体でこのプランの実行が開始されます。\n'
                '開始時刻が過去の場合は、すぐに撮影が始まる可能性があります。\n'
                '現在の本体の状態と撮影範囲を確認して、転送しますか？'):
            return

        def work():
            try:
                client.transfer(payload)
                return None
            except Exception as exc:
                return str(exc)

        def done(error):
            if error:
                self.seestar = None
                self.connection_status.setText('Seestar：接続を再確認してください')
                self.update_seestar_actions()
                self.error(f'プラン転送の完了を確認できませんでした：{error}\n'
                           '本体側の状態を確認してから再接続してください。')
                return
            self.status.setText(f"Seestarへプラン「{payload['plan_name']}」を転送し、実行を開始しました。")

        self.run_job(work, done)

    def show_qr(self, link):
        import qrcode
        qr = qrcode.make(link)
        image_data = io.BytesIO()
        qr.save(image_data, format='PNG')
        pixmap = QPixmap()
        pixmap.loadFromData(image_data.getvalue(), 'PNG')
        win = QDialog(self)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        win.setWindowTitle('Seestarアプリで読み取ってください')
        layout = QVBoxLayout(win)
        image = QLabel()
        image.setPixmap(pixmap.scaled(350, 350, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.FastTransformation))
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(image)
        entry = QLineEdit(link)
        entry.setReadOnly(True)
        entry.setMinimumWidth(500)
        layout.addWidget(entry)
        def save_qr():
            path = QFileDialog.getSaveFileName(win, 'QRコードを保存', '', 'QRコード (*.png)')[0]
            if path:
                try:
                    qr.save(path)
                except Exception as exc:
                    self.error(exc)
        self.add_button(layout, 'QRコードを保存', save_qr)
        layout.addWidget(QLabel('Seestarアプリで取り込み後、時刻と構図を確認して本体へ設定してください。'))
        win.show()
        self.status.setText('共有リンクを作成しました。')

    def closeEvent(self, event):
        if (self.dirty or self.form_dirty) and not self.confirm('終了', '未保存の編集を破棄して終了しますか？'):
            event.ignore()
            return
        self.frame_timer.stop()
        self.poll_timer.stop()
        event.accept()


if __name__ == '__main__':
    application = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(application.exec())
