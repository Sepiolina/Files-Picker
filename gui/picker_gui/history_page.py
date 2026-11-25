"""
History page: every export, newest first. Select a run to see its details,
load its settings back into the Run page, run it again, or open its output.
"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHeaderView, QMessageBox,
                               QStackedWidget, QTableView, QVBoxLayout, QWidget)

from . import options as O
from .delegates import PathDelegate, StatusDelegate
from .models import FilterProxy, HistoryModel
from .util import fmt_duration, fmt_int, fmt_size, open_file, reveal
from .widgets import EmptyState, Panel, button, hbox, label, search_field


class HistoryPage(QWidget):
    load_requested = Signal(dict)   # job -> put into the Run page
    rerun_requested = Signal(dict)  # job -> put into the Run page and export
    count_changed = Signal(int)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.store = store

        self.search = search_field("Search folders and outputs…", lambda t: self.proxy.setFilterFixedString(t))
        self.search.setFixedWidth(300)
        self.clear_btn = button("Clear all", "trash", "Danger")
        self.clear_btn.clicked.connect(self._clear_all)

        # Table
        self.model = HistoryModel()
        self.proxy = FilterProxy()
        self.proxy.setSourceModel(self.model)
        v = self.view = QTableView()
        v.setModel(self.proxy)
        v.setSortingEnabled(True)
        v.sortByColumn(0, Qt.DescendingOrder)
        v.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.setSelectionMode(QAbstractItemView.SingleSelection)
        v.setShowGrid(False)
        v.setWordWrap(False)
        v.setMouseTracking(True)
        v.setFrameShape(QFrame.NoFrame)
        v.verticalHeader().hide()
        v.verticalHeader().setDefaultSectionSize(40)
        v.horizontalHeader().setFixedHeight(36)
        v.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        v.horizontalHeader().setHighlightSections(False)
        for col in (1, 5):
            v.setItemDelegateForColumn(col, PathDelegate(v))
        v.setItemDelegateForColumn(7, StatusDelegate(v))
        h = v.horizontalHeader()
        for c in range(self.model.columnCount()):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        v.doubleClicked.connect(lambda _: self._load())
        v.selectionModel().selectionChanged.connect(self._update_details)

        # Details of the selected run
        self.details = Panel(margins=(18, 16, 18, 16))
        dl = self.details.lay
        dl.setSpacing(12)
        self.d_title = label("", "SectionTitle")
        self.d_grid = QGridLayout()
        self.d_grid.setHorizontalSpacing(18)
        self.d_grid.setVerticalSpacing(6)
        self.d_grid.setColumnStretch(1, 1)
        self.btn_delete = button("Delete", "trash", "Danger")
        self.btn_report = button("Open report", "file")
        self.btn_show = button("Show output", "folder")
        self.btn_load = button("Load settings", "sliders",
                               tooltip="Copy this run's folder, names and options into the Run page")
        self.btn_rerun = button("Run again", "refresh", "Primary", tooltip="Load the settings and export straight away")
        self.btn_delete.clicked.connect(self._delete)
        self.btn_report.clicked.connect(self._open_report)
        self.btn_show.clicked.connect(lambda: self._selected() and reveal(self._selected()["job"]["output"]["path"]))
        self.btn_load.clicked.connect(self._load)
        self.btn_rerun.clicked.connect(self._rerun)
        dl.addWidget(self.d_title)
        dl.addLayout(self.d_grid)
        dl.addLayout(hbox(self.btn_delete, None, self.btn_report, self.btn_show, self.btn_load, self.btn_rerun))

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(14)
        table_card = Panel(margins=(0, 0, 0, 8))
        table_card.lay.addWidget(self.view)
        cl.addWidget(table_card, 1)
        cl.addWidget(self.details)

        self.stack = QStackedWidget()
        self.stack.addWidget(EmptyState("No exports yet", "Each export is saved here with its settings, "
                                        "so you can repeat it later in one click.", "history"))
        self.stack.addWidget(content)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("History", "H1"))
        heading.addWidget(label("Every export, with its settings — load or repeat any of them.", "Muted"))
        lay.addLayout(hbox(heading, None, self.search, 4, self.clear_btn))
        lay.addWidget(self.stack, 1)
        self.refresh()

    def refresh(self):
        runs = self.store.history()
        self.model.set_rows(runs)
        self.stack.setCurrentIndex(1 if runs else 0)
        self.search.setVisible(bool(runs))
        self.clear_btn.setVisible(bool(runs))
        if runs:
            self.view.selectRow(0)
        self._update_details()
        self.count_changed.emit(len(runs))

    # ---- details ----------------------------------------------------------------------

    def _selected(self):
        rows = self.view.selectionModel().selectedRows()
        return self.model.rows[self.proxy.mapToSource(rows[0]).row()] if rows else None

    def _update_details(self, *_):
        r = self._selected()
        self.details.setVisible(r is not None)
        if not r:
            return
        j, s = O.merged_job(r["job"]), r["summary"]
        status = HistoryModel.STATUS.get(r["status"], (r["status"],))[0]
        self.d_title.setText(f"{status} · {datetime.fromtimestamp(r['time']):%A %d %B %Y, %H:%M}")

        rows = [
            ("Searched", j["source"]),
            ("Names", f"{fmt_int(s.get('names'))} names · {fmt_int(s.get('matched'))} matching files · "
                      f"{fmt_int(s.get('notFound'))} not found"),
            ("Saved to", f"{j['output']['path']}  ({O.label(O.OUTPUT_TYPES, j['output']['type'])})"),
            ("Result", f"{fmt_int(s.get('exported'))} files · {fmt_size(s.get('bytes'))} · "
                       f"{fmt_duration(s.get('elapsedMs'))}"),
            ("Rules", " · ".join(O.matching_tokens(j["match"]))),
        ]
        if s.get("message"):
            rows.append(("Error", s["message"]))
        while self.d_grid.count():
            self.d_grid.takeAt(0).widget().deleteLater()
        for i, (key, value) in enumerate(rows):
            v = label(value, "DetailValue", wrap=True)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.d_grid.addWidget(label(key, "FieldLabel"), i, 0, Qt.AlignTop)
            self.d_grid.addWidget(v, i, 1)

        self.btn_show.setEnabled(Path(j["output"]["path"]).exists())
        self.btn_report.setEnabled(any(Path(p).exists() for p in s.get("reports", [])))

    # ---- actions ------------------------------------------------------------------------

    def _load(self):
        r = self._selected()
        if r:
            self.load_requested.emit(self.store.job_for(r))

    def _rerun(self):
        r = self._selected()
        if r:
            self.rerun_requested.emit(self.store.job_for(r))

    def _open_report(self):
        r = self._selected()
        for p in reversed((r or {}).get("summary", {}).get("reports", [])):
            if Path(p).exists():
                open_file(p)
                return

    def _delete(self):
        r = self._selected()
        if r:
            self.store.remove_run(r["id"])
            self.refresh()

    def _clear_all(self):
        if QMessageBox.question(self, "Clear history", "Remove every run from history?\n"
                                "Your exported files are not touched.") == QMessageBox.Yes:
            self.store.clear_history()
            self.refresh()
