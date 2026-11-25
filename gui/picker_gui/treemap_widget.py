"""An interactive treemap: tile area = size. Click a folder tile to open it."""

from collections import namedtuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import fscore
from .treemap import squarify

Item = namedtuple("Item", "label size kind key color tip")   # kind: dir | file | rest

# Folders use muted slate tones so they never look like a file type (those are the vivid colours below).
DIR_COLORS = ["#475569", "#4B5E7A", "#3F6F6A", "#5B5A8A", "#7A5A4B", "#556B8D", "#4F6B5A", "#6B5B7B"]
TYPE_COLORS = {"images": "#DB2777", "video": "#7C3AED", "audio": "#0E9FB5", "documents": "#2563EB",
               "archives": "#D97706", "code": "#059669", "other": "#6B7080"}
REST_COLOR = "#8A8FA3"
_EXT_GROUP = {}
for _g, _exts in fscore.EXT_GROUPS.items():
    if _g in TYPE_COLORS:
        for _e in _exts:
            _EXT_GROUP.setdefault(_e, _g)


def file_type_group(name):
    return _EXT_GROUP.get(fscore.ext_of(name), "other")


class TreemapWidget(QWidget):
    folder_clicked = Signal(object)    # Item (kind == "dir")
    file_clicked = Signal(object)      # Item (kind == "file")
    file_activated = Signal(object)    # double-click on a file

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items, self.rects = [], []
        self.hover = self.selected = -1
        self.setMouseTracking(True)
        self.setMinimumSize(320, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_items(self, items):
        self.items = [i for i in items if i.size > 0]
        self.hover = self.selected = -1
        self._layout()
        self.update()

    def select_key(self, key):
        self.selected = next((i for i, it in enumerate(self.items) if it.key == key), -1)
        self.update()

    def _layout(self):
        self.rects = squarify([i.size for i in self.items], 0, 0, max(self.width(), 1), max(self.height(), 1))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def _at(self, pos):
        for i, (x, y, w, h) in enumerate(self.rects):
            if x <= pos.x() < x + w and y <= pos.y() < y + h:
                return i
        return -1

    def mouseMoveEvent(self, e):
        i = self._at(e.position())
        if i != self.hover:
            self.hover = i
            self.setToolTip(self.items[i].tip if i >= 0 else "")
            self.setCursor(Qt.PointingHandCursor if i >= 0 and self.items[i].kind != "rest" else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        i = self._at(e.position())
        if i < 0 or e.button() != Qt.LeftButton:
            return
        item = self.items[i]
        if item.kind == "dir":
            self.folder_clicked.emit(item)
        elif item.kind == "file":
            self.selected = i
            self.update()
            self.file_clicked.emit(item)

    def mouseDoubleClickEvent(self, e):
        i = self._at(e.position())
        if i >= 0 and self.items[i].kind == "file":
            self.file_activated.emit(self.items[i])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPixelSize(11)
        bold = QFont(self.font())
        bold.setPixelSize(12)
        bold.setBold(True)
        total = sum(i.size for i in self.items) or 1
        for n, (item, (x, y, w, h)) in enumerate(zip(self.items, self.rects)):
            r = QRectF(x + 1.5, y + 1.5, max(w - 3, 0), max(h - 3, 0))
            if r.width() < 1 or r.height() < 1:
                continue
            color = QColor(item.color)
            if n == self.hover:
                color = color.lighter(115)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(r, 5, 5)
            if n == self.selected:
                p.setPen(QPen(QColor("#FFFFFF"), 2.5))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 4, 4)
            if r.width() > 54 and r.height() > 24:
                p.setPen(QColor("#FFFFFF"))
                p.setFont(bold)
                fm = p.fontMetrics()
                label = item.label + ("/" if item.kind == "dir" else "")
                p.drawText(QRectF(r.left() + 7, r.top() + 4, r.width() - 12, 16), Qt.AlignLeft | Qt.AlignVCenter,
                           fm.elidedText(label, Qt.ElideRight, int(r.width() - 12)))
                if r.height() > 42:
                    p.setFont(small)
                    p.setPen(QColor(255, 255, 255, 215))
                    p.drawText(QRectF(r.left() + 7, r.top() + 21, r.width() - 12, 15), Qt.AlignLeft | Qt.AlignVCenter,
                               f"{self._fmt(item.size)}  ·  {item.size / total * 100:.0f}%")
        p.end()

    @staticmethod
    def _fmt(n):
        from .util import fmt_size
        return fmt_size(n)
