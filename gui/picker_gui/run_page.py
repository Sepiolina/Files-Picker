"""
The Run page: form on the left, results on the right, actions at the bottom.

The action bar always says what the primary button will do
("4 names → matched_files.zip") and the button is named after the action
("Export zip", "Copy to folder").

Shortcuts:  Ctrl+Enter = Export · Ctrl+P = Preview · Esc = Cancel
"""

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QMessageBox, QScrollArea, QSplitter, QVBoxLayout, QWidget

from . import options as O
from .engine import EngineProcess
from .form_panel import FormPanel
from .results_panel import ResultsPanel
from .util import fmt_duration, fmt_int, open_file, reveal
from .widgets import ElidedLabel, button, hbox


class RunPage(QWidget):
    run_recorded = Signal(dict, str, dict)  # job, status, summary -> saved to history
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.engine = EngineProcess(self)
        self.engine.event.connect(self._on_event)
        self.engine.finished.connect(self._on_finished)
        self._command = self._job = self._preview = self._final = None

        # Left: scrollable form + sticky action bar
        self.form = FormPanel()
        self.form.changed.connect(self.settings_changed)
        self.form.changed.connect(self._refresh_context)
        self.form.stats_changed.connect(self._refresh_context)
        scroll = QScrollArea()
        scroll.setObjectName("Scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.form)

        # Action bar: what will happen (left) · Preview · Export (primary, largest)
        self.context_title = ElidedLabel("", Qt.ElideRight, "SectionTitle")
        self.context = ElidedLabel("", Qt.ElideRight, "Hint")
        context = QVBoxLayout()
        context.setSpacing(1)
        context.addWidget(self.context_title)
        context.addWidget(self.context)
        self.preview_btn = button("Preview", "eye", tooltip="See what matches — nothing is copied  (Ctrl+P)")
        self.export_btn = button("Export", "download", "Primary",
                                 tooltip="Find and export the matching files  (Ctrl+Enter)")
        self.export_btn.setMinimumWidth(150)
        for b in (self.preview_btn, self.export_btn):
            b.setProperty("large", True)
        self.preview_btn.clicked.connect(lambda: self.start("scan"))
        self.export_btn.clicked.connect(lambda: self.start("export"))
        bar = QFrame()
        bar.setObjectName("ActionBar")
        bar_lay = hbox(context, 16, self.preview_btn, self.export_btn, spacing=10, margins=(20, 14, 20, 14))
        bar_lay.setStretch(0, 1)
        bar.setLayout(bar_lay)

        left = QWidget()
        left.setObjectName("Sidebar")
        left.setAttribute(Qt.WA_StyledBackground, True)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addWidget(scroll, 1)
        ll.addWidget(bar)
        left.setMinimumWidth(440)

        # Right: results
        self.results = ResultsPanel()
        self.results.cancel_btn.clicked.connect(self.cancel)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(1)
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.results)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([480, 800])
        self.splitter.setChildrenCollapsible(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.splitter)

        QShortcut(QKeySequence("Ctrl+Return"), self, lambda: self.start("export"))
        QShortcut(QKeySequence("Ctrl+Enter"), self, lambda: self.start("export"))
        QShortcut(QKeySequence("Ctrl+P"), self, lambda: self.start("scan"))
        QShortcut(QKeySequence("Esc"), self, self.cancel)
        self._refresh_context()

    # ---- public -------------------------------------------------------------------

    def job(self):
        return self.form.job()

    def set_job(self, job):
        self.form.set_job(job)

    def is_running(self):
        return self.engine.is_running()

    def start(self, command):
        if self.is_running():
            return
        if not self._validate(command, self.form.job()):
            return
        job = self.form.job()  # re-read: _validate may have asked for the output path
        try:
            self.engine.start(command, job)
        except FileNotFoundError as e:
            self.results.banner.show_message("danger", "The search engine isn't built yet", str(e))
            return
        self._command, self._job, self._preview, self._final = command, job, None, None
        self._set_busy(True)
        self.results.begin(command)

    def cancel(self):
        if self.is_running():
            self.results.status.setText("Cancelling…")
            self.results.cancel_btn.setEnabled(False)
            self.engine.cancel()

    # ---- context: action bar + checklist ------------------------------------------

    def _refresh_context(self):
        o = self.form.output_settings()
        n = self.form.unique_count
        if not self.form.has_source():
            title, text = "Not ready yet", "Choose a folder first"
        elif not n:
            title, text = "Almost ready", "Add the names to find"
        else:
            names = f"{fmt_int(n)} {'name' if n == 1 else 'names'}"
            title = "Ready"
            text = f"{names} → {Path(o['path']).name if o['path'] else 'you pick where to save'}"
        self.context_title.setText(title)
        self.context.setText(text)
        self.export_btn.setText(O.EXPORT_VERB[o["type"]])
        self.results.set_checklist(self.form.has_source(), n > 0)

    def _set_busy(self, busy):
        self.form.set_enabled(not busy)
        self.preview_btn.setEnabled(not busy)
        self.export_btn.setEnabled(not busy)

    # ---- validation: catch problems before starting, with a clear next step --------

    def _validate(self, command, job):
        def problem(title, body):
            self.results.banner.show_message("warn", title, body)
            return False

        if not job["source"]:
            self.form.source.edit.setFocus()
            return problem("Choose a folder to search", "Use Browse…, paste a path, or drop a folder onto the field.")
        if not Path(job["source"]).is_dir():
            return problem("That folder doesn't exist", job["source"])
        if not job["list"]["text"].strip():
            self.form.list_edit.setFocus()
            return problem("Add the names to find", "Paste them one per line, or import a .txt / .csv file.")
        if command == "scan":
            return True

        out = job["output"]
        if not out["path"]:
            if not self.form.out_path.browse():  # ask right away instead of showing an error
                return False
            out["path"] = self.form.out_path.text()
        if out["type"] == "folder":
            if os.path.abspath(out["path"]) == os.path.abspath(job["source"]):
                return problem("Pick a different output folder", "It can't be the folder being searched.")
        else:
            parent = Path(out["path"]).parent
            if not parent.is_dir():
                return problem("The output folder doesn't exist", str(parent))
            if Path(out["path"]).exists():
                answer = QMessageBox.question(self, "Replace file?",
                                              f"“{Path(out['path']).name}” already exists.\nDo you want to replace it?",
                                              QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
                if answer != QMessageBox.Yes:
                    return False
        return True

    # ---- engine events ----------------------------------------------------------------

    def _on_event(self, ev):
        kind = ev["type"]
        if kind == "progress":
            self.results.on_progress(ev)
        elif kind == "preview":
            self._preview = ev
            self.results.on_preview(ev)
        elif kind in ("done", "error", "cancelled"):
            self._final = ev

    def _on_finished(self):
        self.results.end()
        self._set_busy(False)
        ev = self._final or {"type": "error", "message": "The engine stopped unexpectedly."}
        self._show_outcome(ev)
        if self._command == "export":
            status = {"done": "done", "cancelled": "cancelled"}.get(ev["type"], "error")
            self.run_recorded.emit(self._job, status, self._summary(ev))

    def _show_outcome(self, ev):
        """The banner says what happened and what to do next; the tiles hold the numbers."""
        b = self.results.banner
        if ev["type"] == "cancelled":
            b.show_message("warn", "Stopped", "Nothing was left half-written.")
            return
        if ev["type"] == "error":
            b.show_message("danger", "Couldn't finish", ev["message"])
            return

        warnings = ev.get("warnings") or []
        skipped = (f"{len(warnings)} file(s) couldn't be read and were skipped — see the report."
                   if warnings else "")
        took = fmt_duration(ev.get("elapsedMs"))
        p = self._preview or {}

        if self._command == "scan":
            if not p.get("matched"):
                b.show_message("warn", "No files match",
                               "Try “Anywhere” matching, check “Compare with”, or choose another folder.")
            else:
                nf = p.get("notFound", 0)
                missing = f" {fmt_int(nf)} {'name' if nf == 1 else 'names'} found nothing." if nf else ""
                b.show_message(None, "Preview ready — nothing has been copied yet",
                               f"Searched in {took}.{missing} {skipped}".strip(),
                               [(self.export_btn.text(), lambda: self.start("export"))])
            return

        if not ev.get("exported"):
            b.show_message("warn", "No files match — nothing was exported", skipped)
            return
        actions = [("Show in folder", lambda p=ev["output"]: reveal(p))]
        reports = ev.get("reports") or []
        if reports:
            actions.append(("Open report", lambda p=reports[-1]: open_file(p)))
        b.show_message("warn" if warnings else "success",
                       "Export complete" + (" with skipped files" if warnings else ""),
                       f"{Path(ev['output']).name}  ·  in {Path(ev['output']).parent}  ·  {took}  {skipped}".strip(),
                       actions)

    def _summary(self, ev):
        p = self._preview or {}
        return {
            "names": len(p.get("names", [])),
            "matched": p.get("matched", 0),
            "notFound": p.get("notFound", 0),
            "checked": p.get("checked", 0),
            "exported": ev.get("exported", 0),
            "bytes": ev.get("bytes", 0),
            "elapsedMs": ev.get("elapsedMs", 0),
            "reports": ev.get("reports", []),
            "warnings": len(ev.get("warnings") or []),
            "message": ev.get("message", ""),
        }
