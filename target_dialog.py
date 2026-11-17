"""Target input dialog shared by adding and editing plan targets."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSlider,
    QVBoxLayout,
)


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
        for label, key in [('天体名', 'name'), ('赤経', 'ra'), ('赤緯', 'dec'),
                           ('撮影時間（分）', 'duration')]:
            form.addRow(label, self.v[key])
        self.lp = QCheckBox('LPフィルタ')
        form.addRow(self.lp)
        form.addRow('回転角（°）', self.v['angle'])
        self.angle_slider = QSlider(Qt.Orientation.Horizontal)
        self.angle_slider.setRange(-180, 180)
        form.addRow(self.angle_slider)
        form.addRow('辺の倍率（1〜2）', self.v['scale'])
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('反映')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('キャンセル')
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
