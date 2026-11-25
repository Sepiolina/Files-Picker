"""
Remembers settings and run history between sessions.

Everything lives in one folder (e.g. %APPDATA%/FilePicker on Windows,
~/Library/Application Support/FilePicker on macOS, ~/.local/share/FilePicker
on Linux):

    settings.json   last used job + window/theme state
    history.json    past runs (newest first, max 100)
    lists/<hash>.txt  name lists, stored once and shared by settings + history,
                      so a 180,000-line list isn't copied into every entry
"""

import copy
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QStandardPaths

MAX_HISTORY = 100


class Store:
    def __init__(self, root=None):
        if root is None:
            root = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        self.root = Path(root)
        self.lists = self.root / "lists"
        self.lists.mkdir(parents=True, exist_ok=True)
        self._settings_file = self.root / "settings.json"
        self._history_file = self.root / "history.json"

    # ---- settings -----------------------------------------------------------

    def load_settings(self):
        """Returns {"job": {...with list text...}, "ui": {...}}."""
        data = _read_json(self._settings_file, {})
        job = data.get("job") or {}
        if "listRef" in data:
            job.setdefault("list", {})["text"] = self._load_list(data["listRef"])
        return {"job": job, "ui": data.get("ui", {})}

    def save_settings(self, job, ui):
        job, ref = self._detach_list(job)
        _write_json(self._settings_file, {"job": job, "listRef": ref, "ui": ui})
        self._collect_garbage()

    # ---- history ------------------------------------------------------------

    def history(self):
        return _read_json(self._history_file, [])

    def add_run(self, job, status, summary):
        """Record a finished export. status: done | error | cancelled."""
        job, ref = self._detach_list(job)
        entry = {
            "id": uuid.uuid4().hex,
            "time": time.time(),
            "status": status,
            "job": job,
            "listRef": ref,
            "summary": summary,
        }
        runs = [entry] + self.history()
        _write_json(self._history_file, runs[:MAX_HISTORY])
        self._collect_garbage()
        return entry

    def job_for(self, entry):
        """The full job of a history entry, ready to load or run again."""
        job = copy.deepcopy(entry["job"])
        job.setdefault("list", {})["text"] = self._load_list(entry.get("listRef"))
        return job

    def remove_run(self, run_id):
        _write_json(self._history_file, [r for r in self.history() if r["id"] != run_id])
        self._collect_garbage()

    def clear_history(self):
        _write_json(self._history_file, [])
        self._collect_garbage()

    # ---- name lists (content-addressed) --------------------------------------

    def _detach_list(self, job):
        job = copy.deepcopy(job)
        text = job.get("list", {}).pop("text", "") or ""
        ref = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        path = self.lists / f"{ref}.txt"
        if not path.exists():
            _atomic_write(path, text.encode("utf-8"))
        return job, ref

    def _load_list(self, ref):
        try:
            return (self.lists / f"{ref}.txt").read_text(encoding="utf-8")
        except (OSError, TypeError):
            return ""

    def _collect_garbage(self):
        used = {r.get("listRef") for r in self.history()}
        used.add(_read_json(self._settings_file, {}).get("listRef"))
        for f in self.lists.glob("*.txt"):
            if f.stem not in used:
                try:
                    f.unlink()
                except OSError:
                    pass


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    _atomic_write(path, json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8"))


def _atomic_write(path, data):
    """Write to a temp file then rename, so a crash never corrupts settings."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
