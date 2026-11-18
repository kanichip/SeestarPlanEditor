# -*- mode: python ; coding: utf-8 -*-
"""Standard onedir distribution with dependencies under _internal."""
from pathlib import Path
from shutil import copy2
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / 'frame.png'), '.')]
if (root / 'seestar.pem').is_file():
    datas.append((str(root / 'seestar.pem'), '.'))
datas += collect_data_files('seestarpy')
datas += copy_metadata('seestarpy')

a = Analysis(
    [str(root / 'packaging_entry.py')],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=['seestarpy.connection', 'seestarpy.plan', 'qrcode.image.pil'],
    hookspath=[],
    runtime_hooks=[str(root / 'packaging_runtime.py')],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinter'],
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='SeestarPlanEditor',
    console=False,
    debug=False,
    strip=False,
    upx=False,
    contents_directory='_internal',
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name='SeestarPlanEditor',
)

# Place the user guide and license texts beside the executable.
distribution_dir = Path(coll.name)
copy2(root / 'README.TXT', distribution_dir / 'README.TXT')
license_dir = distribution_dir / 'licenses'
license_dir.mkdir(parents=True, exist_ok=True)
for name in ('LGPL-3.0.txt', 'GPL-3.0.txt'):
    copy2(root / 'licenses' / name, license_dir / name)
