"""
Duplicates tab: find files with identical content in a folder tree, see how much
space the extra copies waste, choose which copies to remove and move them to the
Trash / Recycle Bin (never a permanent delete).

Files are compared by content, not by name: same size -> same first 64 KB ->
same checksum of the whole file. A group always keeps at least one copy.
"""

import os
import sys
from datetime import datetime

from PySide6.QtCore import QFile, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHeaderView, QMessageBox,
                               QProgressBar, QScrollArea, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from . import charts, fscore as F, report as R
from .jobs import Worker, describe
from .preview import PreviewPanel, attach_preview
from .util import fmt_duration, fmt_int, fmt_size, reveal, scan_note
from .widgets import Banner, EmptyState, Panel, PathField, StatTile, button, hbox, label, search_field

MIN_SIZES = [(1, "Any size"), (1 << 10, "1 KB or more"), (100 << 10, "100 KB or more"), (1 << 20, "1 MB or more"),
             (10 << 20, "10 MB or more")]
MAX_GROUPS_SHOWN = 3000
ROLE_REL, ROLE_M, ROLE_SIZE = Qt.UserRole, Qt.UserRole + 1, Qt.UserRole + 2


def move_to_trash(path):
    """True on success. (PySide6 returns either a bool or a (bool, trash_path) pair.)"""
    if sys.platform.startswith("linux"):   # Qt needs this folder to exist to create the Trash inside it
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        try:
            os.makedirs(base, exist_ok=True)
        except OSError:
            pass
    res = QFile.moveToTrash(path)
    return bool(res[0] if isinstance(res, tuple) else res)


def fmt_mtime(ns):
    return datetime.fromtimestamp(ns / 1e9).strftime("%d %b %Y %H:%M") if ns else ""


def choose_keep(files, mode):
    """files: [(rel, mtime_ns)] -> index of the copy to keep. mode: newest | oldest | shortest"""
    idx = range(len(files))
    shortest = lambda i: (len(files[i][0]), files[i][0])
    if mode == "newest":
        return max(idx, key=lambda i: (files[i][1], -len(files[i][0])))
    if mode == "oldest":
        return min(idx, key=lambda i: (files[i][1], len(files[i][0])))
    return min(idx, key=shortest)


class DuplicatesPage(QWidget):
    changed = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.worker = None
        self.result = None
        self.root = ""

        # ---- controls ----------------------------------------------------------------
        self.path = PathField(PathField.FOLDER, "Folder to search for duplicate files")
        self.path.changed.connect(lambda _=None: self.changed.emit())
        self.min_size = QComboBox()
        for value, text in MIN_SIZES:
            self.min_size.addItem(text, value)
        self.min_size.setToolTip("Ignore small files — they rarely matter for disk space")
        self.min_size.activated.connect(lambda _: self.changed.emit())
        self.skip_hidden = QCheckBox("Skip hidden && system files")
        self.skip_hidden.setChecked(True)
        self.options_btn = button("Ignore rules && scan options…", kind="Link")
        self.options_btn.clicked.connect(self.settings_requested.emit)
        self.find_btn = button("Find duplicates", "layers", "Primary")
        self.find_btn.setProperty("large", True)
        self.find_btn.setMinimumWidth(160)
        self.cancel_btn = button("Cancel", "x")
        self.cancel_btn.hide()
        self.find_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())

        controls = Panel(margins=(18, 16, 18, 16))
        controls.lay.setSpacing(12)
        controls.lay.addWidget(label("Folder", "FieldLabel"))
        controls.lay.addWidget(self.path)
        controls.lay.addLayout(hbox(label("Only files", "FieldLabel"), self.min_size, 8, self.skip_hidden, 4,
                                    self.options_btn, None, self.cancel_btn, self.find_btn, spacing=10))
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.status = label("", "Hint")
        self.banner = Banner()

        # ---- results ---------------------------------------------------------------------
        self.t_groups, self.t_extra = StatTile("Duplicate groups", "layers"), StatTile("Extra copies")
        self.t_wasted, self.t_scanned = StatTile("Wasted space"), StatTile("Files scanned")
        tiles = hbox(self.t_groups, self.t_extra, self.t_wasted, self.t_scanned, spacing=12)
        for i in range(4):
            tiles.setStretch(i, 1)

        self.donut = charts.DonutChart()
        self.donut.setMinimumSize(150, 150)
        self.donut.setMaximumHeight(160)
        self.bars = charts.StackedBars()
        left = QVBoxLayout()
        left.addWidget(label("Space used by copies", "SectionTitle"))
        left.addWidget(self.donut, 1)
        right = QVBoxLayout()
        right.addWidget(label("Wasted space by file type", "SectionTitle"))
        right.addSpacing(6)
        right.addWidget(self.bars)
        right.addStretch(1)
        chart_panel = Panel(margins=(18, 14, 18, 14))
        chart_panel.lay.addLayout(hbox(left, 24, right, spacing=0))
        chart_panel.lay.itemAt(0).layout().setStretch(0, 2)
        chart_panel.lay.itemAt(0).layout().setStretch(2, 5)

        self.filter = search_field("Filter by name or folder…", self._apply_filter)
        self.filter.setFixedWidth(260)
        self.keep_btns = [button(text, kind="Small") for text in ("Keep newest", "Keep oldest", "Keep shortest path")]
        for b, mode in zip(self.keep_btns, ("newest", "oldest", "shortest")):
            b.clicked.connect(lambda _=False, m=mode: self._auto_select(m))
        self.clear_btn = button("Clear", kind="Small")
        self.clear_btn.clicked.connect(lambda: self._auto_select(None))
        self.selected_label = label("Nothing selected", "Hint")
        self.trash_btn = button("Move selected to Trash", "trash", "Danger")
        self.trash_btn.setEnabled(False)
        self.trash_btn.clicked.connect(self._trash)
        self.report_btn = button("Report", "file-text", tooltip="Save a shareable HTML or PDF report")
        self.report_btn.clicked.connect(self._report)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["File", "Size", "Modified", "Folder"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setMinimumHeight(340)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Interactive)
        self.tree.setColumnWidth(3, 220)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.itemDoubleClicked.connect(lambda item, _: self._reveal(item))

        tree_panel = Panel(margins=(0, 0, 0, 8))
        tree_panel.lay.addWidget(self.tree)
        results = QWidget()
        rl = QVBoxLayout(results)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        self.preview = PreviewPanel()
        self.preview_wrap, self.preview_btn = attach_preview(tree_panel, self.preview)
        rl.addLayout(hbox(label("Choose what to remove:", "Hint"), *self.keep_btns, self.clear_btn, None,
                          self.filter, self.preview_btn, self.report_btn, spacing=8))
        rl.addWidget(self.preview_wrap, 1)
        rl.addLayout(hbox(self.selected_label, None, self.trash_btn))
        self.note = label("Items go to your Trash / Recycle Bin, so they can be restored. "
                          "Each group always keeps at least one copy.  Double-click a file to show it in your file manager.",
                          "Hint", wrap=True)

        content = QWidget()
        content.setObjectName("Workspace")
        content.setAttribute(Qt.WA_StyledBackground, True)
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 6, 0)
        cl.setSpacing(14)
        cl.addLayout(tiles)
        cl.addWidget(chart_panel)
        cl.addWidget(results)
        cl.addWidget(self.note)
        scroll = QScrollArea()
        scroll.setObjectName("Scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setWidget(content)

        self.stack = QStackedWidget()
        self.empty = EmptyState(
            "Find duplicate files",
            "Pick a folder and File Picker finds files with identical content — even when the names differ — "
            "and shows how much space the extra copies take.", "layers")
        self.stack.addWidget(self.empty)
        self.path.changed.connect(self._sync_empty)
        self._sync_empty()
        self.stack.addWidget(scroll)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("Duplicates", "H1"))
        heading.addWidget(label("Find identical files and reclaim the space they waste.", "Muted"))
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
        self.find_btn.setEnabled(ready and not self.is_running())
        self.find_btn.setToolTip("" if ready else "Choose a folder first")
        if ready:
            self.empty.set_action("Find duplicates", self.start, "layers")
        else:
            self.empty.set_action("Choose a folder…", self.path.browse, "folder")

    # ---- state ------------------------------------------------------------------------------

    def state(self):
        return {"path": self.path.text(), "min_size": self.min_size.currentData(),
                "skip_hidden": self.skip_hidden.isChecked(), "preview": self.preview_btn.isChecked()}

    def restore(self, s):
        self.path.setText(s.get("path", ""))
        self.min_size.setCurrentIndex(max(0, self.min_size.findData(s.get("min_size", 1))))
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
            self.banner.show_message("danger", "Choose a folder to search first")
            return
        self.banner.hide()
        min_size, hidden = self.min_size.currentData(), self.skip_hidden.isChecked()
        self.worker = Worker(lambda cancel, prog: F.find_duplicates(folder, min_size, hidden, cancel, prog), self)
        self.worker.progress.connect(lambda p: self.status.setText(describe(p)))
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(lambda m: (self._busy(False), self.banner.show_message("danger", "Search failed", m)))
        self.worker.cancelled.connect(lambda: (self._busy(False), self.banner.show_message("warn", "Search cancelled")))
        self._busy(True)
        self.worker.start()

    def _busy(self, busy):
        self.progress.setVisible(busy)
        self.status.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.find_btn.setEnabled(not busy)
        if busy:
            self.status.setText("Starting…")

    def _on_done(self, result):
        self._busy(False)
        self.result = result
        self.root = result["stats"]["root"]
        self._fill_tree()
        self._update_stats()
        s = result["stats"]
        self.stack.setCurrentIndex(1)
        note = scan_note(s["cloud"], s["ignored"])
        if not result["groups"]:
            self.banner.show_message("success", "No duplicates found",
                                     f"Checked {fmt_int(s['files_scanned'])} files in {fmt_duration(s['elapsed'] * 1000)}. "
                                     + note)
        else:
            self.banner.show_message(
                "warn", f"{fmt_size(s['wasted'])} could be freed",
                f"{fmt_int(s['groups'])} groups of identical files, found in {fmt_duration(s['elapsed'] * 1000)}. "
                + (f"Showing the {MAX_GROUPS_SHOWN:,} largest groups. " if s["groups"] > MAX_GROUPS_SHOWN else "") + note)

    # ---- tree --------------------------------------------------------------------------------------

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        bold = QFont(self.font())
        bold.setBold(True)
        for g in self.result["groups"][:MAX_GROUPS_SHOWN]:
            n = len(g["files"])
            head = QTreeWidgetItem([f"{n} identical files  ·  {fmt_size(g['size'])} each", fmt_size(g["size"] * (n - 1)),
                                    "wasted", ""])
            head.setFont(0, bold)
            head.setFlags(Qt.ItemIsEnabled)
            head.setData(0, ROLE_SIZE, g["size"])
            for rel, m in g["files"]:
                folder, _, name = rel.rpartition("/")
                child = QTreeWidgetItem([name, fmt_size(g["size"]), fmt_mtime(m), folder or "(top level)"])
                child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
                child.setData(0, ROLE_REL, rel)
                child.setData(0, ROLE_M, m)
                child.setData(0, ROLE_SIZE, g["size"])
                child.setToolTip(0, rel)
                head.addChild(child)
            self.tree.addTopLevelItem(head)
            head.setExpanded(True)
        self.tree.blockSignals(False)
        self.preview.clear()
        self._update_selection_label()

    def _groups(self):
        for i in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(i)
            yield head, [head.child(j) for j in range(head.childCount())]

    def _item_changed(self, item, _col):
        if item.parent() is None:
            return
        head = item.parent()
        if item.checkState(0) == Qt.Checked and all(head.child(j).checkState(0) == Qt.Checked
                                                    for j in range(head.childCount())):
            self.tree.blockSignals(True)
            item.setCheckState(0, Qt.Unchecked)   # never allow removing every copy
            self.tree.blockSignals(False)
            self.selected_label.setText("Every group must keep at least one copy.")
            return
        self._update_selection_label()

    def _auto_select(self, mode):
        self.tree.blockSignals(True)
        for head, kids in self._groups():
            keep = choose_keep([(k.data(0, ROLE_REL), k.data(0, ROLE_M)) for k in kids], mode) if mode else -1
            for i, k in enumerate(kids):
                k.setCheckState(0, Qt.Checked if (mode and i != keep) else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_selection_label()

    def _checked(self):
        return [k for _, kids in self._groups() for k in kids if k.checkState(0) == Qt.Checked]

    def _update_selection_label(self):
        items = self._checked()
        total = sum(k.data(0, ROLE_SIZE) for k in items)
        self.trash_btn.setEnabled(bool(items))
        self.selected_label.setText(f"{fmt_int(len(items))} files selected  ·  {fmt_size(total)} will be freed"
                                    if items else "Nothing selected — tick the copies you want to remove")

    def _apply_filter(self, text):
        t = text.strip().lower()
        for head, kids in self._groups():
            head.setHidden(bool(t) and not any(t in k.data(0, ROLE_REL).lower() for k in kids))

    def _current_changed(self, cur, _prev):
        rel = cur.data(0, ROLE_REL) if cur is not None else None
        self.preview.show_path(os.path.join(self.root, rel.replace("/", os.sep)) if rel else None)

    def _reveal(self, item):
        rel = item.data(0, ROLE_REL)
        if rel:
            reveal(os.path.join(self.root, rel.replace("/", os.sep)))

    # ---- stats ---------------------------------------------------------------------------------------

    def _update_stats(self):
        """Recompute the tiles and charts from what is currently in the tree."""
        groups = [(kids[0].data(0, ROLE_SIZE), [k.data(0, ROLE_REL) for k in kids]) for _, kids in self._groups()]
        s = self.result["stats"]
        extra = sum(len(f) - 1 for _, f in groups)
        wasted = sum(size * (len(f) - 1) for size, f in groups)
        by_ext = {}
        for size, files in groups:
            ext = F.ext_of(files[0]) or "(none)"
            by_ext[ext] = by_ext.get(ext, 0) + size * (len(files) - 1)
        shown_all = len(groups) >= s["groups"]
        self.t_groups.set(fmt_int(len(groups)) + ("" if shown_all else "+"))
        self.t_extra.set(fmt_int(extra))
        self.t_wasted.set(fmt_size(wasted), "warn" if wasted else "success")
        self.t_scanned.set(fmt_int(s["files_scanned"]))
        total = max(s["bytes_scanned"], 1)
        pct = f"{wasted / total * 100:.0f}%"
        self.donut.set_data([("Wasted by copies", wasted, "warn"), ("Everything else", max(total - wasted, 0), "success")],
                            pct, "wasted")
        top = sorted(by_ext.items(), key=lambda kv: -kv[1])[:8]
        self.bars.set_data([(("." + e) if e != "(none)" else e, [(v, "accent", "Wasted")]) for e, v in top], fmt=fmt_size)
        self._stats_now = {"groups": groups, "extra": extra, "wasted": wasted, "by_ext": by_ext}

    # ---- removing --------------------------------------------------------------------------------------

    def _trash(self):
        items = self._checked()
        if not items:
            return
        for head, kids in self._groups():   # belt and braces: never remove the last copy
            if kids and all(k.checkState(0) == Qt.Checked for k in kids):
                self.banner.show_message("danger", "Nothing was removed", "A group would have no copy left.")
                return
        total = sum(k.data(0, ROLE_SIZE) for k in items)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Move to Trash")
        box.setText(f"Move {fmt_int(len(items))} files ({fmt_size(total)}) to the Trash?")
        box.setInformativeText("One copy of each file is always kept. You can restore the files from the Trash.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        done, failed, freed = [], [], 0
        for k in items:
            path = os.path.join(self.root, k.data(0, ROLE_REL).replace("/", os.sep))
            if os.path.exists(path) and move_to_trash(path):
                done.append(k)
                freed += k.data(0, ROLE_SIZE)
            else:
                failed.append(k.data(0, ROLE_REL))
        self.tree.blockSignals(True)
        for k in done:
            k.parent().removeChild(k)
        for head, kids in list(self._groups()):
            if len(kids) < 2:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(head))
            else:
                n = len(kids)
                head.setText(0, f"{n} identical files  ·  {fmt_size(head.data(0, ROLE_SIZE))} each")
                head.setText(1, fmt_size(head.data(0, ROLE_SIZE) * (n - 1)))
        self.tree.blockSignals(False)
        self._update_stats()
        self._update_selection_label()
        if failed:
            self.banner.show_message("warn", f"Moved {len(done)} files; {len(failed)} could not be moved",
                                     "They may be in use or the drive has no Trash. First: " + failed[0])
        else:
            self.banner.show_message("success", f"Freed {fmt_size(freed)}",
                                     f"{fmt_int(len(done))} files were moved to the Trash.")

    # ---- report ---------------------------------------------------------------------------------------------

    def _report(self):
        if not self.result:
            return
        st = self._stats_now
        s = self.result["stats"]
        rep = R.Report("Duplicate files", self.root)
        rep.tiles([("Duplicate groups", fmt_int(len(st["groups"]))), ("Extra copies", fmt_int(st["extra"])),
                   ("Wasted space", fmt_size(st["wasted"])), ("Files scanned", fmt_int(s["files_scanned"]))])
        total = max(s["bytes_scanned"], 1)
        rep.heading("Overview")
        rep.svg(R.svg_donut([("Wasted by copies", st["wasted"], "#D97706"),
                             ("Everything else", max(total - st["wasted"], 0), "#059669")],
                            f"{st['wasted'] / total * 100:.0f}%", "of scanned bytes"), 560)
        top = sorted(st["by_ext"].items(), key=lambda kv: -kv[1])[:10]
        if top:
            rep.heading("Wasted space by file type")
            rep.svg(R.svg_bars([(("." + e) if e != "(none)" else e, [(v, R.ACCENT, "Wasted")]) for e, v in top],
                               fmt=fmt_size), 640)
        rows = []
        for n, (size, files) in enumerate(st["groups"], 1):
            for i, rel in enumerate(files):
                rows.append([f"Group {n}" if i == 0 else "", fmt_size(size) if i == 0 else "",
                             fmt_size(size * (len(files) - 1)) if i == 0 else "", rel])
        rep.heading("Identical files")
        rep.table(["Group", "Size each", "Wasted", "Path"], rows, ["l", "r", "r", "l"],
                  note=scan_note(s["cloud"], s["ignored"]))
        self._save_report(rep, "duplicates-report")

    def _save_report(self, rep, name):
        try:
            path = R.ask_and_save(self, rep, name)
        except OSError as e:
            self.banner.show_message("danger", "Could not save the report", str(e))
            return
        if path:
            from .util import open_file
            self.banner.show_message("success", "Report saved", path, [("Open", lambda: open_file(path))])
