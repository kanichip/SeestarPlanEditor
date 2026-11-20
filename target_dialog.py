"""Target input dialog shared by adding and editing plan targets."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QLabel, QLineEdit, QSlider,
    QSpinBox, QVBoxLayout,
)


ROTATION_RANGE = (-90, 90)


class TargetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('撮影対象の設定')
        self.setMinimumWidth(360)
        self.v = {key: QLineEdit(value) for key, value in {
            'name': '', 'ra': '', 'dec': '', 'duration': '30',
            'angle': '0', 'scale': '1.0',
        }.items()}
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
