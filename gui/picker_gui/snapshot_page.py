"""
Snapshots tab: scan a folder (and every sub-folder) and keep the result as a log.
Scan the same folder again later and see exactly what was added, removed,
modified or moved since the previous scan - or since any older one.
"""

import csv
import os
from collections import namedtuple
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QMessageBox, QProgressBar, QScrollArea,
                               QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from . import charts, fscore as F, report as R
from .jobs import Worker, describe
from .models import TableModel
from .preview import PreviewPanel, attach_preview
from .result_table import ResultTable
from .util import fmt_duration, fmt_int, fmt_size, open_file, reveal, scan_note
from .widgets import Banner, Chip, EmptyState, Panel, PathField, Segmented, StatTile, button, hbox, label

Change = namedtuple("Change", "kind rel size mtime detail")
KIND = {"added": ("Added", "success"), "removed": ("Removed", "danger"), "modified": ("Modified", "warn"),
        "moved": ("Moved", "#8B5CF6")}
KIND_RANK = {"added": 0, "removed": 1, "modified": 2, "moved": 3}


def when(t, fmt="%d %b %Y, %H:%M"):
    return datetime.fromtimestamp(t).strftime(fmt)


def fmt_mtime(ns):
    return datetime.fromtimestamp(ns / 1e9).strftime("%d %b %Y %H:%M") if ns else ""


class SnapshotModel(TableModel):
    COLUMNS = ["Scanned", "Folder", "Files", "Size", "Changes since previous", "Contents", "Took"]

    def cell(self, m, c):
        if c == 0:
            return when(m["time"])
        if c == 1:
            return m["root"]
        if c == 2:
            return fmt_int(m["files"])
        if c == 3:
            return fmt_size(m["bytes"])
        if c == 4:
            s = m.get("summary")
            if not s:
                return "First scan"
            parts = [f"+{s['added']}" if s["added"] else "", f"−{s['removed']}" if s["removed"] else "",
                     f"~{s['modified']}" if s["modified"] else "", f"↔{s['moved']}" if s["moved"] else ""]
            return "  ".join(p for p in parts if p) or "No changes"
        if c == 5:
            return "Checksums" if m.get("hashed") else "Size + date"
        return fmt_duration(m["elapsed"] * 1000)

    def sort_key(self, m, c):
        return {0: m["time"], 2: m["files"], 3: m["bytes"], 6: m["elapsed"]}.get(c, self.cell(m, c).lower())

    def color(self, m, c):
        if c == 4:
            s = m.get("summary")
            return "muted" if not s else ("success" if not any(s[k] for k in ("added", "removed", "modified", "moved"))
                                          else "warn")
        return "muted" if c in (0, 3, 5, 6) else None

    def is_numeric(self, c):
        return c in (2, 3, 6)


class ChangeModel(TableModel):
    COLUMNS = ["Change", "File", "Size", "Details", "Modified"]

    def cell(self, r, c):
        return [KIND[r.kind][0], r.rel, fmt_size(r.size) if r.size is not None else "–", r.detail,
                fmt_mtime(r.mtime) or "–"][c]

    def sort_key(self, r, c):
        return {0: KIND_RANK[r.kind], 2: r.size or 0, 4: r.mtime or 0}.get(c, self.cell(r, c).lower())

    def color(self, r, c):
        return KIND[r.kind][1] if c == 0 else ("muted" if c in (2, 3, 4) else None)

    def is_numeric(self, c):
        return c == 2


def build_changes(changes, old, new):
    """diff_files() output -> flat list of Change rows."""
    rows = []
    for rel in changes["added"]:
        s, m, _ = new[rel]
        rows.append(Change("added", rel, s, m, ""))
    for rel in changes["removed"]:
        s, m, _ = old[rel]
        rows.append(Change("removed", rel, s, m, ""))
    for rel in changes["modified"]:
        (s1, _, h1), (s2, m2, h2) = old[rel], new[rel]
        if s1 != s2:
            d = f"{'+' if s2 > s1 else '−'}{fmt_size(abs(s2 - s1))}"
        else:
            d = "content changed" if (h1 and h2) else "date changed"
        rows.append(Change("modified", rel, s2, m2, d))
    for a, b in changes["moved"]:
        s, m, _ = new[b]
        rows.append(Change("moved", f"{a}  →  {b}", s, m, "renamed or moved"))
    return rows


class SnapshotPage(QWidget):
    changed = Signal()
    settings_requested = Signal()
    count_changed = Signal(int)

    def __init__(self, store_root, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.snaps = F.SnapshotStore(store_root)
        self.worker = self.diff_worker = None
        self._files = {}        # snapshot id -> files (small in-memory cache)
        self._token = 0

        # ---- controls --------------------------------------------------------
        self.path = PathField(PathField.FOLDER, "Folder to scan — everything inside it is logged")
        self.path.changed.connect(lambda _=None: self.changed.emit())
        self.hashed = QCheckBox("Read file contents (exact change && move detection, slower)")
        self.hashed.setToolTip("Computes a checksum for each file. Unchanged files are not read again on later scans.")
        self.skip_hidden = QCheckBox("Skip hidden && system files")
        self.skip_hidden.setChecked(True)
        self.scan_btn = button("Scan folder", "scan", "Primary")
        self.scan_btn.setProperty("large", True)
        self.scan_btn.setMinimumWidth(140)
        self.cancel_btn = button("Cancel", "x")
        self.cancel_btn.hide()
        self.scan_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.options_btn = button("Ignore rules && scan options…", kind="Link")
        self.options_btn.clicked.connect(self.settings_requested.emit)

        controls = Panel(margins=(18, 16, 18, 16))
        controls.lay.setSpacing(12)
        controls.lay.addWidget(label("Folder", "FieldLabel"))
        controls.lay.addWidget(self.path)
        controls.lay.addLayout(hbox(self.hashed, 12, self.skip_hidden, 4, self.options_btn, None, self.cancel_btn,
                                    self.scan_btn, spacing=10))

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.status = label("", "Hint")
        self.banner = Banner()

        # ---- snapshot list ----------------------------------------------------------
        self.table = ResultTable(SnapshotModel(), path_cols=(1,), stretch_col=1, sort=(0, Qt.DescendingOrder),
                                 placeholder="Filter scans…", min_height=120)
        self.table.selection_changed.connect(self._selected_changed)

        # ---- details ------------------------------------------------------------------
        self.d_title = label("", "H2")
        self.d_sub = label("", "Hint", wrap=True)
        self.again_btn = button("Scan again", "refresh", "Primary",
                                tooltip="Scan this folder now and compare with the previous scan")
        self.reveal_btn = button("Open folder", "folder")
        self.log_btn = button("Export log", "download", tooltip="Every file in this scan, as CSV")
        self.changes_btn = button("Export changes", "download", tooltip="The changes listed below, as CSV")
        self.report_btn = button("Report", "file-text", tooltip="Save a shareable HTML or PDF report")
        self.report_btn.clicked.connect(self._report)
        self.delete_btn = button("Delete", "trash", "Danger")
        self.again_btn.clicked.connect(self._scan_again)
        self.reveal_btn.clicked.connect(lambda: self._sel() and reveal(self._sel()["root"]))
        self.log_btn.clicked.connect(self._export_log)
        self.changes_btn.clicked.connect(self._export_changes)
        self.delete_btn.clicked.connect(self._delete)
        self.baseline = QComboBox()
        self.baseline.setMinimumWidth(340)
        self.baseline.activated.connect(lambda _: self._load_diff())

        self.tiles = {}
        tile_row = hbox(spacing=12)
        for key in ("added", "removed", "modified", "moved"):
            t = StatTile(KIND[key][0], clickable=True)
            t.clicked.connect(lambda k=key: self.changes.set_status(k, KIND[k][0]))
            self.tiles[key] = t
            tile_row.addWidget(t, 1)
        self.tiles["unchanged"] = StatTile("Unchanged")
        tile_row.addWidget(self.tiles["unchanged"], 1)

        self.trend_mode = Segmented([("files", "Files"), ("bytes", "Size")])
        self.trend_mode.setValue("files")
        self.trend_mode.changed.connect(lambda _: self._draw_trend())
        self.trend = charts.TrendChart()
        trend_panel = Panel(margins=(18, 14, 18, 12))
        trend_panel.lay.addLayout(hbox(label("This folder over time", "SectionTitle"), None, self.trend_mode))
        trend_panel.lay.addSpacing(4)
        trend_panel.lay.addWidget(self.trend)

        self.changes = ResultTable(ChangeModel(), path_cols=(1,), stretch_col=1, sort=(0, Qt.AscendingOrder),
                                   placeholder="Filter changes…", min_height=300)
        self.changes.activated.connect(self._reveal_change)
        self.preview = PreviewPanel()
        self.changes_split, self.preview_btn = attach_preview(self.changes, self.preview)
        self.changes.selection_changed.connect(self._change_selected)
        self.note = label("", "Muted", wrap=True)

        details = QWidget()
        details.setObjectName("Workspace")
        details.setAttribute(Qt.WA_StyledBackground, True)
        dl = QVBoxLayout(details)
        dl.setContentsMargins(0, 8, 6, 0)
        dl.setSpacing(12)
        dl.addLayout(hbox(self.d_title, None, self.delete_btn, self.reveal_btn, self.log_btn, self.again_btn))
        dl.addWidget(self.d_sub)
        dl.addLayout(hbox(label("Compare with", "FieldLabel"), self.baseline, None, self.preview_btn, self.report_btn,
                          self.changes_btn, spacing=10))
        dl.addLayout(tile_row)
        dl.addWidget(self.note)
        dl.addWidget(trend_panel)
        dl.addWidget(self.changes_split)
        scroll = QScrollArea()
        scroll.setObjectName("Scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setWidget(details)

        split = QSplitter(Qt.Vertical)
        split.setHandleWidth(8)
        split.setChildrenCollapsible(False)
        split.addWidget(self.table)
        split.addWidget(scroll)
        split.setSizes([210, 520])
        self.stack = QStackedWidget()
        self.stack.addWidget(EmptyState(
            "No scans yet",
            "Scan a folder to log every file in it and its sub-folders. Scan it again any time to see what changed.",
            "camera"))
        self.stack.addWidget(split)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("Snapshots", "H1"))
        heading.addWidget(label("Log a folder and everything below it — then scan again to see what changed.", "Muted"))
        lay.addLayout(heading)
        lay.addWidget(controls)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addWidget(self.banner)
        lay.addWidget(self.stack, 1)
        self.progress.hide()
        self.status.hide()
        self.refresh()

    # ---- state ----------------------------------------------------------------

    def state(self):
        return {"path": self.path.text(), "hashed": self.hashed.isChecked(), "skip_hidden": self.skip_hidden.isChecked(),
                "preview": self.preview_btn.isChecked()}

    def restore(self, s):
        self.path.setText(s.get("path", ""))
        self.hashed.setChecked(s.get("hashed", False))
        self.skip_hidden.setChecked(s.get("skip_hidden", True))
        self.preview_btn.setChecked(s.get("preview", False))

    def stop_background_work(self):
        for w in (self.worker, self.diff_worker):
            if w:
                w.stop()

    def is_running(self):
        return self.worker is not None and self.worker.isRunning()

    # ---- list ------------------------------------------------------------------

    def refresh(self, select_id=None):
        metas = self.snaps.list()
        self.table.set_rows(metas)
        self.stack.setCurrentIndex(1 if metas else 0)
        self.count_changed.emit(len(metas))
        if metas:
            target = select_id or (self._sel() or {}).get("id") or max(metas, key=lambda m: m["time"])["id"]
            for i, m in enumerate(self.table.model.rows):
                if m["id"] == target:
                    self.table.view.selectRow(self.table.proxy.mapFromSource(self.table.model.index(i, 0)).row())
                    break
        self._selected_changed()

    def _sel(self):
        return self.table.selected()

    def _selected_changed(self):
        m = self._sel()
        for b in (self.again_btn, self.reveal_btn, self.log_btn, self.changes_btn, self.delete_btn, self.report_btn):
            b.setEnabled(m is not None)
        if not m:
            return
        self.d_title.setText(os.path.basename(m["root"].rstrip("/\\")) or m["root"])
        self.reveal_btn.setEnabled(os.path.isdir(m["root"]))
        self.again_btn.setEnabled(os.path.isdir(m["root"]))
        self.d_sub.setText(f"{m['root']}  ·  scanned {when(m['time'], '%A %d %B %Y, %H:%M')}  ·  "
                           f"{fmt_int(m['files'])} files in {fmt_int(m['dirs'])} folders, {fmt_size(m['bytes'])}"
                           + (f"  ·  {fmt_int(m['errors'])} unreadable folders skipped" if m.get("errors") else ""))
        same = sorted((x for x in self.snaps.list() if F._same_path(x["root"], m["root"]) and x["time"] < m["time"]),
                      key=lambda x: -x["time"])
        self.baseline.blockSignals(True)
        self.baseline.clear()
        for i, x in enumerate(same):
            self.baseline.addItem(("Previous scan · " if i == 0 else "") + when(x["time"]), x["id"])
        self.baseline.setEnabled(bool(same))
        self.baseline.blockSignals(False)
        self._draw_trend()
        self._load_diff()

    def _draw_trend(self):
        m = self._sel()
        if not m:
            return
        key = self.trend_mode.value()
        same = sorted((x for x in self.snaps.list() if F._same_path(x["root"], m["root"])), key=lambda x: x["time"])
        self.trend.set_data([(when(x["time"], "%d %b %H:%M"), x[key]) for x in same],
                            fmt_int if key == "files" else fmt_size)

    # ---- diff -------------------------------------------------------------------

    def _files_of(self, snap_id):
        if snap_id not in self._files:
            if len(self._files) >= 3:
                self._files.pop(next(iter(self._files)))
            self._files[snap_id] = self.snaps.load_files(snap_id)
        return self._files[snap_id]

    def _load_diff(self):
        m = self._sel()
        if not m:
            return
        base = self.baseline.currentData() if self.baseline.isEnabled() else None
        self._token += 1
        token = self._token
        for k, t in self.tiles.items():
            t.set("…")
        if base is None:
            self.note.setText("This is the first scan of this folder. Scan it again later and the differences will "
                              "appear here.")
            self.note.show()
            self.changes.set_rows([], status_of=lambda r: r.kind)
            self.tiles["unchanged"].set(fmt_int(m["files"]))
            for k in ("added", "removed", "modified", "moved"):
                self.tiles[k].set("–")
            return
        self.note.hide()

        def job(cancel, progress):
            old, new = self._files_of(base), self._files_of(m["id"])
            changes = F.diff_files(old, new)
            return token, changes, build_changes(changes, old, new)

        self.diff_worker = Worker(job, self)
        self.diff_worker.done.connect(self._diff_done)
        self.diff_worker.failed.connect(lambda msg: self.banner.show_message("danger", "Could not read the scan", msg))
        self.diff_worker.start()

    def _diff_done(self, res):
        token, changes, rows = res
        if token != self._token:
            return  # the selection changed while this was loading
        for k in ("added", "removed", "modified", "moved"):
            self.tiles[k].set(fmt_int(len(changes[k])), None)
        self.tiles["unchanged"].set(fmt_int(changes["unchanged"]))
        self.changes.set_rows(rows, status_of=lambda r: r.kind)
        if not rows:
            self.note.setText("Nothing changed between these two scans.")
            self.note.show()

    # ---- scanning ----------------------------------------------------------------

    def start(self):
        if self.is_running():
            return
        folder = self.path.text()
        if not folder or not os.path.isdir(folder):
            self.banner.show_message("danger", "Choose a folder to scan first")
            return
        self.banner.hide()
        hashed, hidden = self.hashed.isChecked(), self.skip_hidden.isChecked()
        self.worker = Worker(lambda cancel, prog: self.snaps.scan_and_save(folder, hashed, hidden, cancel, prog), self)
        self.worker.progress.connect(lambda p: self.status.setText(describe(p)))
        self.worker.done.connect(self._scan_done)
        self.worker.failed.connect(lambda m: (self._busy(False), self.banner.show_message("danger", "Scan failed", m)))
        self.worker.cancelled.connect(lambda: (self._busy(False), self.banner.show_message("warn", "Scan cancelled",
                                                                                               "Nothing was saved.")))
        self._busy(True)
        self.worker.start()

    def _scan_again(self):
        m = self._sel()
        if m:
            self.path.setText(m["root"])
            self.hashed.setChecked(bool(m.get("hashed")))
            self.start()

    def _busy(self, busy):
        self.progress.setVisible(busy)
        self.status.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.scan_btn.setEnabled(not busy)
        self.again_btn.setEnabled(not busy and self._sel() is not None)
        if busy:
            self.status.setText("Starting…")

    def _scan_done(self, res):
        self._busy(False)
        meta = res["meta"]
        self._files[meta["id"]] = res["files"]
        if res["prev_files"] is not None and meta["prev"]:
            self._files.setdefault(meta["prev"], res["prev_files"])
        self.refresh(select_id=meta["id"])
        s = meta.get("summary")
        if s is None:
            self.banner.show_message("success", f"Saved — {fmt_int(meta['files'])} files logged",
                                     "This is the first scan of this folder. Scan it again later to see what changed. "
                                     + scan_note(meta.get("cloud", 0), meta.get("ignored", 0)))
        else:
            total = s["added"] + s["removed"] + s["modified"] + s["moved"]
            self.banner.show_message(
                "warn" if total else "success",
                f"{fmt_int(total)} changes since the previous scan" if total else "No changes since the previous scan",
                (f"{fmt_int(s['added'])} added · {fmt_int(s['removed'])} removed · {fmt_int(s['modified'])} modified · "
                 f"{fmt_int(s['moved'])} moved. " if total else "") + scan_note(meta.get("cloud", 0), meta.get("ignored", 0)))

    # ---- actions --------------------------------------------------------------------

    def _delete(self):
        m = self._sel()
        if m and QMessageBox.question(self, "Delete scan", f"Delete the scan from {when(m['time'])}?\n"
                                      "Your files are not touched.") == QMessageBox.Yes:
            self.snaps.delete(m["id"])
            self._files.pop(m["id"], None)
            self.refresh()

    def _reveal_change(self, row):
        m = self._sel()
        if m:
            rel = row.rel.split("  →  ")[-1]
            path = os.path.join(m["root"], rel.replace("/", os.sep))
            if os.path.exists(path):
                reveal(path)

    def _change_selected(self):
        row, m = self.changes.selected(), self._sel()
        if row is None or m is None:
            return
        if row.kind == "removed":
            self.preview.clear("This file was removed — it is no longer on disk")
        else:
            rel = row.rel.split("  →  ")[-1]
            self.preview.show_path(os.path.join(m["root"], rel.replace("/", os.sep)))

    def _report(self):
        m = self._sel()
        if not m:
            return
        rows = self.changes.model.rows
        counts = {k: sum(1 for r in rows if r.kind == k) for k in KIND}
        base = self.baseline.currentText() if self.baseline.isEnabled() else ""
        rep = R.Report(f"Folder changes — {os.path.basename(m['root'].rstrip('/\\')) or m['root']}", m["root"])
        rep.text(f"Scanned {when(m['time'], '%A %d %B %Y, %H:%M')}: {fmt_int(m['files'])} files in {fmt_int(m['dirs'])} "
                 f"folders, {fmt_size(m['bytes'])}." + (f"  Compared with: {base}." if base else ""))
        if base:
            rep.tiles([(KIND[k][0], fmt_int(counts[k])) for k in KIND])
        same = sorted((x for x in self.snaps.list() if F._same_path(x["root"], m["root"])), key=lambda x: x["time"])
        rep.heading("This folder over time")
        rep.svg(R.svg_trend([(when(x["time"], "%d %b %H:%M"), x["files"]) for x in same], fmt=fmt_int), 640)
        rep.svg(R.svg_trend([(when(x["time"], "%d %b %H:%M"), x["bytes"]) for x in same], fmt=fmt_size), 640)
        rep.heading("Changes")
        rep.table(["Change", "File", "Size", "Details"],
                  [[KIND[r.kind][0], r.rel, fmt_size(r.size) if r.size is not None else "–", r.detail] for r in
                   sorted(rows, key=lambda r: (KIND_RANK[r.kind], r.rel.lower()))], ["l", "l", "r", "l"],
                  note=scan_note(m.get("cloud", 0), m.get("ignored", 0)) or ("" if base else "First scan of this folder."))
        try:
            path = R.ask_and_save(self, rep, f"changes_{when(m['time'], '%Y%m%d_%H%M')}")
        except OSError as e:
            self.banner.show_message("danger", "Could not save the report", str(e))
            return
        if path:
            self.banner.show_message("success", "Report saved", path, [("Open", lambda: open_file(path))])

    def _save_csv(self, default, header, rows):
        path, _ = QFileDialog.getSaveFileName(self, "Export", default, "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(rows)
        except OSError as e:
            self.banner.show_message("danger", "Could not save the file", str(e))
            return
        self.banner.show_message("success", "Exported", path, [("Open", lambda: open_file(path))])

    def _export_log(self):
        m = self._sel()
        if not m:
            return
        try:
            files = self._files_of(m["id"])
        except (OSError, ValueError) as e:
            self.banner.show_message("danger", "Could not read the scan", str(e))
            return
        self._save_csv(f"scan_{when(m['time'], '%Y%m%d_%H%M')}.csv",
                       ["Path", "Size (bytes)", "Modified", "Checksum"],
                       ([rel, s, fmt_mtime(mt), h or ""] for rel, (s, mt, h) in sorted(files.items())))

    def _export_changes(self):
        m = self._sel()
        if m:
            self._save_csv(f"changes_{when(m['time'], '%Y%m%d_%H%M')}.csv", ["Change", "Path", "Size (bytes)", "Details", "Modified"],
                           ([KIND[r.kind][0], r.rel, r.size if r.size is not None else "", r.detail, fmt_mtime(r.mtime)]
                            for r in self.changes.visible_rows()))
