"""
Right side of the Run page - the preview workspace.

    Idle      onboarding: intro + three steps that tick themselves off + shortcuts
    Running   "Exporting…" · live counts · Cancel, with a thin progress bar
    Finished  banner (what happened + next actions)
              stat tiles (the numbers)
              one results card: [Files | Names] tabs · search · table

The banner never repeats the numbers in the tiles - it says what happened and
what you can do next; the tiles carry the figures.
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHeaderView, QProgressBar, QStackedWidget,
                               QTabBar, QTableView, QVBoxLayout, QWidget)

from .delegates import PathDelegate, TagsDelegate
from .models import FilesModel, FilterProxy, NamesModel
from .util import fmt_duration, fmt_int, fmt_size
from .widgets import Banner, Checklist, Panel, StatTile, button, hbox, label, search_field


def make_table(model):
    proxy = FilterProxy()
    proxy.setSourceModel(model)
    view = QTableView()
    view.setModel(proxy)
    # No sort column at first: rows keep the engine's order (files by path,
    # names in list order). Clicking a header sorts.
    view.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
    view.setSortingEnabled(True)
    view.setTextElideMode(Qt.ElideMiddle)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setShowGrid(False)
    view.setWordWrap(False)
    view.setMouseTracking(True)
    view.setFrameShape(QFrame.NoFrame)
    view.verticalHeader().hide()
    view.verticalHeader().setDefaultSectionSize(36)
    view.horizontalHeader().setHighlightSections(False)
    view.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    view.horizontalHeader().setFixedHeight(36)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    return view, proxy


class ResultsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Workspace")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(14)

        # Header + progress
        self.title = label("Results", "H2")
        self.status = label("", "Muted")
        self.cancel_btn = button("Cancel", "x", tooltip="Stop now (Esc). Nothing is left half-written.")
        self.cancel_btn.hide()
        self.header = hbox(self.title, 12, self.status, None, self.cancel_btn)
        lay.addLayout(self.header)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        lay.addWidget(self.progress)

        self.banner = Banner()
        lay.addWidget(self.banner)

        # Onboarding (idle) vs. results
        self.stack = QStackedWidget()
        self.checklist = Checklist(
            "Find files by name",
            "Search a folder for every file whose name contains one of your names, "
            "then export them with their folders intact.",
            [("Choose the folder to search", "Browse, paste a path, or drop a folder on the field"),
             ("Add the names to find", "Paste them, or import a .txt / .csv list"),
             ("Preview or export", "Preview shows what matches before anything is copied")])
        self.stack.addWidget(self.checklist)
        self.stack.addWidget(self._build_results())
        lay.addWidget(self.stack, 1)
        self.stack.currentChanged.connect(self._sync_header)
        self._sync_header()
        self._export_started = 0.0

    def _build_results(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # Most important first: what matched, then what's missing.
        self.t_matched = StatTile("Matching files", "file", clickable=True)
        self.t_missing = StatTile("Names not found", "alert", clickable=True)
        self.t_size = StatTile("Total size", "archive")
        self.t_checked = StatTile("Files checked", "search")
        self.t_matched.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.t_missing.clicked.connect(self.show_missing)
        self.t_missing.setToolTip("Show the names that found no file")
        lay.addLayout(hbox(self.t_matched, self.t_missing, self.t_size, self.t_checked, spacing=12))

        # Files table: name first (what people look for), folder muted, matched names as tags.
        self.files_model = FilesModel()
        self.files_view, self.files_proxy = make_table(self.files_model)
        self.files_view.setItemDelegateForColumn(0, PathDelegate(self.files_view))
        self.files_view.setItemDelegateForColumn(2, TagsDelegate(self.files_view))
        self.files_view.setItemDelegateForColumn(3, TagsDelegate(self.files_view))
        h = self.files_view.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)

        # Names table
        self.names_model = NamesModel()
        self.names_view, self.names_proxy = make_table(self.names_model)
        h = self.names_view.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)

        # One card: toolbar (tabs · note · filters) + the active table
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.addTab("Files")
        self.tabs.addTab("Names")
        self.files_note = label("", "Hint")
        self.only_missing = QCheckBox("Only not found")
        self.only_missing.toggled.connect(
            lambda on: self.names_proxy.set_predicate((lambda r: r["count"] == 0) if on else None))
        self.filter = search_field("Filter…", self._apply_filter)
        self.filter.setFixedWidth(240)
        toolbar = hbox(self.tabs, 12, self.files_note, None, self.only_missing, 8, self.filter,
                       margins=(12, 10, 12, 10))

        self.tables = QStackedWidget()
        self.tables.addWidget(self.files_view)
        self.tables.addWidget(self.names_view)
        self.tabs.currentChanged.connect(self._on_tab)

        card = Panel(margins=(0, 0, 0, 8))  # bottom gap keeps the table inside the rounded corners
        card.lay.addLayout(toolbar)
        card.lay.addWidget(self.tables, 1)
        lay.addWidget(card, 1)
        self._on_tab(0)
        return w

    def _on_tab(self, index):
        self.tables.setCurrentIndex(index)
        self.only_missing.setVisible(index == 1)
        self.files_note.setVisible(index == 0)
        self.filter.setPlaceholderText("Filter files…" if index == 0 else "Filter names…")
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text):
        (self.files_proxy if self.tabs.currentIndex() == 0 else self.names_proxy).setFilterFixedString(text)

    def _sync_header(self, *_):
        # The onboarding has its own title; "Results" only makes sense once there are some.
        visible = self.stack.currentIndex() == 1 or self.progress.isVisible()
        for w in (self.title, self.status):
            w.setVisible(visible)

    # ---- idle state ----------------------------------------------------------------

    def set_checklist(self, has_folder, has_names):
        self.checklist.set_done([has_folder, has_names, False])

    # ---- run lifecycle ----------------------------------------------------------------

    def begin(self, command):
        self.banner.hide()
        self.title.setText("Previewing…" if command == "scan" else "Exporting…")
        self.status.setText("Starting")
        self.progress.setRange(0, 0)  # indeterminate while scanning
        self.progress.show()
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        self._sync_header()

    def on_progress(self, ev):
        if ev["phase"] == "scan":
            self.status.setText(f"{fmt_int(ev.get('checked'))} files checked · "
                                f"{fmt_int(ev.get('matched'))} matching")
            return
        if not self._export_started:
            self._export_started = time.monotonic()
        done, total = ev.get("bytes", 0), max(ev.get("totalBytes", 0), 1)
        self.progress.setRange(0, 1000)
        self.progress.setValue(int(done * 1000 / total))
        text = (f"{fmt_int(ev.get('files'))} of {fmt_int(ev.get('totalFiles'))} files · "
                f"{fmt_size(done)} of {fmt_size(total)}")
        elapsed = time.monotonic() - self._export_started
        if done and elapsed > 2:
            remaining = (total - done) / (done / elapsed)
            text += f" · about {fmt_duration(remaining * 1000)} left"
        self.status.setText(text)

    def on_preview(self, ev):
        self.stack.setCurrentIndex(1)
        self.t_matched.set(fmt_int(ev["matched"]), None if ev["matched"] else "danger")
        self.t_missing.set(fmt_int(ev["notFound"]), "danger" if ev["notFound"] else None)
        self.t_size.set(fmt_size(ev["totalBytes"]))
        self.t_checked.set(fmt_int(ev["checked"]))
        self.files_model.set_rows(ev["files"])
        self.names_model.set_rows(ev["names"])
        # "Assigned to" only adds information when a file matched several names.
        multi = any(len(f["matched"]) > 1 for f in ev["files"])
        self.files_view.setColumnHidden(3, not multi)
        self.files_note.setText(f"First {fmt_int(len(ev['files']))} shown · all are exported"
                                if ev["truncated"] else "")
        self.tabs.setTabText(0, f"Files  {fmt_int(ev['matched'])}")
        self.tabs.setTabText(1, f"Names  {fmt_int(len(ev['names']))}")

    def end(self):
        self.progress.hide()
        self.cancel_btn.hide()
        self.title.setText("Results")
        self.status.setText("")
        self._export_started = 0.0
        self._sync_header()

    def show_missing(self):
        self.tabs.setCurrentIndex(1)
        self.only_missing.setChecked(True)
