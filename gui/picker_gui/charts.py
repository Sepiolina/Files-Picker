"""
Small custom-painted charts that follow the light/dark theme:

    DonutChart    share of each category, total in the middle
    StackedBars   one horizontal bar per row, split by category
    TrendChart    a line over time (e.g. file count per scan)
    Legend        coloured dots + labels
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme


def _c(color, alpha=None):
    c = QColor(theme.current.get(color, color))
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


class DonutChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments, self.top, self.bottom = [], "", ""
        self.setMinimumSize(170, 170)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    def set_data(self, segments, top="", bottom=""):
        """segments: [(label, value, color)]"""
        self.segments, self.top, self.bottom = [s for s in segments if s[1] > 0], top, bottom
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 12
        thick = max(14, side * 0.12)
        rect = QRectF((self.width() - side) / 2 + thick / 2, (self.height() - side) / 2 + thick / 2,
                      side - thick, side - thick)
        total = sum(v for _, v, _ in self.segments)
        pen = QPen(_c("surface2"), thick)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        if total:
            angle = 90.0
            for _, value, color in self.segments:
                span = value / total * 360
                pen = QPen(_c(color), thick)
                pen.setCapStyle(Qt.FlatCap)
                p.setPen(pen)
                gap = 1.2 if len(self.segments) > 1 and span > 3 else 0
                p.drawArc(rect, int((angle - gap / 2) * 16), int(-(span - gap) * 16))
                angle -= span
        p.setPen(_c("text"))
        f = QFont(self.font())
        f.setPixelSize(max(18, int(side * 0.16)))
        f.setBold(True)
        p.setFont(f)
        inner = rect.adjusted(thick, thick, -thick, -thick)
        p.drawText(QRectF(inner.left(), inner.center().y() - f.pixelSize(), inner.width(), f.pixelSize() + 4),
                   Qt.AlignHCenter | Qt.AlignBottom, self.top)
        f.setPixelSize(12)
        f.setBold(False)
        p.setFont(f)
        p.setPen(_c("muted"))
        p.drawText(QRectF(inner.left(), inner.center().y() + 4, inner.width(), 20), Qt.AlignHCenter | Qt.AlignTop,
                   self.bottom)
        p.end()


class StackedBars(QWidget):
    ROW_H, LABEL_W, VALUE_W = 26, 120, 70

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.fmt = [], "{:,}".format
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def set_data(self, rows, fmt=None):
        """rows: [(label, [(value, color, name)])] - drawn top to bottom, scaled to the largest row.
        fmt: how to print a value (default: 1,234)."""
        self.rows, self.fmt = rows, fmt or "{:,}".format
        self.setMinimumHeight(max(1, len(rows)) * self.ROW_H + 6)
        self.update()

    def mouseMoveEvent(self, e):
        i = int(e.position().y() // self.ROW_H)
        if 0 <= i < len(self.rows):
            label, segs = self.rows[i]
            self.setToolTip(label + "\n" + "\n".join(f"{n}: {self.fmt(v)}" for v, _, n in segs if v))
        else:
            self.setToolTip("")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top = max((sum(v for v, _, _ in segs) for _, segs in self.rows), default=0) or 1
        bar_x = self.LABEL_W + 8
        bar_w = max(40, self.width() - bar_x - self.VALUE_W - 8)
        fm = p.fontMetrics()
        for i, (label, segs) in enumerate(self.rows):
            y = i * self.ROW_H + 3
            p.setPen(_c("text"))
            p.drawText(QRectF(0, y, self.LABEL_W, self.ROW_H - 6), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(label, Qt.ElideMiddle, self.LABEL_W))
            total = sum(v for v, _, _ in segs)
            x = float(bar_x)
            clip = QPainterPath()
            width = bar_w * total / top
            clip.addRoundedRect(QRectF(bar_x, y + 3, max(width, 2), self.ROW_H - 12), 4, 4)
            p.save()
            p.setClipPath(clip)
            p.setPen(Qt.NoPen)
            for value, color, _ in segs:
                if value:
                    w = bar_w * value / top
                    p.setBrush(_c(color))
                    p.drawRect(QRectF(x, y + 3, w, self.ROW_H - 12))
                    x += w
            p.restore()
            p.setPen(_c("muted"))
            p.drawText(QRectF(bar_x + bar_w + 8, y, self.VALUE_W, self.ROW_H - 6), Qt.AlignVCenter | Qt.AlignLeft,
                       self.fmt(total))
        p.end()


class TrendChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points, self.fmt = [], str
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_data(self, points, fmt=str):
        """points: [(label, value)] oldest first"""
        self.points, self.fmt = points, fmt
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pts = self.points
        if len(pts) < 2:
            p.setPen(_c("faint"))
            p.drawText(self.rect(), Qt.AlignCenter, "Scan this folder again to see how it changes over time")
            p.end()
            return
        pad_l, pad_r, pad_t, pad_b = 8, 12, 22, 20
        area = QRectF(pad_l, pad_t, self.width() - pad_l - pad_r, self.height() - pad_t - pad_b)
        values = [v for _, v in pts]
        lo, hi = min(values), max(values)
        span = (hi - lo) or max(hi, 1)
        lo -= span * 0.15
        span *= 1.3
        xy = [QPointF(area.left() + area.width() * i / (len(pts) - 1),
                      area.bottom() - area.height() * (v - lo) / span) for i, (_, v) in enumerate(pts)]
        p.setPen(QPen(_c("border"), 1))
        p.drawLine(QPointF(area.left(), area.bottom()), QPointF(area.right(), area.bottom()))
        fill = QPainterPath(xy[0])
        for q in xy[1:]:
            fill.lineTo(q)
        fill.lineTo(xy[-1].x(), area.bottom())
        fill.lineTo(xy[0].x(), area.bottom())
        fill.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(_c("accent", 0.12))
        p.drawPath(fill)
        p.setPen(QPen(_c("accent"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for a, b in zip(xy, xy[1:]):
            p.drawLine(a, b)
        p.setBrush(_c("surface"))
        for q in xy:
            p.drawEllipse(q, 3.5, 3.5)
        p.setPen(_c("muted"))
        p.drawText(QRectF(pad_l, 2, self.width() - 20, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self.fmt(values[0])}  →  {self.fmt(values[-1])}")
        p.drawText(QRectF(pad_l, area.bottom() + 3, 200, 14), Qt.AlignLeft, pts[0][0])
        p.drawText(QRectF(self.width() - pad_r - 200, area.bottom() + 3, 200, 14), Qt.AlignRight, pts[-1][0])
        p.end()


class Legend(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.setMinimumHeight(46)  # room for two wrapped rows
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_items(self, items):
        """items: [(label, color)]"""
        self.items = items
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = p.fontMetrics()
        x, row_h, row = 0, 22, 0
        for label, color in self.items:
            w = fm.horizontalAdvance(label)
            if x and x + 14 + w > self.width():  # wrap to the next line
                x, row = 0, row + 1
            y = row * row_h + row_h / 2
            p.setPen(Qt.NoPen)
            p.setBrush(_c(color))
            p.drawEllipse(QPointF(x + 4, y), 4, 4)
            p.setPen(_c("muted"))
            p.drawText(QRectF(x + 14, row * row_h, w + 4, row_h), Qt.AlignVCenter | Qt.AlignLeft, label)
            x += 14 + w + 16
        p.end()
