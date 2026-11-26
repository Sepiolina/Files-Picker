"""
Space tab: see what is using your disk. A treemap draws every folder and big file
as a tile whose area is its size - click a folder to look inside it. The list on
the right shows the same items with exact sizes.
"""

import os
import zlib

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QProgressBar, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from . import charts, fscore as F, report as R
from .jobs import Worker, describe
from .models import TableModel
from .preview import PreviewPanel
from .result_table import ResultTable
from .treemap_widget import DIR_COLORS, REST_COLOR, TYPE_COLORS, Item, TreemapWidget, file_type_group
from .util import fmt_duration, fmt_int, fmt_size, reveal, scan_note
from .widgets import Banner, EmptyState, Panel, PathField, StatTile, button, hbox, label

MAX_FILE_TILES, MAX_TILES = 60, 220


def items_for(node):
    """The tiles for one folder: sub-folders, its biggest files, and a lump for the rest."""
    items = []
    for ch in node.children.values():
        if ch.size > 0:
            color = DIR_COLORS[zlib.crc32(ch.name.encode("utf-8")) % len(DIR_COLORS)]
            items.append(Item(ch.name, ch.size, "dir", ch, color,
                              f"{ch.name}/\n{fmt_size(ch.size)} in {fmt_int(ch.count)} files"))
    files = sorted((f for f in node.files if f[1] > 0), key=lambda f: -f[1])
    for name, size, m in files[:MAX_FILE_TILES]:
        rel = f"{node.rel}/{name}" if node.rel else name
        items.append(Item(name, size, "file", rel, TYPE_COLORS[file_type_group(name)], f"{name}\n{fmt_size(size)}"))
    items.sort(key=lambda i: -i.size)
    lump = sum(f[1] for f in files[MAX_FILE_TILES:])
    n_lump = max(len(files) - MAX_FILE_TILES, 0)
    if len(items) > MAX_TILES:
        tail = items[MAX_TILES:]
        items = items[:MAX_TILES]
        lump += sum(i.size for i in tail)
        n_lump += len(tail)
    if lump > 0:
        items.append(Item(f"{fmt_int(n_lump)} smaller items", lump, "rest", None, REST_COLOR,
                          f"{fmt_int(n_lump)} smaller items\n{fmt_size(lump)} together"))
    return items


class SpaceModel(TableModel):
    COLUMNS = ["Name", "Kind", "Size", "Share"]

    def __init__(self):
        super().__init__()
        self.total = 1

    def set_rows(self, rows):
        self.total = sum(r.size for r in rows) or 1
        super().set_rows(rows)

    def cell(self, r, c):
        if c == 0:
            return r.label + ("/" if r.kind == "dir" else "")
        if c == 1:
            return {"dir": "Folder", "file": "File", "rest": "Many files"}[r.kind]
        return fmt_size(r.size) if c == 2 else f"{r.size / self.total * 100:.1f}%"

    def sort_key(self, r, c):
        return {2: r.size, 3: r.size}.get(c, r.label.lower() if c == 0 else self.cell(r, c))

    def color(self, r, c):
        return "muted" if c else None

    def is_numeric(self, c):
        return c in (2, 3)


class SpacePage(QWidget):
    changed = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.worker = None
        self.data = None
        self.current = None

        # ---- controls ------------------------------------------------------------
        self.path = PathField(PathField.FOLDER, "Folder or drive to measure")
        self.path.changed.connect(lambda _=None: self.changed.emit())
        self.skip_hidden = QCheckBox("Skip hidden && system files")
        self.skip_hidden.setChecked(True)
        self.options_btn = button("Ignore rules && scan options…", kind="Link")
        self.options_btn.clicked.connect(self.settings_requested.emit)
        self.scan_btn = button("Measure", "grid", "Primary")
        self.scan_btn.setProperty("large", True)
        self.scan_btn.setMinimumWidth(140)
        self.cancel_btn = button("Cancel", "x")
        self.cancel_btn.hide()
        self.scan_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.report_btn = button("Report", "file-text", tooltip="Save a shareable HTML or PDF report of this view")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._report)

        controls = Panel(margins=(18, 16, 18, 16))
        controls.lay.setSpacing(12)
        controls.lay.addWidget(label("Folder", "FieldLabel"))
        controls.lay.addWidget(self.path)
        controls.lay.addLayout(hbox(self.skip_hidden, 4, self.options_btn, None, self.report_btn, self.cancel_btn,
                                    self.scan_btn, spacing=10))
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.status = label("", "Hint")
        self.banner = Banner()

        # ---- results ----------------------------------------------------------------
        self.crumbs = QWidget()
        self.crumb_lay = hbox(spacing=4)
        self.crumbs.setLayout(self.crumb_lay)
        self.up_btn = button("↑ Up", kind="Small")
        self.up_btn.clicked.connect(self._up)
        self.t_size, self.t_files, self.t_big = StatTile("This folder", "grid"), StatTile("Files inside"), StatTile("Largest item")
        tiles = hbox(self.t_size, self.t_files, self.t_big, spacing=12)
        for i in range(3):
            tiles.setStretch(i, 1)

        self.treemap = TreemapWidget()
        self.treemap.folder_clicked.connect(lambda it: self._go(it.key))
        self.treemap.file_clicked.connect(self._file_picked)
        self.treemap.file_activated.connect(lambda it: self._reveal_file(it.key))
        self.legend = charts.Legend()
        self.legend.set_items([("Folders", DIR_COLORS[0])] + [(name.capitalize(), color) for name, color in TYPE_COLORS.items()])
        tm_panel = Panel(margins=(10, 10, 10, 8))
        tm_panel.lay.addWidget(self.treemap, 1)
        tm_panel.lay.addSpacing(4)
        tm_panel.lay.addWidget(self.legend)

        self.table = ResultTable(SpaceModel(), stretch_col=0, sort=(2, Qt.DescendingOrder), with_search=False,
                                 min_height=160, show_count=False)
        self.table.activated.connect(self._row_activated)
        self.table.selection_changed.connect(self._row_selected)
        self.preview = PreviewPanel()
        self.preview_btn = button("Preview", "eye", tooltip="Show a preview of the selected file")
        self.preview_btn.setCheckable(True)
        self.preview.hide()
        self.preview_btn.toggled.connect(self.preview.setVisible)
        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        right.setHandleWidth(10)
        right.addWidget(self.table)
        right.addWidget(self.preview)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        main = QSplitter(Qt.Horizontal)
        main.setChildrenCollapsible(False)
        main.setHandleWidth(10)
        main.addWidget(tm_panel)
        main.addWidget(right)
        main.setSizes([680, 380])

        results = QWidget()
        rl = QVBoxLayout(results)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)
        rl.addLayout(hbox(self.up_btn, self.crumbs, None, self.preview_btn, spacing=8))
        rl.addLayout(tiles)
        rl.addWidget(main, 1)

        self.stack = QStackedWidget()
        self.empty = EmptyState(
            "See what is using your space",
            "Measure a folder or a whole drive. Every folder and large file becomes a tile sized by how much "
            "space it takes — click to dig in.", "grid")
        self.stack.addWidget(self.empty)
        self.path.changed.connect(self._sync_empty)
        self._sync_empty()
        self.stack.addWidget(results)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("Space", "H1"))
        heading.addWidget(label("A map of your folders: bigger tile, more space used.", "Muted"))
        lay.addLayout(heading)
        lay.addWidget(controls)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addWidget(self.banner)
        lay.addWidget(self.stack, 1)
        self.progress.hide()
        self.status.hide()

    def _sync_empty(self, *_):
        """The empty-state button first asks for a folder, then starts the scan."""
        ready = bool(self.path.text()) and os.path.isdir(self.path.text())
        self.scan_btn.setEnabled(ready and not self.is_running())
        self.scan_btn.setToolTip("" if ready else "Choose a folder first")
        if ready:
            self.empty.set_action("Measure this folder", self.start, "grid")
        else:
            self.empty.set_action("Choose a folder…", self.path.browse, "folder")

    # ---- state ------------------------------------------------------------------------------

    def state(self):
        return {"path": self.path.text(), "skip_hidden": self.skip_hidden.isChecked(),
                "preview": self.preview_btn.isChecked()}

    def restore(self, s):
        self.path.setText(s.get("path", ""))
        self.skip_hidden.setChecked(s.get("skip_hidden", True))
        self.preview_btn.setChecked(s.get("preview", False))

    def is_running(self):
        return self.worker is not None and self.worker.isRunning()

    def stop_background_work(self):
        if self.worker:
            self.worker.stop()

    # ---- running ------------------------------------------------------------------------------

    def start(self):
        if self.is_running():
            return
        folder = self.path.text()
        if not folder or not os.path.isdir(folder):
            self.banner.show_message("danger", "Choose a folder or drive to measure first")
            return
        self.banner.hide()
        hidden = self.skip_hidden.isChecked()
        self.worker = Worker(lambda cancel, prog: F.scan_sizes(folder, hidden, cancel, prog), self)
        self.worker.progress.connect(lambda p: self.status.setText(describe(p)))
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(lambda m: (self._busy(False), self.banner.show_message("danger", "Measuring failed", m)))
        self.worker.cancelled.connect(lambda: (self._busy(False), self.banner.show_message("warn", "Cancelled")))
        self._busy(True)
        self.worker.start()

    def _busy(self, busy):
        self.progress.setVisible(busy)
        self.status.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.scan_btn.setEnabled(not busy)
        if busy:
            self.status.setText("Starting…")

    def _on_done(self, data):
        self._busy(False)
        self.data = data
        self.report_btn.setEnabled(True)
        self.stack.setCurrentIndex(1)
        self._show(data["root"])
        note = scan_note(data["cloud"], data["ignored"])
        self.banner.show_message("success", f"{fmt_size(data['bytes'])} in {fmt_int(data['files'])} files",
                                 f"Measured in {fmt_duration(data['elapsed'] * 1000)}. " + note)

    # ---- navigation ---------------------------------------------------------------------------------

    def _node_at(self, rel):
        node = self.data["root"]
        for part in rel.split("/") if rel else []:
            node = node.children.get(part)
            if node is None:
                return self.data["root"]
        return node

    def _go(self, node):
        self._show(node if not isinstance(node, str) else self._node_at(node))

    def _up(self):
        if self.current is not None and self.current.parent is not None:
            self._show(self.current.parent)

    def _show(self, node):
        self.current = node
        items = items_for(node)
        self.treemap.set_items(items)
        self.table.set_rows(items)
        self.preview.clear()
        self.up_btn.setEnabled(node.parent is not None)
        # breadcrumb
        while self.crumb_lay.count():
            w = self.crumb_lay.takeAt(0).widget()
            if w:
                w.setParent(None)   # leave the layout now; deleteLater alone keeps painting until the next event
                w.deleteLater()
        chain = [self.data["root"]] + node.path_parts()
        for i, n in enumerate(chain):
            b = button(n.name if i else self.data["path"], kind="Link" if i < len(chain) - 1 else "Small")
            b.setEnabled(i < len(chain) - 1)
            b.clicked.connect(lambda _=False, target=n: self._show(target))
            self.crumb_lay.addWidget(b)
            if i < len(chain) - 1:
                self.crumb_lay.addWidget(label("›", "Hint"))
        self.crumb_lay.addStretch(1)
        self.t_size.set(fmt_size(node.size))
        self.t_files.set(fmt_int(node.count))
        biggest = max(items, key=lambda i: i.size, default=None)
        self.t_big.set(fmt_size(biggest.size) if biggest else "–")

    # ---- selection ------------------------------------------------------------------------------------

    def _abs(self, rel):
        return os.path.join(self.data["path"], rel.replace("/", os.sep))

    def _file_picked(self, item):
        self.preview.show_path(self._abs(item.key))
        for i, r in enumerate(self.table.model.rows):
            if r.key == item.key:
                self.table.view.selectRow(self.table.proxy.mapFromSource(self.table.model.index(i, 0)).row())
                break

    def _row_selected(self):
        row = self.table.selected()
        if row is None:
            return
        self.treemap.select_key(row.key)
        if row.kind == "file":
            self.preview.show_path(self._abs(row.key))
        else:
            self.preview.clear("Double-click a folder to open it" if row.kind == "dir" else "Many small files")

    def _row_activated(self, row):
        if row.kind == "dir":
            self._go(row.key)
        elif row.kind == "file":
            self._reveal_file(row.key)

    def _reveal_file(self, rel):
        reveal(self._abs(rel))

    # ---- report ---------------------------------------------------------------------------------------------

    def _report(self):
        if not self.data or self.current is None:
            return
        node, items = self.current, items_for(self.current)
        where = self.data["path"] + (os.sep + node.rel.replace("/", os.sep) if node.rel else "")
        rep = R.Report("Disk space report", where)
        big = max(items, key=lambda i: i.size, default=None)
        rep.tiles([("This folder", fmt_size(node.size)), ("Files inside", fmt_int(node.count)),
                   ("Largest item", fmt_size(big.size) if big else "–")])
        rep.heading("Map (tile area = size)")
        rep.svg(R.svg_treemap([(i.label + ("/" if i.kind == "dir" else ""), i.size, i.color, fmt_size(i.size))
                               for i in items[:80]]), 720)
        total = sum(i.size for i in items) or 1
        rep.heading("Biggest items")
        rep.table(["Name", "Kind", "Size", "Share"],
                  [[i.label + ("/" if i.kind == "dir" else ""), {"dir": "Folder", "file": "File", "rest": "Many files"}[i.kind],
                    fmt_size(i.size), f"{i.size / total * 100:.1f}%"] for i in items], ["l", "l", "r", "r"],
                  note=scan_note(self.data["cloud"], self.data["ignored"]))
        try:
            path = R.ask_and_save(self, rep, "space-report")
        except OSError as e:
            self.banner.show_message("danger", "Could not save the report", str(e))
            return
        if path:
            from .util import open_file
            self.banner.show_message("success", "Report saved", path, [("Open", lambda: open_file(path))])
