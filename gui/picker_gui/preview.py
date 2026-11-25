"""
A small preview panel: pictures are shown scaled, text files show their first
lines, everything else shows its details. Used by Compare, Snapshots, Duplicates,
Space and Find.

Safety: only the start of a file is read, large pictures are skipped, and
cloud-only placeholder files are never opened (opening one would download it).
"""

import os
from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase, QImageReader, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QSplitter, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import fscore
from .util import fmt_size, open_file, reveal
from .widgets import ElidedLabel, Panel, button, label

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "ico", "tif", "tiff", "svg", "jfif", "ppm", "pgm"}
MAX_IMAGE_BYTES = 64 << 20
TEXT_BYTES = 64 << 10
MAX_LINES = 400


def decode_text(data):
    """bytes -> str, or None when it looks binary."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\0" in data:
        return None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


class PreviewPanel(Panel):
    def __init__(self, title="Preview", parent=None):
        super().__init__(margins=(14, 12, 14, 12), parent=parent)
        self.path = None
        self.setMinimumWidth(240)
        self.title = label(title, "FieldLabel")
        self.msg = label("", "Muted", wrap=True)
        self.msg.setAlignment(Qt.AlignCenter)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setFrameShape(QPlainTextEdit.NoFrame)
        self.stack = QStackedWidget()
        for w in (self.msg, self.image, self.text):
            self.stack.addWidget(w)
        self.name = ElidedLabel("", Qt.ElideMiddle)
        self.name.setStyleSheet("font-weight: 600;")
        self.meta = label("", "Hint", wrap=True)
        self.open_btn = button("Open", "external", "Small")
        self.show_btn = button("Show in folder", "folder", "Small")
        self.open_btn.clicked.connect(lambda: self.path and open_file(self.path))
        self.show_btn.clicked.connect(lambda: self.path and reveal(self.path))
        self.lay.setSpacing(8)
        self.lay.addWidget(self.title)
        self.lay.addWidget(self.stack, 1)
        self.lay.addWidget(self.name)
        self.lay.addWidget(self.meta)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.open_btn)
        row.addWidget(self.show_btn)
        row.addStretch(1)
        self.lay.addLayout(row)
        self._pixmap = None
        self.clear()

    def clear(self, text="Select a file to preview it"):
        self.path, self._pixmap = None, None
        self.msg.setText(text)
        self.stack.setCurrentWidget(self.msg)
        self.name.setText("")
        self.meta.setText("")
        self.open_btn.setEnabled(False)
        self.show_btn.setEnabled(False)

    def show_path(self, path, caption=None):
        if not path:
            return self.clear()
        try:
            st = os.stat(path)
        except OSError:
            return self.clear("This file no longer exists.")
        self.path = path
        self.name.setText(caption or os.path.basename(path))
        self.open_btn.setEnabled(os.path.isfile(path))
        self.show_btn.setEnabled(True)
        when = datetime.fromtimestamp(st.st_mtime).strftime("%d %b %Y, %H:%M")
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        kind = f".{ext} file" if ext else "file"
        if os.path.isdir(path):
            self.meta.setText("Folder")
            return self._message("Folder")
        self.meta.setText(f"{fmt_size(st.st_size)}  ·  {kind}  ·  modified {when}")
        if fscore.is_cloud_placeholder(os.path.basename(path), st):
            return self._message("Online-only cloud file. It is not stored on this computer, so it is not "
                                 "previewed — nothing is downloaded.")
        if ext in IMAGE_EXTS and self._show_image(path, st.st_size):
            return
        if st.st_size == 0:
            return self._message("Empty file")
        try:
            with open(path, "rb") as f:
                data = f.read(TEXT_BYTES)
        except OSError as e:
            return self._message(f"Could not read the file.\n{e.strerror or e}")
        text = decode_text(data)
        if text is None:
            return self._message("No preview for this type of file.")
        lines = text.splitlines()
        shown = "\n".join(lines[:MAX_LINES])
        if len(lines) > MAX_LINES or st.st_size > TEXT_BYTES:
            shown += "\n…"
        self.text.setPlainText(shown)
        self.stack.setCurrentWidget(self.text)

    def _message(self, text):
        self.msg.setText(text)
        self.stack.setCurrentWidget(self.msg)

    def _show_image(self, path, size):
        if size > MAX_IMAGE_BYTES:
            self._message("This picture is too large to preview.")
            return True
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        s = reader.size()
        if s.isValid() and max(s.width(), s.height()) > 1600:
            s.scale(QSize(1600, 1600), Qt.KeepAspectRatio)
            reader.setScaledSize(s)
        img = reader.read()
        if img.isNull():
            return False
        self._pixmap = QPixmap.fromImage(img)
        self.stack.setCurrentWidget(self.image)
        self._fit()
        if s.isValid():
            self.meta.setText(self.meta.text() + f"  ·  {reader.size().width()}×{reader.size().height()} px")
        return True

    def _fit(self):
        if self._pixmap is not None:
            box = self.image.size()
            self.image.setPixmap(self._pixmap.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                 if not self._pixmap.isNull() else self._pixmap)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


def attach_preview(content, panel, start_open=False):
    """
    Put `panel` to the right of `content` in a splitter and return (splitter, toggle_button).
    The button (checkable) shows / hides the panel.
    """
    split = QSplitter(Qt.Horizontal)
    split.setHandleWidth(10)
    split.setChildrenCollapsible(False)
    split.addWidget(content)
    split.addWidget(panel)
    split.setStretchFactor(0, 3)
    split.setStretchFactor(1, 1)
    toggle = button("Preview", "eye", tooltip="Show a preview of the selected file")
    toggle.setCheckable(True)

    def apply(on):
        panel.setVisible(on)
        if on:
            total = max(split.width(), 900)
            split.setSizes([int(total * 0.64), int(total * 0.36)])

    toggle.toggled.connect(apply)
    toggle.setChecked(start_open)
    apply(start_open)
    return split, toggle
