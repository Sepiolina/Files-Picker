"""Run a long Python function off the UI thread, with progress and cancel."""

import threading

from PySide6.QtCore import QThread, Signal

from .fscore import Cancelled
from .util import fmt_int, fmt_size


class Worker(QThread):
    progress = Signal(dict)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = self._fn(self.cancel_event, self.progress.emit)
        except Cancelled:
            self.cancelled.emit()
        except Exception as e:  # shown to the user, never crashes the app
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.done.emit(result)

    def cancel(self):
        self.cancel_event.set()

    def stop(self, wait_ms=3000):
        """Cancel and wait (used when the window closes)."""
        if self.isRunning():
            self.cancel_event.set()
            self.wait(wait_ms)


def describe(p):
    """One-line status text from a progress dict emitted by fscore."""
    phase = p.get("phase") or "Working…"
    if p.get("hash_total"):
        return f"{phase}  {fmt_int(p.get('hashed'))} / {fmt_int(p['hash_total'])} files"
    if "files" in p:
        extra = f" · {fmt_size(p['bytes'])}" if "bytes" in p else ""
        return f"{phase}  {fmt_int(p['files'])} files{extra}"
    return phase
