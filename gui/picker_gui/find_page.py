"""
Find tab: search any folder or whole drive by file name with a "smart" search
that understands word variants, synonyms, file types and typos
("recipt" finds receipt, "photos" finds IMG_2041.jpg, "invoice" finds bill_march.pdf).

Caching, two layers:
  * the scanned folder structure is saved to disk - reopening the app is instant
    and "Refresh" only re-reads folders whose contents changed;
  * every query and its results are remembered (in memory and on disk), so
    repeating a search is instant. They are discarded when the index changes.
"""

import os
import time
from pathlib import Path

from PySide6.QtCore import QStorageInfo, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QCompleter, QMenu, QProgressBar, QStackedWidget, QVBoxLayout, QWidget

from . import fscore as F
from .jobs import Worker, describe
from .models import TableModel
from .preview import PreviewPanel, attach_preview
from .result_table import ResultTable
from .util import fmt_int, fmt_size, open_file, reveal, scan_note
from .widgets import Banner, Chip, EmptyState, Panel, PathField, Segmented, button, hbox, label, search_field

TYPES = [("", "Any type")] + [(k, k.capitalize()) for k in F.EXT_GROUPS]
MAX_RESULTS = 300


def ago(t):
    s = max(0, time.time() - t)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} days ago"


class FindModel(TableModel):
    COLUMNS = ["File", "Match", "Size", "Modified"]

    def __init__(self):
        super().__init__()
        self.top = 1.0

    def set_rows(self, rows):
        self.top = max((r.score for r in rows), default=1.0) or 1.0
        super().set_rows(rows)

    def cell(self, r, c):
        if c == 0:
            return r.rel
        if c == 1:
            return f"{min(100, round(r.score / self.top * 100))}%"
        if c == 2:
            return fmt_size(r.size)
        from datetime import datetime
        return datetime.fromtimestamp(r.mtime / 1e9).strftime("%d %b %Y %H:%M")

    def sort_key(self, r, c):
        return {1: r.score, 2: r.size, 3: r.mtime}.get(c, r.rel.lower())

    def color(self, r, c):
        return "muted" if c else None

    def is_numeric(self, c):
        return c in (1, 2)


class FindPage(QWidget):
    changed = Signal()
    settings_requested = Signal()

    def __init__(self, store_root, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.cache = F.IndexCache(store_root)
        self.index = None
        self.worker = None
        self.qcache = {}
        self._pending = False
        self._first_show = True

        # ---- where to search --------------------------------------------------------
        self.path = PathField(PathField.FOLDER, "Folder or drive to search in")
        self.path.changed.connect(self._path_changed)
        self.drives_btn = button("Drives", "drive", tooltip="Pick a whole drive to search")
        self.drives_menu = QMenu(self.drives_btn)
        self.drives_menu.aboutToShow.connect(self._fill_drives)
        self.drives_btn.setMenu(self.drives_menu)
        self.drives_btn.setMinimumHeight(36)
        self.index_btn = button("Build index", "refresh", tooltip="Scan the folder so searching is instant")
        self.full_btn = button("Full rescan", tooltip="Ignore the cache and read every folder again")
        self.index_btn.clicked.connect(lambda: self.build(full=False))
        self.full_btn.clicked.connect(lambda: self.build(full=True))
        self.cancel_btn = button("Cancel", "x")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.index_chip = Chip("Not indexed yet")
        self.options_btn = button("Ignore rules && scan options…", kind="Link")
        self.options_btn.clicked.connect(self.settings_requested.emit)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.status = label("", "Hint")
        self.status.hide()

        where = Panel(margins=(18, 16, 18, 16))
        where.lay.setSpacing(10)
        where.lay.addWidget(label("Search in", "FieldLabel"))
        where.lay.addLayout(hbox(self.path, self.drives_btn, spacing=8))
        where.lay.itemAt(1).layout().setStretch(0, 1)
        where.lay.addLayout(hbox(self.index_chip, 4, self.options_btn, None, self.cancel_btn, self.full_btn, self.index_btn,
                                 spacing=8))

        # ---- the search ----------------------------------------------------------------
        self.box = search_field("Describe the file — e.g. “march invoice pdf”, “holiday photos”, “resume”", lambda _: None)
        self.box.setMinimumHeight(40)
        self.box.setClearButtonEnabled(True)
        self.completer = QCompleter([], self.box)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.box.setCompleter(self.completer)
        self.timer = QTimer(self, singleShot=True, interval=250)
        self.timer.timeout.connect(self.search)
        self.box.textChanged.connect(lambda _: self.timer.start())
        self.box.returnPressed.connect(self.search)
        self.mode = Segmented([("smart", "Smart"), ("exact", "Exact")])
        self.mode.setValue("smart")
        self.mode.setToolTip("Smart: synonyms, word forms, file types and typos.\n"
                             "Exact: text must appear in the path (supports * and ? wildcards).")
        self.mode.changed.connect(lambda _: self.search())
        self.type = QComboBox()
        for value, text in TYPES:
            self.type.addItem(text, value)
        self.type.activated.connect(lambda _: self.search())
        self.result_chip = Chip("")
        self.result_chip.hide()   # only shown once there is something to report
        self.banner = Banner()

        # ---- results ---------------------------------------------------------------------
        self.table = ResultTable(FindModel(), path_cols=(0,), stretch_col=0, sort=(1, Qt.DescendingOrder),
                                 with_search=False, min_height=240, show_count=False)
        self.table.activated.connect(lambda r: self._open(r))
        self.table.selection_changed.connect(self._sel_changed)
        self.open_btn = button("Open", "external")
        self.show_btn = button("Show in folder", "folder")
        self.copy_btn = button("Copy path", "copy")
        self.open_btn.clicked.connect(lambda: self._open(self.table.selected()))
        self.show_btn.clicked.connect(lambda: self._reveal(self.table.selected()))
        self.copy_btn.clicked.connect(self._copy)
        for b in (self.open_btn, self.show_btn, self.copy_btn):
            b.setEnabled(False)

        results = QWidget()
        rl = QVBoxLayout(results)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        self.preview = PreviewPanel()
        split, self.preview_btn = attach_preview(self.table, self.preview)
        rl.addWidget(split, 1)
        rl.addLayout(hbox(label("Double-click a result to open it", "Hint"), None, self.preview_btn, self.copy_btn,
                          self.show_btn, self.open_btn))
        self.stack = QStackedWidget()
        self.empty = EmptyState(
            "Find a file anywhere",
            "Choose a folder or a whole drive. The first scan builds a cached index — after that, searches are instant "
            "and refreshing only re-reads what changed.", "search")
        self.stack.addWidget(self.empty)
        self.path.changed.connect(self._sync_empty)
        self._sync_empty()
        self.stack.addWidget(results)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("Find", "H1"))
        heading.addWidget(label("Search a folder or drive by describing the file. Typos and synonyms are fine.", "Muted"))
        lay.addLayout(heading)
        lay.addWidget(where)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addWidget(self.banner)
        lay.addLayout(hbox(self.box, self.mode, self.type, spacing=10))
        lay.itemAt(lay.count() - 1).layout().setStretch(0, 1)
        lay.addLayout(hbox(self.result_chip, None))
        lay.addWidget(self.stack, 1)

        self._save_q = QTimer(self, singleShot=True, interval=1500)
        self._save_q.timeout.connect(self._persist_queries)
        self._sync_buttons()

    def _set_result(self, text, tone=None):
        self.result_chip.setVisible(bool(text))
        if text:
            self.result_chip.set(text, tone)

    def _sync_empty(self, *_):
        """The empty-state button first asks for a folder or drive, then builds the index."""
        if self.path.text() and os.path.isdir(self.path.text()):
            self.empty.set_action("Index this folder", lambda: self.build(False), "refresh")
        else:
            self.empty.set_action("Choose a folder or drive…", self.path.browse, "folder")

    # ---- state -------------------------------------------------------------------------

    def state(self):
        return {"path": self.path.text(), "mode": self.mode.value(), "type": self.type.currentData(),
                "preview": self.preview_btn.isChecked()}

    def restore(self, s):
        self.path.setText(s.get("path", ""))
        self.mode.setValue(s.get("mode", "smart"))
        i = self.type.findData(s.get("type", ""))
        self.type.setCurrentIndex(max(0, i))
        self.preview_btn.setChecked(s.get("preview", False))

    def showEvent(self, e):
        super().showEvent(e)
        if self._first_show:  # reopen the cached index quietly the first time the tab is shown
            self._first_show = False
            folder = self.path.text()
            if folder and os.path.isdir(folder) and self.cache.load(folder):
                QTimer.singleShot(100, lambda: self.build(full=False))

    def is_running(self):
        return self.worker is not None and self.worker.isRunning()

    def stop_background_work(self):
        self._persist_queries()
        if self.worker:
            self.worker.stop()

    # ---- drives ---------------------------------------------------------------------------

    def _fill_drives(self):
        self.drives_menu.clear()
        skip = ("/proc", "/sys", "/dev", "/run", "/snap", "/boot")
        for v in QStorageInfo.mountedVolumes():
            root = v.rootPath()
            if not (v.isValid() and v.isReady()) or v.bytesTotal() <= 0 or root.startswith(skip):
                continue
            name = v.displayName() if v.displayName() != root else ""
            self.drives_menu.addAction(f"{root}   {name}  ({fmt_size(v.bytesFree())} free of {fmt_size(v.bytesTotal())})",
                                       lambda r=root: self.path.setText(r))
        if self.drives_menu.isEmpty():
            self.drives_menu.addAction("No drives found").setEnabled(False)

    # ---- index -----------------------------------------------------------------------------

    def _path_changed(self, _=None):
        self.changed.emit()
        folder = self.path.text()
        if self.index and not F._same_path(self.index.root, folder or "."):
            self.index = None
            self.qcache = {}
            self.table.set_rows([])
            self._set_result("")
            self.index_chip.set("Not indexed yet")
        self._sync_buttons()

    def _sync_buttons(self):
        ok = bool(self.path.text()) and os.path.isdir(self.path.text())
        busy = self.is_running()
        self.index_btn.setEnabled(ok and not busy)
        self.full_btn.setEnabled(ok and not busy)
        self.index_btn.setText("Refresh index" if self.index else "Build index")

    def build(self, full=False):
        folder = self.path.text()
        if self.is_running():
            return
        if not folder or not os.path.isdir(folder):
            self.banner.show_message("danger", "Choose a folder or drive to search in first")
            return
        self.banner.hide()
        if Path(folder).parent == Path(folder) and not self.cache.load(folder):
            self.banner.show_message("info", "Indexing a whole drive",
                                     "The first scan can take a few minutes. After that it is cached and refreshes "
                                     "only re-read folders that changed.")
        self.worker = Worker(lambda cancel, prog: F.build_index(folder, self.cache, cancel, prog, True, full), self)
        self.worker.progress.connect(lambda p: self.status.setText(describe(p)))
        self.worker.done.connect(self._built)
        self.worker.failed.connect(lambda m: (self._busy(False), self.banner.show_message("danger", "Indexing failed", m)))
        self.worker.cancelled.connect(lambda: (self._busy(False), self.index_chip.set("Indexing cancelled", "warn")))
        self._busy(True)
        self.worker.start()

    def _busy(self, busy):
        self.progress.setVisible(busy)
        self.status.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        if busy:
            self.status.setText("Starting…")
            self.index_chip.set("Indexing…")
        self._sync_buttons()

    def _built(self, res):
        index, info = res
        self._busy(False)
        self.index = index
        self.qcache = self.cache.load_queries(index.root, index.stamp)
        self._update_completer()
        reuse = (f" · {fmt_int(info['reused'])} of {fmt_int(info['dirs'])} folders reused from cache"
                 if info["from_cache"] else "")
        self.index_chip.set(f"{fmt_int(info['files'])} files indexed · {ago(index.stamp)}{reuse} · "
                            f"{info['elapsed']:.1f}s"
                            + (" · " + scan_note(info['cloud'], info['ignored']) if info['cloud'] or info['ignored'] else ""),
                            "success")
        self.stack.setCurrentIndex(1)
        self._sync_buttons()
        if self.box.text().strip():
            self.search()

    # ---- searching ----------------------------------------------------------------------------

    def search(self):
        self.timer.stop()
        query = self.box.text().strip()
        if not query:
            self.table.set_rows([])
            self._set_result("")
            return
        if self.index is None:
            if not self.is_running():
                self.build(False)
            return
        smart = self.mode.value() == "smart"
        group = self.type.currentData()
        key = f"{'s' if smart else 'e'}|{group}|{query.casefold()}"
        t0 = time.perf_counter()
        hit = self.qcache.get(key)
        if hit:
            rows = [F.SearchHit(*r) for r in hit["r"]]
            cached = True
        else:
            rows = self.index.search(query, smart, F.EXT_GROUPS.get(group), MAX_RESULTS)
            self.qcache[key] = {"q": query, "r": [list(r) for r in rows]}
            self._save_q.start()
            self._update_completer()
            cached = False
        ms = (time.perf_counter() - t0) * 1000
        self.table.set_rows(rows)
        self._set_result(f"{fmt_int(len(rows))}{'+' if len(rows) >= MAX_RESULTS else ''} results · {ms:.0f} ms"
                             + (" · from cache" if cached else ""), "success" if cached else None)
        if rows:
            self.table.view.selectRow(0)
        elif smart:
            self.banner.show_message("info", "No matches", "Try fewer or different words, or switch the type to “Any type”.")
        self.stack.setCurrentIndex(1)

    def _update_completer(self):
        self.completer.setModel(None)
        from PySide6.QtCore import QStringListModel
        queries = list(dict.fromkeys(v["q"] for v in reversed(list(self.qcache.values()))))[:30]
        self.completer.setModel(QStringListModel(queries, self.completer))

    def _persist_queries(self):
        if self.index is not None:
            self.cache.save_queries(self.index.root, self.index.stamp, self.qcache)

    # ---- result actions --------------------------------------------------------------------------

    def _full(self, row):
        return os.path.join(self.index.root, row.rel.replace("/", os.sep)) if (row is not None and self.index) else ""

    def _sel_changed(self):
        row = self.table.selected()
        for b in (self.open_btn, self.show_btn, self.copy_btn):
            b.setEnabled(row is not None)
        self.preview.show_path(self._full(row)) if row is not None else self.preview.clear()

    def _open(self, row):
        p = self._full(row)
        if p and os.path.exists(p):
            open_file(p)
        elif p:
            self.banner.show_message("warn", "That file no longer exists", "Refresh the index to update the results.",
                                     [("Refresh", lambda: self.build(False))])

    def _reveal(self, row):
        p = self._full(row)
        if p:
            reveal(p)

    def _copy(self):
        p = self._full(self.table.selected())
        if p:
            QGuiApplication.clipboard().setText(os.path.normpath(p))
