"""Display settings for Stellarium's shooting frames."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QSlider, QVBoxLayout,
)


class FrameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('撮影枠の調整')
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self.intensity_slider.setRange(50, 100)
        self.intensity_slider.setValue(100)
        self.thickness_slider = QSlider(Qt.Orientation.Horizontal)
        self.thickness_slider.setRange(1, 10)
        self.thickness_slider.setValue(10)
        for title, slider, unit in [('枠の濃さ', self.intensity_slider, '%'),
                                    ('枠の太さ', self.thickness_slider, '')]:
            value = QLabel(f'{title}：{slider.value()}{unit}')
            slider.valueChanged.connect(
                lambda n, label=value, text=title, suffix=unit:
                label.setText(f'{text}：{n}{suffix}'))
            form.addRow(value, slider)
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def edit(self):
        previous = self.intensity_slider.value(), self.thickness_slider.value()
        if self.exec() != QDialog.DialogCode.Accepted:
            self.intensity_slider.setValue(previous[0])
            self.thickness_slider.setValue(previous[1])
