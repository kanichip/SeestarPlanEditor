"""Target input dialog shared by adding and editing plan targets."""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QLabel, QLineEdit, QSlider,
    QSpinBox, QVBoxLayout,
)

from planner import coordinate_text, parse_coordinate


ROTATION_RANGE = (-90, 90)
ARCSECOND_STEPS = (3600, 60, 1)


class CoordinateEdit(QLineEdit):
    def __init__(self, *, ra):
        super().__init__()
        self.ra = ra
        self.wheel_delta = 0
        self.setToolTip('度・分・秒にカーソルを置くと、マウスホイールで増減できます。')

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not self.hasFocus() or self.isReadOnly() or not delta:
            super().wheelEvent(event)
            return

        text = self.text()
        try:
            value = parse_coordinate(text, ra=self.ra) if text.strip() else 0
        except ValueError:
            event.ignore()
            return

        spans = list(re.finditer(r'[+−-]?\d+(?:\.\d+)?', text))
        component = 0
        if len(spans) == 3:
            start = self.selectionStart() if self.hasSelectedText() else self.cursorPosition()
            end = start + len(self.selectedText())
            component = next((i for i, span in enumerate(spans)
                              if span.start() <= start <= end <= span.end()), None)
            if component is None:
                event.ignore()
                return

        self.wheel_delta += delta
        steps = int(self.wheel_delta / 120)
        self.wheel_delta -= steps * 120
        event.accept()
        if not steps:
            return

        units_per_degree = 15 if self.ra else 1
        ticks_per_unit = units_per_degree * 3600
        ticks = round(value * ticks_per_unit) + steps * ARCSECOND_STEPS[component]
        if self.ra:
            ticks %= 360 * 3600
        else:
            ticks = max(-90 * 3600, min(90 * 3600, ticks))
        updated = coordinate_text(ticks / ticks_per_unit, ra=self.ra)
        if updated != text:
            self.selectAll()
            self.insert(updated)
        span = list(re.finditer(r'\d+', updated))[component]
        self.setSelection(span.start(), len(span.group()))

    def focusOutEvent(self, event):
        self.wheel_delta = 0
        super().focusOutEvent(event)


class TargetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('撮影対象の設定')
        self.setMinimumWidth(360)
        self.v = {key: QLineEdit(value) for key, value in {
            'name': '', 'duration': '30',
            'angle': '0', 'scale': '1.0',
        }.items()}
        self.v.update(ra=CoordinateEdit(ra=True), dec=CoordinateEdit(ra=False))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        form.addRow('天体名', self.v['name'])
        fields = QGridLayout()
        fields.setColumnStretch(1, 1)
        fields.setColumnStretch(3, 1)
        for row, column, label, key in [
            (0, 0, '赤経', 'ra'), (0, 2, '赤緯', 'dec'),
            (1, 0, '撮影時間', 'duration'),
            (2, 0, '回転角', 'angle'), (2, 2, '倍率（1〜2）', 'scale'),
        ]:
            caption = QLabel(label)
            caption.setBuddy(self.v[key])
            fields.addWidget(caption, row, column)
            fields.addWidget(self.v[key], row, column + 1)
        self.lp = QCheckBox('LPフィルタ')
        fields.addWidget(self.lp, 1, 2, 1, 2)
        self.angle_slider = QSlider(Qt.Orientation.Horizontal)
        self.angle_slider.setRange(*ROTATION_RANGE)
        self.angle_slider.setAccessibleName('回転角')
        fields.addWidget(self.angle_slider, 3, 0, 1, 2)
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(10, 20)
        self.scale_slider.setPageStep(1)
        self.scale_slider.setAccessibleName('辺の倍率')
        fields.addWidget(self.scale_slider, 3, 2, 1, 2)
        form.addRow(fields)
        self.setTabOrder(self.v['duration'], self.lp)
        self.setTabOrder(self.lp, self.v['angle'])
        self.extended_mosaic = QCheckBox('拡張モザイク')
        form.addRow(self.extended_mosaic)
        self.panel_rows = QSpinBox()
        self.panel_columns = QSpinBox()
        for label, field in [('南北方向のパネル数', self.panel_rows),
                             ('東西方向のパネル数', self.panel_columns)]:
            field.setRange(1, 20)
            form.addRow(label, field)
            form.setRowVisible(field, False)
            self.extended_mosaic.toggled.connect(
                lambda checked, field=field: form.setRowVisible(field, checked))
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('反映')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('キャンセル')
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
