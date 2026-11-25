"""
Custom cell painters for tables.

  PathDelegate    "file.txt   a/b"   - name first, folder muted
  TagsDelegate    [ABC] [ABCDE]      - matched names as small tags
  StatusDelegate  ● Done             - coloured dot + label
"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate

from . import theme

TAGS_ROLE = Qt.UserRole + 2  # model returns a list[str] for tag columns
PAD = 10


def _draw_background(painter, option, index, delegate):
    """Let the style paint hover/selection, but no text."""
    delegate.initStyleOption(option, index)
    option.text = ""
    style = option.widget.style() if option.widget else QApplication.style()
    style.drawControl(QStyle.CE_ItemViewItem, option, painter, option.widget)


class PathDelegate(QStyledItemDelegate):
    def paint(self, p, option, index):
        text = index.data(Qt.DisplayRole) or ""
        _draw_background(p, option, index, self)
        folder, _, name = text.replace("\\", "/").rstrip("/").rpartition("/")
        r = option.rect.adjusted(PAD, 0, -PAD, 0)
        fm = option.fontMetrics
        p.save()
        p.setPen(QColor(theme.current["text"]))
        shown = fm.elidedText(name, Qt.ElideMiddle, r.width())
        p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, shown)
        used = fm.horizontalAdvance(shown) + 12
        if folder and r.width() - used > 40:
            p.setPen(QColor(theme.current["muted"]))
            rest = r.adjusted(used, 0, 0, 0)
            p.drawText(rest, Qt.AlignVCenter | Qt.AlignLeft, fm.elidedText(folder + "/", Qt.ElideLeft, rest.width()))
        p.restore()


class TagsDelegate(QStyledItemDelegate):
    def paint(self, p, option, index):
        tags = index.data(TAGS_ROLE) or []
        _draw_background(p, option, index, self)
        fm = option.fontMetrics
        x, right = option.rect.left() + PAD, option.rect.right() - PAD
        h = fm.height() + 4
        y = option.rect.top() + (option.rect.height() - h) // 2
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        for i, tag in enumerate(tags):
            w = fm.horizontalAdvance(tag) + 14
            more = f"+{len(tags) - i}"
            if x + w > right and i:  # no room: show "+N"
                p.setPen(QColor(theme.current["muted"]))
                p.drawText(QRect(x, y, fm.horizontalAdvance(more) + 4, h), Qt.AlignCenter, more)
                break
            rect = QRect(x, y, min(w, right - x), h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.current["surface2"]))
            p.drawRoundedRect(rect, 5, 5)
            p.setPen(QColor(theme.current["text"]))
            p.drawText(rect, Qt.AlignCenter, fm.elidedText(tag, Qt.ElideRight, rect.width() - 10))
            x += w + 4
        p.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        tags = index.data(TAGS_ROLE) or []
        width = sum(option.fontMetrics.horizontalAdvance(t) + 18 for t in tags[:3]) + 2 * PAD
        s.setWidth(min(max(width, 90), 320))
        return s


class StatusDelegate(QStyledItemDelegate):
    def paint(self, p, option, index):
        text = index.data(Qt.DisplayRole) or ""
        color = index.data(Qt.ForegroundRole) or QColor(theme.current["muted"])
        _draw_background(p, option, index, self)
        r = option.rect.adjusted(PAD, 0, -PAD, 0)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(r.left(), r.center().y() - 3, 7, 7)
        p.setPen(QColor(theme.current["text"]))
        p.drawText(r.adjusted(14, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setWidth(s.width() + 18)
        return s
