"""Resolve optional Seestar authentication in the bundled resource directory."""
import os
import sys
from pathlib import Path

key = Path(sys._MEIPASS) / 'seestar.pem'
if key.is_file():
    os.environ.setdefault('SEESTAR_KEY_PATH', str(key))
