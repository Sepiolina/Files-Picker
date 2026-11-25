"""
Bridge to the engine process.

The GUI never searches or copies files itself. It starts the engine
(`picker scan|export|inspect --stdin-control`), writes the job as one JSON line
to its stdin, and turns each JSON line the engine prints into a Qt signal.
Writing "cancel" to stdin stops the run cleanly (partial files are removed).
"""

import json
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

EXE = "picker.exe" if sys.platform == "win32" else "picker"


def app_dir():
    """The gui/ folder - or, inside a packaged .exe, the folder it unpacks to."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def find_engine():
    """Where to look for the engine binary, in order."""
    gui_dir = app_dir()
    candidates = [
        os.environ.get("PICKER_ENGINE"),
        gui_dir / "bin" / EXE,                     # built by build script / bundled
        gui_dir.parent / "engine" / "bin" / EXE,   # developer checkout
        shutil.which("picker"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    return None


def _hide_console_window(process):
    """
    The engine is a console program. When a windowed app (like the packaged
    FilePicker.exe) starts one, Windows may flash a black console window.
    CREATE_NO_WINDOW prevents that. Qt usually does this already; this makes sure.
    """
    if sys.platform != "win32" or not hasattr(process, "setCreateProcessArgumentsModifier"):
        return
    CREATE_NO_WINDOW = 0x08000000

    def modify(args):
        args.flags |= CREATE_NO_WINDOW

    try:
        process.setCreateProcessArgumentsModifier(modify)
    except (TypeError, AttributeError):
        pass


class EngineProcess(QObject):
    event = Signal(dict)   # every event the engine prints: {"type": ..., ...}
    finished = Signal()    # process ended (after the last event)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = None
        self._buffer = bytearray()
        self._scanned = 0  # bytes of _buffer already searched for a newline
        self._job_line = b""
        self._got_final_event = False

    def is_running(self):
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def start(self, command, job):
        """Start a run. Raises FileNotFoundError if the engine isn't built."""
        path = find_engine()
        if not path:
            raise FileNotFoundError(
                "The search engine was not found.\n\n"
                "Build it once with:  build.sh  (macOS/Linux)  or  build.cmd  (Windows)\n"
                "or set PICKER_ENGINE to the path of the picker executable.")
        self.kill()
        self._buffer = bytearray()
        self._scanned = 0
        self._got_final_event = False
        self._job_line = (json.dumps(job) + "\n").encode("utf-8")

        p = QProcess(self)
        p.setProgram(path)
        p.setArguments([command, "--stdin-control"])
        p.started.connect(lambda: p.write(self._job_line))
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._on_finished)
        p.errorOccurred.connect(self._on_error)
        _hide_console_window(p)
        self._proc = p
        p.start()

    def cancel(self):
        if self.is_running():
            self._proc.write(b"cancel\n")

    def kill(self):
        """Stop immediately without waiting (used for throw-away 'inspect' runs)."""
        if self._proc is not None:
            p, self._proc = self._proc, None
            for sig in ("readyReadStandardOutput", "finished", "errorOccurred"):
                try:
                    getattr(p, sig).disconnect()
                except (RuntimeError, TypeError):
                    pass
            if p.state() != QProcess.NotRunning:
                p.kill()
                p.waitForFinished(1000)
            p.deleteLater()

    # ---- internals ----------------------------------------------------------

    def _read(self):
        if self._proc is None:
            return
        # The preview event can be several MB, arriving in 64 KB chunks: only
        # search the new bytes for a newline, never re-copy the whole buffer.
        self._buffer += self._proc.readAllStandardOutput().data()
        while (end := self._buffer.find(b"\n", self._scanned)) >= 0:
            line = bytes(self._buffer[:end])
            del self._buffer[:end + 1]
            self._scanned = 0
            if line.strip():
                self._dispatch(line)
        self._scanned = len(self._buffer)

    def _dispatch(self, line):
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if ev.get("type") in ("done", "error", "cancelled"):
            self._got_final_event = True
        self.event.emit(ev)

    def _on_finished(self, code, _status):
        self._read()
        if self._buffer.strip():
            self._dispatch(bytes(self._buffer))
            self._buffer = bytearray()
        if not self._got_final_event and code != 0 and self._proc is not None:
            err = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace").strip()
            self.event.emit({"type": "error", "message": err or f"The engine stopped unexpectedly (code {code})."})
        self.finished.emit()

    def _on_error(self, error):
        if error == QProcess.FailedToStart:
            self.event.emit({"type": "error", "message": "Could not start the engine: " + self._proc.errorString()})
            self._got_final_event = True
            self.finished.emit()
