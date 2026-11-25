#!/usr/bin/env bash
# Package File Picker into one program with GUI + engine inside.
#   Linux: dist/FilePicker            (single file)
#   macOS: dist/FilePicker.app        (app bundle)
# Windows: use package.cmd instead.
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
gui="$root/gui"

echo "[1/5] Building the engine";  "$root/build.sh"

echo "[2/5] Finding Python"
python=$(command -v python3 || command -v python || true)
[[ -n "$python" ]] || { echo "Python 3.9+ is needed"; exit 1; }
"$python" -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ is needed"'

echo "[3/5] Preparing the build environment (.venv-build)"
[[ -x "$root/.venv-build/bin/python" ]] || "$python" -m venv "$root/.venv-build"
vpy="$root/.venv-build/bin/python"
"$vpy" -m pip install --quiet --disable-pip-version-check -r "$gui/requirements.txt" pyinstaller

echo "[4/5] Bundling with PyInstaller"
# macOS gets an .app bundle (folder); Linux a single file. Exe icons are Windows-only.
if [[ "$(uname)" == "Darwin" ]]; then mode=--onedir; else mode=--onefile; fi
"$vpy" -m PyInstaller --noconfirm --clean "$mode" --windowed --name FilePicker \
  --add-binary "$gui/bin/picker:bin" --add-data "$gui/assets/icon.png:assets" \
  --paths "$gui" --exclude-module tkinter \
  --distpath "$root/dist" --workpath "$root/build/pyinstaller" --specpath "$root/build/pyinstaller" \
  "$gui/run_gui.py"

echo "[5/5] Checking the result"
if [[ "$(uname)" == "Darwin" ]]; then exe="$root/dist/FilePicker.app/Contents/MacOS/FilePicker"; else exe="$root/dist/FilePicker"; fi
"$exe" --check
echo "Done: $(ls -d "$root"/dist/FilePicker* | head -1)"
