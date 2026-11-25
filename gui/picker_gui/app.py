"""
Application entry point.

    python gui/run_gui.py            start the app
    FilePicker.exe --check           print where the engine is and exit (diagnostics)
"""

import subprocess
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import theme
from .engine import app_dir, find_engine
from .main_window import MainWindow
from .store import Store


def _say(text):
    # A windowed .exe has no console (sys.stdout is None) - the exit code still tells.
    if sys.stdout:
        print(text)


def check():
    """
    Self-test used by the packaging scripts and CI. Exit code 0 = everything works:
      * the bundled/built engine is found and runs,
      * it has the tree/hash commands the analysis tabs use (and gives the same answers as the Python fallback),
      * the Qt pieces needed for reports (SVG + PDF) are present in the bundle.
    """
    import os
    import tempfile

    from . import fscore

    path = find_engine()
    if not path:
        _say("engine: NOT FOUND")
        return 1
    try:
        version = subprocess.run([path, "version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except OSError as e:
        _say(f"engine: {path}\nerror:  {e}")
        return 1
    _say(f"engine:  {path}\nversion: {version}")
    if not version:
        return 1

    ok, _version, _path = fscore.engine_status(refresh=True)
    if not ok:
        _say("analysis: engine is too old for the Compare / Snapshots / Duplicates / Space / Find tabs (needs 2.1+)")
        return 2
    with tempfile.TemporaryDirectory() as d:
        for rel, data in (("a.txt", b"hello"), ("sub/b.txt", b"hello"), ("sub/c.bin", b"x" * 70000)):
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            with open(os.path.join(d, rel), "wb") as f:
                f.write(data)
        fast = fscore.walk(d, use_engine=True)
        slow = fscore.walk(d, use_engine=False)
        rels = sorted(fscore.flatten(slow["dirs"]))
        same_tree = fast["engine"] and fscore.flatten(fast["dirs"]) == fscore.flatten(slow["dirs"])
        same_hash = (fscore.hash_many(d, rels, use_engine=True) == fscore.hash_many(d, rels, use_engine=False)
                     and fscore.hash_many(d, rels, partial=1000, use_engine=True)
                     == fscore.hash_many(d, rels, partial=1000, use_engine=False))
        dupes = fscore.find_duplicates(d)["stats"]["groups"]
    _say(f"analysis: tree {'ok' if same_tree else 'MISMATCH'}, hash {'ok' if same_hash else 'MISMATCH'}, "
         f"duplicates {'ok' if dupes == 1 else 'WRONG'}")
    if not (same_tree and same_hash and dupes == 1):
        return 3
    try:
        from PySide6 import QtSvg  # noqa: F401  (report charts)
        from PySide6.QtGui import QPdfWriter  # noqa: F401  (PDF reports)
    except ImportError as e:
        _say(f"reports: missing Qt module ({e})")
        return 4
    _say("reports:  ok")
    return 0


def main():
    if "--check" in sys.argv:
        return check()
    app = QApplication(sys.argv)
    app.setOrganizationName("FilePicker")
    app.setApplicationName("FilePicker")  # decides where settings/history are stored
    app.setWindowIcon(QIcon(str(app_dir() / "assets" / "icon.png")))
    store = Store()
    theme.apply(app, store.load_settings()["ui"].get("theme", "system"))
    window = MainWindow(store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
