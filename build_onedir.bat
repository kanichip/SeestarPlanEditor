@echo off
setlocal
cd /d "%~dp0"

set "BUILD_PYTHON=%~dp0.build-venv\Scripts\python.exe"
set "BUILD_EXE=%~dp0dist\SeestarPlanEditor\SeestarPlanEditor.exe"
if not exist "%BUILD_PYTHON%" (
    echo Build environment not found: .build-venv
    echo Create it and install requirements-build.txt as described in README.md.
    exit /b 1
)

rem Do not let COLLECT partially delete a distribution that is still running.
powershell -NoProfile -Command "if (Get-Process SeestarPlanEditor -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $env:BUILD_EXE }) { exit 1 }"
if errorlevel 1 (
    echo Close the running dist\SeestarPlanEditor application before building.
    exit /b 1
)

rem The spec defines onedir via EXE(exclude_binaries=True) and COLLECT.
rem --onedir cannot be passed when building an existing spec file.
"%BUILD_PYTHON%" -m PyInstaller --noconfirm "%~dp0SeestarPlanEditor.spec"
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo Build complete: %~dp0dist\SeestarPlanEditor\SeestarPlanEditor.exe
exit /b 0
