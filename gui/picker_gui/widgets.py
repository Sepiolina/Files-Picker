"""
Reusable building blocks: surfaces, cards, path pickers, segmented controls,
navigation tabs, banners, empty states...

Colours always come from theme.current / the stylesheet, icons from icons.bind
(so both follow light/dark switching without rebuilding widgets).
"""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from . import icons, theme

# --------------------------------------------------------------------------- #
#  Small helpers                                                                #
# --------------------------------------------------------------------------- #


def label(text, name=None, wrap=False):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def set_prop(widget, name, value):
    """Change a style property (e.g. tone="danger") and refresh the stylesheet."""
    widget.setProperty(name, value)
    for w in [widget] + widget.findChildren(QWidget):
        w.style().unpolish(w)
        w.style().polish(w)


def set_tone(widget, tone):
    set_prop(widget, "tone", tone or "")


def hbox(*items, spacing=8, margins=(0, 0, 0, 0)):
    """Row layout. None = stretch, int = fixed gap."""
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def button(text, icon_name=None, kind=None, tooltip=""):
    """QPushButton with an optional theme-aware icon. kind: Primary | Small | Danger | Link."""
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        color = {"Primary": "on_accent", "Danger": "danger", "Link": "accent"}.get(kind, "text")
        icons.bind(b, icon_name, color, size=14 if kind == "Small" else 16)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def search_field(placeholder, on_change):
    """QLineEdit with a leading search icon."""
    box = QLineEdit()
    box.setObjectName("Search")
    box.setPlaceholderText(placeholder)
    box.setClearButtonEnabled(True)
    action = QAction(box)
    icons.bind(action, "search", "faint", size=16)
    box.addAction(action, QLineEdit.LeadingPosition)
    box.textChanged.connect(on_change)
    return box


def icon_label(name, color="muted", size=16):
    w = QLabel()
    w.setFixedSize(size, size)
    icons.bind(w, name, color, size)
    return w


def paint_surface(widget, radius=theme.CARD_RADIUS, shadow=True, fill="surface", hover=False):
    """
    A card surface: soft elevation shadow, fill and 1px hairline border.
    Painted in code (cheap) instead of QGraphicsDropShadowEffect, which would
    re-render the whole card - including a 180k-line editor - on every change.
    """
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(widget.rect()).adjusted(1, 1, -1, -3)
    if shadow:
        strength = 2.2 if theme.mode_is_dark else 1.0
        p.setPen(Qt.NoPen)
        for dy, alpha in ((3, 0.020), (2, 0.035), (1, 0.05)):
            p.setBrush(theme.qcolor("shadow", alpha * strength))
            p.drawRoundedRect(r.translated(0, dy), radius, radius)
    p.setBrush(theme.qcolor(fill))
    p.setPen(QPen(theme.qcolor("accent_ring" if hover else "border"), 1))
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    p.end()


# --------------------------------------------------------------------------- #
#  Surfaces                                                                     #
# --------------------------------------------------------------------------- #

class Panel(QFrame):
    """A plain card surface to put any content on (tables, details...)."""

    def __init__(self, margins=(0, 0, 0, 0), shadow=True, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self._shadow = shadow
        self.lay = QVBoxLayout(self)
        # +2 / +4: keep content inside the painted 1px border and the shadow below it
        self.lay.setContentsMargins(margins[0] + 2, margins[1] + 2, margins[2] + 2, margins[3] + 4)
        self.lay.setSpacing(0)

    def paintEvent(self, _):
        paint_surface(self, shadow=self._shadow)


class ElidedLabel(QLabel):
    """A label that shortens long text with … instead of growing the layout."""

    def __init__(self, text="", mode=Qt.ElideMiddle, name=None):
        super().__init__()
        self._full, self._mode = text, mode
        if name:
            self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self._refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def _refresh(self):
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, max(self.width(), 20)))


class Card(QFrame):
    """A numbered step: (1) Title ·········· [header widgets]  + body."""

    def __init__(self, step, title, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(17, 15, 17, 18)  # 16/16 + room for the painted shadow
        outer.setSpacing(12)
        self._step = str(step)
        self.num = label(self._step, "StepNumber")
        self.num.setAlignment(Qt.AlignCenter)
        self.title = label(title, "SectionTitle")
        self.header = hbox(self.num, 2, self.title, None, spacing=8)
        outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)

    def paintEvent(self, _):
        paint_surface(self)

    def add_to_header(self, widget):
        self.header.addWidget(widget)

    def set_done(self, done):
        """The step number turns into a check mark once the step is complete."""
        self.num.setText("✓" if done else self._step)
        set_prop(self.num, "done", bool(done))


# --------------------------------------------------------------------------- #
#  Inputs                                                                       #
# --------------------------------------------------------------------------- #

class PathField(QWidget):
    """
    Choose a folder or file.

    Empty → a text field with a Browse button (type, paste or drop a path).
    Set   → a compact card: [icon] **name**  parent/path  [Change]
            Click the card to edit the path as text.
    """

    changed = Signal(str)
    FOLDER, SAVE_FILE = "folder", "save"

    def __init__(self, mode, placeholder="", parent=None):
        super().__init__(parent)
        self.setObjectName("PathField")
        self.mode = mode
        self.file_filter = "All files (*)"
        self.default_name = ""
        self.setAcceptDrops(True)

        # Edit mode
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self.changed)
        self.edit.editingFinished.connect(self._show_display)
        self.browse_btn = button("Browse", "folder")
        self.browse_btn.clicked.connect(self.browse)
        edit_row = QWidget()
        edit_row.setLayout(hbox(self.edit, self.browse_btn))
        edit_row.layout().setStretch(0, 1)

        # Display mode
        self.card = QFrame()
        self.card.setObjectName("PathCard")
        self.card.setCursor(Qt.IBeamCursor)
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        self.name = ElidedLabel("", Qt.ElideRight, "PathName")
        self.name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.parent_path = ElidedLabel("", Qt.ElideLeft, "Hint")
        change = button("Change", kind="Small")
        change.clicked.connect(self.browse)
        card_lay = hbox(self.icon, 2, self.name, 6, self.parent_path, change, margins=(11, 0, 4, 0))
        card_lay.setStretch(4, 1)  # the parent path takes the spare room, not the button
        self.card.setLayout(card_lay)
        self.card.mouseReleaseEvent = lambda e: self._show_edit()

        self.stack = QStackedWidget()
        self.stack.addWidget(edit_row)
        self.stack.addWidget(self.card)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        self.stack.setFixedHeight(theme.CONTROL_H)

    def text(self):
        return self.edit.text().strip()

    def setText(self, t):
        self.edit.setText(t or "")
        self._show_display()

    def browse(self):
        start = self.text() or str(Path.home())
        if self.mode == self.FOLDER:
            path = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        else:
            if not self.text() and self.default_name:
                start = str(Path.home() / self.default_name)
            path, _ = QFileDialog.getSaveFileName(self, "Save as", start, self.file_filter)
        if path:
            self.setText(path)
        return path

    def _show_edit(self):
        self.stack.setCurrentIndex(0)
        self.edit.setFocus()
        self.edit.selectAll()

    def _show_display(self):
        path = self.text()
        if not path:
            self.stack.setCurrentIndex(0)
            return
        p = Path(path)
        is_folder = self.mode == self.FOLDER
        missing = is_folder and not p.is_dir()
        icons.bind(self.icon, "folder" if is_folder else "archive", "danger" if missing else "accent")
        self.name.setText(p.name or path)
        # The name is what matters: give it room (up to ~60%), the parent path gets the rest.
        self.name.setMinimumWidth(min(self.name.fontMetrics().horizontalAdvance(p.name or path) + 6,
                                      int(self.width() * 0.6) or 220))
        self.parent_path.setText(("Folder not found · " if missing else "") + (str(p.parent) if p.name else ""))
        self.card.setToolTip(path + "\n\nClick to edit")
        set_tone(self.card, "danger" if missing else None)
        self.stack.setCurrentIndex(1)

    def refresh(self):
        if self.stack.currentIndex() == 1:
            self._show_display()

    # drag & drop: highlight while hovering
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            set_prop(self, "drag", True)

    def dragLeaveEvent(self, e):
        set_prop(self, "drag", False)

    def dropEvent(self, e):
        set_prop(self, "drag", False)
        urls = e.mimeData().urls()
        if urls:
            self.setText(urls[0].toLocalFile())


class Segmented(QFrame):
    """Segmented control. options = [(value, label), ...]"""

    changed = Signal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        lay = hbox(spacing=2, margins=(3, 3, 3, 3))
        self.buttons = {}
        for value, text in options:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=value: self.changed.emit(v))
            self.group.addButton(b)
            self.buttons[value] = b
            lay.addWidget(b)
        self.setLayout(lay)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def value(self):
        for v, b in self.buttons.items():
            if b.isChecked():
                return v
        return next(iter(self.buttons))

    def setValue(self, v):
        if v in self.buttons:
            self.buttons[v].setChecked(True)


class TokenRow(QWidget):
    """A row of small read-only chips summarising collapsed settings. Clickable."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to change")
        self.lay = hbox(spacing=6)
        self.setLayout(self.lay)
        self._tokens = None

    def set_tokens(self, tokens):
        if tokens == self._tokens:
            return
        self._tokens = list(tokens)
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for t in tokens:
            self.lay.addWidget(label(t, "Token"))
        self.lay.addStretch(1)

    def mouseReleaseEvent(self, e):
        self.clicked.emit()


class ListEditor(QPlainTextEdit):
    """Plain-text editor for the name list; dropping a .txt/.csv imports it."""

    fileDropped = Signal(str)

    def canInsertFromMimeData(self, source):
        return source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasUrls():
            path = source.urls()[0].toLocalFile()
            if path:
                self.fileDropped.emit(path)
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, e):
        super().dragEnterEvent(e)
        if e.mimeData().hasUrls():
            set_prop(self, "drag", True)

    def dragLeaveEvent(self, e):
        super().dragLeaveEvent(e)
        set_prop(self, "drag", False)

    def dropEvent(self, e):
        set_prop(self, "drag", False)
        super().dropEvent(e)


# --------------------------------------------------------------------------- #
#  Navigation                                                                   #
# --------------------------------------------------------------------------- #

class NavTab(QPushButton):
    """Title-bar navigation tab: icon + text (+ optional count badge)."""

    def __init__(self, text, icon_name, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)  # title-bar control; keyboard users have Ctrl+1 / Ctrl+2
        self._icon_name = icon_name
        self._badge = 0
        self.toggled.connect(self._recolor)
        self._recolor(False)

    def _recolor(self, checked):
        icons.bind(self, self._icon_name, "text" if checked else "muted", size=15)

    def set_badge(self, n):
        self._badge = n
        self.setStyleSheet("padding-right: 34px;" if n else "")
        self.updateGeometry()
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._badge:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        text = str(self._badge) if self._badge < 1000 else "999+"
        font = p.font()
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        w = max(18, p.fontMetrics().horizontalAdvance(text) + 10)
        r = QRectF(self.width() - w - 10, (self.height() - 16) / 2, w, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor("border"))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(theme.qcolor("muted"))
        p.drawText(r, Qt.AlignCenter, text)


class NavTabs(QWidget):
    """Exclusive group of NavTabs. changed(value) when the user switches."""

    changed = Signal(str)

    def __init__(self, options, parent=None):  # options: [(value, text, icon_name)]
        super().__init__(parent)
        self.setObjectName("NavTabs")
        self.group = QButtonGroup(self)
        self.tabs = {}
        lay = hbox(spacing=2)
        for value, text, icon_name in options:
            t = NavTab(text, icon_name)
            t.clicked.connect(lambda _=False, v=value: self.changed.emit(v))
            self.group.addButton(t)
            self.tabs[value] = t
            lay.addWidget(t)
        self.setLayout(lay)

    def setValue(self, v):
        self.tabs[v].setChecked(True)


# --------------------------------------------------------------------------- #
#  Results & feedback                                                           #
# --------------------------------------------------------------------------- #

class StatTile(QFrame):
    """Big number + caption on a card. Optionally clickable."""

    clicked = Signal()

    def __init__(self, caption, icon_name=None, clickable=False, parent=None):
        super().__init__(parent)
        self.setObjectName("StatTile")
        self._clickable = clickable
        self._hover = False
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        self.value = label("–", "StatValue")
        self.caption = label(caption, "StatCaption")
        top = hbox(self.caption, None, spacing=6)
        if icon_name:
            top.addWidget(icon_label(icon_name, "faint", 15))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(17, 13, 17, 15)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.value)

    def paintEvent(self, _):
        paint_surface(self, radius=10, hover=self._hover)

    def set(self, value, tone=None):
        self.value.setText(value)
        set_tone(self.value, tone)

    def enterEvent(self, e):
        self._hover = self._clickable
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if self._clickable:
            self.clicked.emit()


class Banner(QFrame):
    """Inline message with optional action buttons. tone: success|warn|danger|None."""

    ICONS = {"success": "check", "warn": "alert", "danger": "x"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        self.icon = QLabel()
        self.icon.setObjectName("BannerIcon")
        self.icon.setAlignment(Qt.AlignCenter)
        self.title = label("", "BannerTitle", wrap=True)
        self.body = label("", "Muted", wrap=True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        close = QToolButton()
        close.setObjectName("Ghost")
        close.setToolTip("Dismiss")
        icons.bind(close, "x", "muted", 14)
        close.clicked.connect(self.hide)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.title)
        text.addWidget(self.body)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 8, 12)
        lay.setSpacing(12)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        lay.addLayout(text, 1)
        lay.addLayout(self.actions)
        lay.addWidget(close, 0, Qt.AlignTop)
        self.hide()

    def show_message(self, tone, title, body="", actions=()):
        while self.actions.count():
            w = self.actions.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, (text, fn) in enumerate(actions):
            b = button(text, kind="Small")
            # clicked() passes a "checked" bool; never let it reach the action
            # (lambda p=path: ... would otherwise receive False instead of the path).
            b.clicked.connect(lambda _checked=False, action=fn: action())
            self.actions.addWidget(b, 0, Qt.AlignVCenter)
        icons.bind(self.icon, self.ICONS.get(tone, "info"), "surface" if tone else "accent", size=14, stroke=2.2)
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        set_tone(self, tone)
        self.show()


class Chip(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Chip")

    def set(self, text, tone=None, tooltip=""):
        self.setText(text)
        self.setToolTip(tooltip)
        set_tone(self, tone)


class Checklist(QWidget):
    """
    Empty state that doubles as onboarding: a short intro, then the steps as
    rows that tick themselves off as the user completes them, then shortcuts.
    """

    def __init__(self, title, subtitle, steps, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        box = QWidget()
        box.setFixedWidth(440)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        hero = QLabel()
        hero.setObjectName("HeroIcon")
        hero.setAlignment(Qt.AlignCenter)
        icons.bind(hero, "search", "accent", size=26, stroke=2)
        lay.addWidget(hero, 0, Qt.AlignHCenter)
        lay.addSpacing(18)
        t = label(title, "H1")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        lay.addSpacing(6)
        s = label(subtitle, "Muted", wrap=True)
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        lay.addSpacing(24)

        self.rows = []
        for step_title, step_hint in steps:
            row = QFrame()
            row.setObjectName("CheckRow")
            dot = label("", "CheckDot")
            dot.setAlignment(Qt.AlignCenter)
            text = QVBoxLayout()
            text.setSpacing(1)
            st = label(step_title, "CheckTitle")
            text.addWidget(st)
            text.addWidget(label(step_hint, "Hint"))
            row.setLayout(hbox(dot, 4, text, None, spacing=8, margins=(14, 10, 14, 10)))
            lay.addWidget(row)
            lay.addSpacing(8)
            self.rows.append((row, dot, st))

        lay.addSpacing(12)
        keys = hbox(None, spacing=6)
        for k, what in (("Ctrl", None), ("Enter", "Export"), ("Ctrl", None), ("P", "Preview"), ("Esc", "Cancel")):
            keys.addWidget(label(k, "Kbd"))
            if what:
                keys.addWidget(label(what, "Faint"))
                keys.addSpacing(10)
        keys.addStretch(1)
        lay.addLayout(keys)

        outer.addWidget(box, 0, Qt.AlignHCenter)
        outer.addStretch(3)
        self.set_done([False] * len(steps))

    def set_done(self, flags):
        for i, ((row, dot, title), done) in enumerate(zip(self.rows, flags)):
            dot.setText("✓" if done else str(i + 1))
            for w in (row, dot, title):
                set_prop(w, "done", bool(done))


class EmptyState(QWidget):
    def __init__(self, title, body, icon_name="history", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        hero = QLabel()
        hero.setObjectName("HeroIcon")
        hero.setAlignment(Qt.AlignCenter)
        icons.bind(hero, icon_name, "accent", size=26, stroke=2)
        lay.addWidget(hero, 0, Qt.AlignHCenter)
        lay.addSpacing(16)
        t = label(title, "H2")
        b = label(body, "Muted", wrap=True)
        b.setFixedWidth(380)
        for w in (t, b):
            w.setAlignment(Qt.AlignCenter)
            lay.addWidget(w, 0, Qt.AlignHCenter)
        lay.addStretch(2)


def debounce(parent, ms, fn):
    """Call fn once, ms after the last trigger."""
    t = QTimer(parent, singleShot=True, interval=ms)
    t.timeout.connect(fn)
    return t
