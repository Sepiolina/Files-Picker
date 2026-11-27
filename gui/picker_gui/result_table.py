"""A filterable, sortable results table shared by the Compare, Snapshots and Find tabs."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHeaderView, QTableView, QVBoxLayout, QWidget

from .delegates import PathDelegate, StatusDelegate
from .models import FilterProxy
from .util import fmt_int
from .widgets import Chip, Panel, button, hbox, search_field


class ResultTable(QWidget):
    activated = Signal(object)   # double-click / Enter -> the row object
    selection_changed = Signal()

    def __init__(self, model, path_cols=(), status_col=None, stretch_col=1, sort=(0, Qt.AscendingOrder),
                 with_search=True, placeholder="Filter results…", min_height=300, show_count=True, parent=None):
        super().__init__(parent)
        self.model = model
        self.proxy = FilterProxy()
        self.proxy.setSourceModel(model)
        self._status_of = None

        v = self.view = QTableView()
        v.setModel(self.proxy)
        # Sorting is done here with list.sort (C speed) instead of by QSortFilterProxyModel, which calls back into
        # Python ~2 x N log N times: 50,000 rows froze the window for seconds, 600,000 for minutes.
        v.setSortingEnabled(False)
        v.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.setSelectionMode(QAbstractItemView.SingleSelection)
        v.setShowGrid(False)
        v.setWordWrap(False)
        v.setMouseTracking(True)
        v.setFrameShape(QFrame.NoFrame)
        v.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.verticalHeader().hide()
        v.verticalHeader().setDefaultSectionSize(36)
        v.horizontalHeader().setFixedHeight(36)
        v.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        v.horizontalHeader().setHighlightSections(False)
        v.setMinimumHeight(min_height)
        for c in path_cols:
            v.setItemDelegateForColumn(c, PathDelegate(v))
        if status_col is not None:
            v.setItemDelegateForColumn(status_col, StatusDelegate(v))
        h = v.horizontalHeader()
        for c in range(model.columnCount()):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(True)
        self._sort = tuple(sort)
        h.setSortIndicator(*self._sort)
        h.sortIndicatorChanged.connect(self._on_sort_indicator)
        v.doubleClicked.connect(lambda _: self._emit_activated())
        v.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit())

        self.search = search_field(placeholder, lambda t: self.proxy.setFilterFixedString(t))
        self.search.setVisible(with_search)
        self.count = Chip("")
        self.count.setVisible(show_count)
        self.show_all = button("Show all", kind="Link")
        self.show_all.hide()
        self.show_all.clicked.connect(lambda: self.set_status(None))

        panel = Panel(margins=(0, 0, 0, 8))
        panel.lay.addWidget(v)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addLayout(hbox(self.count, self.show_all, None, self.search))
        lay.addWidget(panel, 1)
        self.search.setFixedWidth(280)
        for sig in (self.proxy.rowsInserted, self.proxy.rowsRemoved, self.proxy.modelReset, self.proxy.layoutChanged):
            sig.connect(self._update_count)

    def set_rows(self, rows, status_of=None):
        self._status_of = status_of
        self.proxy.set_predicate(None)
        self.show_all.hide()
        self.model.set_rows(self._sorted(rows))
        self._update_count()

    def _sorted(self, rows):
        col, order = self._sort
        key = lambda r: self.model.sort_key(r, col)  # noqa: E731
        reverse = order == Qt.DescendingOrder
        try:
            return sorted(rows, key=key, reverse=reverse)
        except TypeError:   # a column that mixes numbers and missing values
            return sorted(rows, key=lambda r: (key(r) is None, key(r) or 0), reverse=reverse)

    def _on_sort_indicator(self, col, order):
        self._sort = (col, order)
        self.model.set_rows(self._sorted(self.model.rows))

    def set_status(self, status, label=""):
        """Show only rows whose status_of(row) == status (None shows everything)."""
        if status is None or not self._status_of:
            self.proxy.set_predicate(None)
            self.show_all.hide()
        else:
            fn = self._status_of
            self.proxy.set_predicate(lambda r: fn(r) == status)
            self.show_all.setText(f"Showing only {label or status} — show all")
            self.show_all.show()
        self._update_count()

    def selected(self):
        rows = self.view.selectionModel().selectedRows()
        return self.model.rows[self.proxy.mapToSource(rows[0]).row()] if rows else None

    def visible_rows(self):
        if self.proxy.rowCount() == len(self.model.rows):   # nothing filtered out: the proxy keeps model order
            return list(self.model.rows)
        return [self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()]
                for i in range(self.proxy.rowCount())]

    def _update_count(self, *_):
        shown, total = self.proxy.rowCount(), len(self.model.rows)
        self.count.set(f"{fmt_int(shown)} of {fmt_int(total)}" if shown != total else f"{fmt_int(total)} items")

    def _emit_activated(self):
        r = self.selected()
        if r is not None:
            self.activated.emit(r)
