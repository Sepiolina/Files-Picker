"""
Snapshots tab: scan a folder (and every sub-folder) and keep the result as a log.
Scan the same folder again later and see exactly what was added, removed,
modified or moved since the previous scan - or since any older one.
"""

import csv
import os
from collections import namedtuple
from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QProgressBar, QSizePolicy, QSplitter, QStackedWidget,
                               QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import charts, fscore as F, report as R, theme
from .jobs import Worker, describe
from .models import TableModel
from .preview import PreviewPanel, attach_preview
from .result_table import ResultTable
from .util import fmt_duration, fmt_int, fmt_size, open_file, reveal, scan_note
from .widgets import Banner, ElidedLabel, EmptyState, Panel, PathField, Segmented, StatTile, button, hbox, label

Change = namedtuple("Change", "kind rel size mtime detail")
KIND = {"added": ("Added", "success"), "removed": ("Removed", "danger"), "modified": ("Modified", "warn"),
        "moved": ("Moved", "#8B5CF6")}
KIND_RANK = {"added": 0, "removed": 1, "modified": 2, "moved": 3}


def when(t, fmt="%d %b %Y, %H:%M"):
    return datetime.fromtimestamp(t).strftime(fmt)


def when_short(t):
    """'25 Nov, 12:38' for this year, '25 Nov 2025, 12:38' otherwise - short enough for a narrow combo box."""
    same_year = datetime.fromtimestamp(t).year == datetime.now().year
    return when(t, "%d %b, %H:%M" if same_year else "%d %b %Y, %H:%M")


def fmt_mtime(ns):
    return datetime.fromtimestamp(ns / 1e9).strftime("%d %b %Y %H:%M") if ns else ""


def _color(key):
    return QColor(theme.current.get(key, key))


def folder_name(root):
    return os.path.basename(root.rstrip("/\\")) or root


class ScanDelegate(QStyledItemDelegate):
    """One scan in the history list: date, folder / size, and what changed since the scan before it."""
    ROW_H = 62

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, p, option, index):
        m = index.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(4, 3, -4, -3)
        if option.state & QStyle.State_Selected:
            p.setPen(QPen(_color("accent"), 1.3))
            p.setBrush(_color("accent_soft"))
            p.drawRoundedRect(r, 9, 9)
        elif option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(_color("surface2"))
            p.drawRoundedRect(r, 9, 9)
        x, w = r.left() + 12, r.width() - 24
        bold = QFont(option.font)
        bold.setPixelSize(13)
        bold.setBold(True)
        p.setFont(bold)
        p.setPen(_color("text"))
        p.drawText(QRectF(x, r.top() + 5, w, 18), Qt.AlignLeft | Qt.AlignVCenter, when(m["time"]))
        small = QFont(option.font)
        small.setPixelSize(12)
        p.setFont(small)
        p.setPen(_color("muted"))
        info = f"{folder_name(m['root'])}  ·  {fmt_int(m['files'])} files  ·  {fmt_size(m['bytes'])}"
        p.drawText(QRectF(x, r.top() + 23, w, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(small).elidedText(info, Qt.ElideRight, int(w)))
        s = m.get("summary")
        if s is None:
            parts = [("First scan", "faint")]
        else:
            parts = [(f"+{s['added']}", "success")] * bool(s["added"]) + [(f"−{s['removed']}", "danger")] * bool(s["removed"]) \
                + [(f"~{s['modified']}", "warn")] * bool(s["modified"]) + [(f"↔{s['moved']}", "#8B5CF6")] * bool(s["moved"])
            parts = parts or [("No changes", "success")]
        small.setBold(True)
        p.setFont(small)
        fm = QFontMetrics(small)
        cx = x
        for text, color in parts:
            p.setPen(_color(color))
            p.drawText(QRectF(cx, r.top() + 39, w, 16), Qt.AlignLeft | Qt.AlignVCenter, text)
            cx += fm.horizontalAdvance(text) + 12
        p.restore()


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

        # ================= left column: start a scan, browse the history =================
        self.path = PathField(PathField.FOLDER, "Folder to scan")
        self.path.changed.connect(lambda _=None: self.changed.emit())
        self.hashed = QCheckBox("Exact mode (read file contents)")
        self.hashed.setToolTip("Computes a checksum for every file, so edits and renames are detected exactly.\n"
                               "Slower the first time; unchanged files are not re-read on later scans.")
        self.skip_hidden = QCheckBox("Skip hidden && system files")
        self.skip_hidden.setChecked(True)
        self.options_btn = button("Scan options…", kind="Link", tooltip="Ignore rules, cloud files, engine")
        self.options_btn.clicked.connect(self.settings_requested.emit)
        self.scan_btn = button("Scan folder", "scan", "Primary")
        self.scan_btn.setProperty("large", True)
        self.scan_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cancel_btn = button("Cancel", "x")
        self.cancel_btn.hide()
        self.scan_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.status = label("", "Hint", wrap=True)
        self.progress.hide()
        self.status.hide()

        new_card = Panel(margins=(16, 14, 16, 14))
        nl = new_card.lay
        nl.setSpacing(8)
        nl.addLayout(hbox(label("New scan", "SectionTitle"), None, self.options_btn))
        nl.addWidget(self.path)
        nl.addWidget(self.hashed)
        nl.addWidget(self.skip_hidden)
        nl.addSpacing(2)
        nl.addLayout(hbox(self.cancel_btn, self.scan_btn, spacing=8))
        nl.addWidget(self.progress)
        nl.addWidget(self.status)

        self.folder_filter = QComboBox()
        self.folder_filter.setToolTip("Show the scans of one folder")
        self.folder_filter.activated.connect(lambda _: self.refresh())
        self.history_count = label("", "Hint")
        self.list = QListWidget()
        self.list.setItemDelegate(ScanDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; outline: 0; }")
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.currentItemChanged.connect(self._user_selected)
        self.history_hint = label("Your scans will appear here.", "Hint")
        self.history_hint.setAlignment(Qt.AlignCenter)

        hist_card = Panel(margins=(10, 14, 10, 10))
        hl = hist_card.lay
        hl.setSpacing(8)
        hl.addLayout(hbox(label("Scan history", "SectionTitle"), None, self.history_count, margins=(6, 0, 6, 0)))
        hl.addWidget(self.folder_filter)
        hl.addWidget(self.list, 1)
        hl.addWidget(self.history_hint)

        side = QWidget()
        side.setMinimumWidth(290)
        side.setMaximumWidth(460)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(12)
        sl.addWidget(new_card)
        sl.addWidget(hist_card, 1)

        # ================= right pane: what changed =================
        self.d_title = label("", "H2")
        self.d_path = ElidedLabel("", Qt.ElideMiddle, "Hint")
        self.d_stats = ElidedLabel("", Qt.ElideRight, "Hint")
        self.again_btn = button("Scan again", "refresh", "Primary",
                                tooltip="Scan this folder now and compare with the previous scan")
        self.reveal_btn = button("Open folder", "folder")
        self.delete_btn = button("Delete", "trash", "Danger", tooltip="Delete this scan (your files are not touched)")
        self.again_btn.clicked.connect(self._scan_again)
        self.reveal_btn.clicked.connect(lambda: self._sel() and reveal(self._sel()["root"]))
        self.delete_btn.clicked.connect(self._delete)

        self.baseline = QComboBox()
        self.baseline.setMinimumWidth(190)
        self.baseline.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.baseline.activated.connect(lambda _: self._load_diff())
        self.history_btn = button("", "history", tooltip="Chart this folder's file count and size over time")
        self.history_btn.setFixedWidth(42)
        self.history_btn.setCheckable(True)
        self.history_btn.toggled.connect(lambda _: (self._draw_trend(), self.changed.emit()))
        self.report_btn = button("Report", "file-text", tooltip="Save a shareable HTML or PDF report")
        self.report_btn.clicked.connect(self._report)
        self.export_btn = button("Export", "download", tooltip="Save as CSV")
        export_menu = QMenu(self.export_btn)
        export_menu.addAction("Changes shown below (CSV)", self._export_changes)
        export_menu.addAction("Every file in this scan (CSV)", self._export_log)
        self.export_btn.setMenu(export_menu)

        self.tiles = {}
        tile_row = hbox(spacing=10)
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
        self.trend.setMinimumHeight(86)
        self.trend_panel = Panel(margins=(16, 10, 16, 8))
        self.trend_panel.lay.addLayout(hbox(label("This folder over time", "SectionTitle"), None, self.trend_mode))
        self.trend_panel.lay.addWidget(self.trend)
        self.trend_panel.hide()

        self.changes = ResultTable(ChangeModel(), path_cols=(1,), stretch_col=1, sort=(0, Qt.AscendingOrder),
                                   placeholder="Filter changes…", min_height=220)
        self.changes.activated.connect(self._reveal_change)
        self.preview = PreviewPanel()
        self.changes_split, self.preview_btn = attach_preview(self.changes, self.preview)
        self.changes.selection_changed.connect(self._change_selected)
        self.note = label("", "Muted", wrap=True)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(self.d_title)
        title_box.addWidget(self.d_path)
        title_box.addWidget(self.d_stats)
        details = QWidget()
        dl = QVBoxLayout(details)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(12)
        dl.addLayout(hbox(title_box, None, self.delete_btn, self.reveal_btn, self.again_btn, spacing=8))
        dl.itemAt(0).layout().setStretch(0, 1)
        self.preview_btn.setText("")      # icon only: the toolbar has to fit a narrow window
        self.preview_btn.setFixedWidth(42)
        toolbar = hbox(label("Compare with", "FieldLabel"), self.baseline, self.history_btn, self.preview_btn,
                       self.report_btn, self.export_btn, spacing=8)
        toolbar.setStretch(1, 1)
        dl.addLayout(toolbar)
        dl.addLayout(tile_row)
        dl.addWidget(self.note)
        dl.addWidget(self.trend_panel)
        dl.addWidget(self.changes_split, 1)

        self.stack = QStackedWidget()
        self.empty = EmptyState(
            "No scans yet",
            "Scan a folder to log every file in it and its sub-folders. Scan it again any time to see what changed.",
            "camera")
        self.stack.addWidget(self.empty)
        self.stack.addWidget(details)
        self.path.changed.connect(self._sync_empty)
        self._sync_empty()

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(10)
        split.setChildrenCollapsible(False)
        split.addWidget(side)
        split.addWidget(self.stack)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 840])

        self.banner = Banner()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("Snapshots", "H1"))
        heading.addWidget(label("Log a folder and everything below it — then scan again to see what changed.", "Muted"))
        lay.addLayout(heading)
        lay.addWidget(self.banner)
        lay.addWidget(split, 1)
        self.refresh()

    def _sync_empty(self, *_):
        """The empty-state button first asks for a folder, then starts the scan."""
        ready = bool(self.path.text()) and os.path.isdir(self.path.text())
        self.scan_btn.setEnabled(ready and not self.is_running())
        self.scan_btn.setToolTip("" if ready else "Choose a folder first")
        if ready:
            self.empty.set_action("Scan this folder", self.start, "scan")
        else:
            self.empty.set_action("Choose a folder…", self.path.browse, "folder")

    # ---- state ----------------------------------------------------------------

    def state(self):
        return {"path": self.path.text(), "hashed": self.hashed.isChecked(), "skip_hidden": self.skip_hidden.isChecked(),
                "preview": self.preview_btn.isChecked(), "history_chart": self.history_btn.isChecked()}

    def restore(self, s):
        self.path.setText(s.get("path", ""))
        self.hashed.setChecked(s.get("hashed", False))
        self.skip_hidden.setChecked(s.get("skip_hidden", True))
        self.preview_btn.setChecked(s.get("preview", False))
        self.history_btn.setChecked(s.get("history_chart", False))

    def stop_background_work(self):
        for w in (self.worker, self.diff_worker):
            if w:
                w.stop()

    def is_running(self):
        return self.worker is not None and self.worker.isRunning()

    # ---- history list -----------------------------------------------------------

    def refresh(self, select_id=None):
        metas = sorted(self.snaps.list(), key=lambda m: -m["time"])
        self.count_changed.emit(len(metas))
        keep = (self._sel() or {}).get("id")

        # the "show scans of…" filter: every folder that has a scan
        roots = []
        for m in metas:
            if not any(F._same_path(m["root"], r) for r in roots):
                roots.append(m["root"])
        current = self.folder_filter.currentData()
        self.folder_filter.blockSignals(True)
        self.folder_filter.clear()
        self.folder_filter.addItem("All folders", None)
        for r in sorted(roots, key=str.lower):
            self.folder_filter.addItem(folder_name(r), r)
            self.folder_filter.setItemData(self.folder_filter.count() - 1, r, Qt.ToolTipRole)
        idx = self.folder_filter.findData(current) if current else 0
        self.folder_filter.setCurrentIndex(max(idx, 0))
        self.folder_filter.setVisible(len(roots) > 1)
        self.folder_filter.blockSignals(False)
        only = self.folder_filter.currentData()
        shown = [m for m in metas if only is None or F._same_path(m["root"], only)]

        self.list.blockSignals(True)
        self.list.clear()
        for m in shown:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, m)
            item.setSizeHint(QSize(0, ScanDelegate.ROW_H))
            item.setToolTip(f"{m['root']}\n{when(m['time'], '%A %d %B %Y, %H:%M')}")
            self.list.addItem(item)
        target = select_id or keep
        row = next((i for i, m in enumerate(shown) if m["id"] == target), 0 if shown else -1)
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self.history_count.setText(f"{len(shown)} scan{'s' if len(shown) != 1 else ''}" if shown else "")
        self.history_hint.setVisible(not shown)
        self.stack.setCurrentIndex(1 if metas else 0)
        self._selected_changed()

    def _user_selected(self, *_):
        self.banner.hide()   # a message about the last scan is stale once you look at another one
        self._selected_changed()

    def _sel(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _list_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Scan again", self._scan_again)
        menu.addAction("Open folder", lambda: reveal(self._sel()["root"]))
        menu.addSeparator()
        menu.addAction("Delete this scan", self._delete)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _selected_changed(self):
        m = self._sel()
        for b in (self.again_btn, self.reveal_btn, self.delete_btn, self.report_btn, self.export_btn):
            b.setEnabled(m is not None)
        if not m:
            return
        self.d_title.setText(folder_name(m["root"]))
        self.reveal_btn.setEnabled(os.path.isdir(m["root"]))
        self.again_btn.setEnabled(os.path.isdir(m["root"]) and not self.is_running())
        self.d_path.setText(m["root"])
        self.d_path.setToolTip(m["root"])
        stats = (f"{when(m['time'])}  ·  {fmt_int(m['files'])} files  ·  {fmt_size(m['bytes'])}"
                 + (f"  ·  {fmt_int(m['errors'])} unreadable skipped" if m.get("errors") else ""))
        self.d_stats.setText(stats)
        self.d_stats.setToolTip(f"Scanned {when(m['time'], '%A %d %B %Y, %H:%M')}\n{fmt_int(m['files'])} files in "
                                f"{fmt_int(m['dirs'])} folders, {fmt_size(m['bytes'])}")
        same = sorted((x for x in self.snaps.list() if F._same_path(x["root"], m["root"]) and x["time"] < m["time"]),
                      key=lambda x: -x["time"])
        self.baseline.blockSignals(True)
        self.baseline.clear()
        for i, x in enumerate(same):
            self.baseline.addItem(("Previous scan · " if i == 0 else "") + when_short(x["time"]), x["id"])
        if not same:
            self.baseline.addItem("— first scan of this folder —")
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
        self.history_btn.setEnabled(len(same) >= 2)
        self.history_btn.setToolTip("Chart this folder's file count and size over time" if len(same) >= 2
                                    else "Scan this folder at least twice to see its history")
        self.trend_panel.setVisible(self.history_btn.isChecked() and len(same) >= 2)
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
        self.again_btn.setEnabled(not busy and self._sel() is not None and os.path.isdir(self._sel()["root"]))
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
        folder_name = os.path.basename(m["root"].rstrip("/\\")) or m["root"]   # (no backslash inside an f-string: Python < 3.12)
        rep = R.Report(f"Folder changes — {folder_name}", m["root"])
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
