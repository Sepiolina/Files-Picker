"""Table models for the results and history views (fast with 100k+ rows)."""

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from . import theme
from .delegates import TAGS_ROLE
from .util import fmt_duration, fmt_int, fmt_size

SORT_ROLE = Qt.UserRole + 1


class TableModel(QAbstractTableModel):
    """Generic read-only model. Subclasses define COLUMNS and cell()."""

    COLUMNS = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.DisplayRole:
            return self.cell(row, index.column())
        if role == SORT_ROLE:
            return self.sort_key(row, index.column())
        if role == Qt.ForegroundRole:
            color = self.color(row, index.column())
            return QColor(theme.current.get(color, color)) if color else None
        if role == Qt.TextAlignmentRole and self.is_numeric(index.column()):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            return self.cell(row, index.column())
        if role == TAGS_ROLE:
            return self.tags(row, index.column())
        return None

    def tags(self, row, col):
        return None

    def cell(self, row, col):
        raise NotImplementedError

    def sort_key(self, row, col):
        return self.cell(row, col)

    def color(self, row, col):
        return None

    def is_numeric(self, col):
        return False


class FilesModel(TableModel):
    COLUMNS = ["File", "Size", "Matched", "Assigned to"]

    def tags(self, r, c):
        return r["matched"] if c == 2 else r["assigned"] if c == 3 else None

    def cell(self, r, c):
        return [r["path"], fmt_size(r["size"]), ", ".join(r["matched"]), ", ".join(r["assigned"])][c]

    def sort_key(self, r, c):
        return r["size"] if c == 1 else self.cell(r, c).lower()

    def color(self, r, c):
        return "muted" if c in (1, 3) else None

    def is_numeric(self, c):
        return c == 1


class NamesModel(TableModel):
    COLUMNS = ["Name", "Files", "Also written as"]

    def cell(self, r, c):
        if c == 0:
            return r["name"]
        if c == 1:
            return fmt_int(r["count"]) if r["count"] else "Not found"
        return ", ".join(s for s in r.get("spellings", []) if s != r["name"])

    def sort_key(self, r, c):
        return r["count"] if c == 1 else self.cell(r, c).lower()

    def color(self, r, c):
        if c == 1 and not r["count"]:
            return "danger"
        return "muted" if c == 2 else None

    def is_numeric(self, c):
        return c == 1


class HistoryModel(TableModel):
    COLUMNS = ["When", "Folder", "Names", "Files", "Size", "Output", "Time", "Status"]
    STATUS = {"done": ("Done", "success"), "error": ("Failed", "danger"), "cancelled": ("Cancelled", "warn")}

    def cell(self, r, c):
        s = r.get("summary", {})
        j = r.get("job", {})
        if c == 0:
            return datetime.fromtimestamp(r["time"]).strftime("%d %b %Y, %H:%M")
        if c == 1:
            return j.get("source", "")
        if c == 2:
            return fmt_int(s.get("names"))
        if c == 3:
            return fmt_int(s.get("exported"))
        if c == 4:
            return fmt_size(s.get("bytes"))
        if c == 5:
            return j.get("output", {}).get("path", "")
        if c == 6:
            return fmt_duration(s.get("elapsedMs"))
        return self.STATUS.get(r["status"], (r["status"], None))[0]

    def sort_key(self, r, c):
        s = r.get("summary", {})
        keys = {0: r["time"], 2: s.get("names", 0), 3: s.get("exported", 0), 4: s.get("bytes", 0),
                6: s.get("elapsedMs", 0)}
        return keys.get(c, self.cell(r, c).lower())

    def color(self, r, c):
        if c == 7:
            return self.STATUS.get(r["status"], ("", None))[1]
        return "muted" if c in (0, 6) else None

    def is_numeric(self, c):
        return c in (2, 3, 4, 6)


class FilterProxy(QSortFilterProxyModel):
    """Case-insensitive text filter over all columns + optional row predicate."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.predicate = None

    def set_predicate(self, fn):
        self.predicate = fn
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if self.predicate and not self.predicate(self.sourceModel().rows[row]):
            return False
        return super().filterAcceptsRow(row, parent)
