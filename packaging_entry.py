"""Executable entry point with an offline packaging smoke test."""
import multiprocessing
import sys


def run():
    from PySide6.QtWidgets import QApplication
    from main import App

    app = QApplication(sys.argv)
    window = App()
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke-test':
        import json
        from pathlib import Path
        import PySide6
        import shiboken6
        from PySide6 import QtCore, QtGui, QtWidgets
        from PIL import Image
        import qrcode

        window.startup_timer.stop()
        window.frame_timer.stop()
        window.poll_timer.stop()
        root = Path(sys._MEIPASS)
        with Image.open(root / 'frame.png') as frame:
            frame_size = list(frame.size)
        qr = qrcode.make('SeestarPlanEditor packaging check')
        assert qr.size[0] > 0
        report = {
            'qt_version': QtCore.qVersion(),
            'qtcore': QtCore.__file__,
            'qtgui': QtGui.__file__,
            'qtwidgets': QtWidgets.__file__,
            'pyside6': PySide6.__file__,
            'shiboken6': shiboken6.__file__,
            'frame_size': frame_size,
            'platform': app.platformName(),
            'window_created': True,
        }
        Path(sys.argv[2]).write_text(json.dumps(report, indent=2), encoding='utf-8')
        return 0
    window.show()
    return app.exec()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(run())
