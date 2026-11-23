"""Resolve optional Seestar authentication in the bundled resource directory."""
import os
import sys
from pathlib import Path

key = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'seestar.pem'
configured_path = os.environ.get('SEESTAR_KEY_PATH', '')
# Preserve a valid explicit override, but replace empty or stale paths.
if key.is_file() and (not configured_path or not Path(configured_path).is_file()):
    os.environ['SEESTAR_KEY_PATH'] = str(key)
