"""
Compare tab: pick two folders (same drive or different drives) and see how they
differ - as tiles, a donut, stacked bars and a filterable table.

    Identical   same path (or name) and same size [/ date / content]
    Different   same path but the file differs
    Moved       same name + size, but in a different sub-folder
    Only in A / Only in B
"""

import csv
import os
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QProgressBar, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import charts, fscore as F, report as R
from .jobs import Worker, describe
from .models import TableModel
from .preview import PreviewPanel, attach_preview
from .result_table import ResultTable
from .util import fmt_duration, fmt_int, fmt_size, reveal, scan_note
from .widgets import (Banner, EmptyState, Panel, PathField, Segmented, StatTile, button, hbox, label)

# status -> (label, colour: theme key or #hex, sort rank)
STATUS = {
    "diff": ("Different", "warn", 0),
    "a_only": ("Only in A", "accent", 1),
    "b_only": ("Only in B", "#06B6D4", 2),
    "moved": ("Moved", "#8B5CF6", 3),
    "same": ("Identical", "success", 4),
}
MODES = [("size", "Size"), ("date", "Size + date"), ("content", "Content")]
MODE_HINT = {
    "size": "Fastest: files are the same when their sizes match.",
    "date": "Files are the same when size and modified time match (2 s tolerance).",
    "content": "Most accurate: files with equal sizes are read and compared by checksum. Slower on big folders.",
}


def fmt_time(ns):
    return datetime.fromtimestamp(ns / 1e9).strftime("%d %b %Y %H:%M") if ns else ""


class CompareModel(TableModel):
    COLUMNS = ["Status", "File", "Size in A", "Size in B", "Modified in A", "Modified in B"]

    def cell(self, r, c):
        if c == 0:
            return STATUS[r.status][0]
        if c == 1:
            return r.rel
        if c == 2:
            return fmt_size(r.size_a) if r.size_a is not None else "–"
        if c == 3:
            return fmt_size(r.size_b) if r.size_b is not None else "–"
        return fmt_time(r.m_a if c == 4 else r.m_b) or "–"

    def sort_key(self, r, c):
        if c == 0:
            return STATUS[r.status][2]
        if c in (2, 3):
            return (r.size_a if c == 2 else r.size_b) or 0
        if c in (4, 5):
            return (r.m_a if c == 4 else r.m_b) or 0
        return r.rel.lower()

    def color(self, r, c):
        if c == 0:
            return STATUS[r.status][1]
        return "muted" if c >= 2 else None

    def is_numeric(self, c):
        return c in (2, 3)


REPORT_COLORS = {"same": "#059669", "diff": "#D97706", "moved": "#8B5CF6", "a_only": "#5B54F0", "b_only": "#06B6D4"}


class ComparePage(QWidget):
    changed = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.worker = None
        self.result = None

        # ---- controls --------------------------------------------------------
        self.path_a, self.path_b = PathField(PathField.FOLDER, "Folder A — any folder or drive"), \
            PathField(PathField.FOLDER, "Folder B — compare it with…")
        self.swap_btn = button("", "swap", tooltip="Swap A and B")
        self.swap_btn.setFixedWidth(40)
        self.swap_btn.clicked.connect(self._swap)
        self.mode = Segmented(MODES)
        self.mode.setValue("size")
        self.mode_hint = label(MODE_HINT["size"], "Hint")
        self.by_name = QCheckBox("Ignore sub-folders")
        self.by_name.setToolTip("Match files by name only, wherever they are.\nUse this when the two folders are organised differently.")
        self.skip_hidden = QCheckBox("Skip hidden && system files")
        self.skip_hidden.setChecked(True)
        self.compare_btn = button("Compare", "compare", "Primary")
        self.compare_btn.setProperty("large", True)
        self.compare_btn.setMinimumWidth(140)
        self.cancel_btn = button("Cancel", "x")
        self.cancel_btn.hide()
        self.export_btn = button("Export CSV", "download")
        self.export_btn.setEnabled(False)
        self.report_btn = button("Report", "file-text", tooltip="Save a shareable HTML or PDF report")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._report)
        self.options_btn = button("Ignore rules && scan options…", kind="Link")
        self.options_btn.clicked.connect(self.settings_requested.emit)
        self.compare_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.export_btn.clicked.connect(self._export)
        self.mode.changed.connect(lambda v: (self.mode_hint.setText(MODE_HINT[v]), self.changed.emit()))
        for w in (self.path_a, self.path_b):
            w.changed.connect(lambda _=None: self.changed.emit())

        controls = Panel(margins=(18, 16, 18, 16))
        cl = controls.lay
        cl.setSpacing(12)
        pick = hbox(spacing=8)
        col_a, col_b = QVBoxLayout(), QVBoxLayout()
        col_a.setSpacing(4)
        col_b.setSpacing(4)
        col_a.addWidget(label("Folder A", "FieldLabel"))
        col_a.addWidget(self.path_a)
        col_b.addWidget(label("Folder B", "FieldLabel"))
        col_b.addWidget(self.path_b)
        swap_col = QVBoxLayout()
        swap_col.addSpacing(20)
        swap_col.addWidget(self.swap_btn)
        pick.addLayout(col_a, 1)
        pick.addLayout(swap_col)
        pick.addLayout(col_b, 1)
        cl.addLayout(pick)
        cl.addLayout(hbox(label("Compare by", "FieldLabel"), self.mode, self.mode_hint, None, spacing=10))
        self.opts_row = hbox(self.by_name, 12, self.skip_hidden, 4, self.options_btn, None, self.report_btn,
                             self.export_btn, self.cancel_btn, self.compare_btn, spacing=10)
        cl.addLayout(self.opts_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.status = label("", "Hint")
        self.banner = Banner()

        # ---- results -----------------------------------------------------------
        self.tiles = {}
        tile_row = hbox(spacing=12)
        for key in ("same", "diff", "moved", "a_only", "b_only"):
            t = StatTile(STATUS[key][0], clickable=True)
            t.clicked.connect(lambda k=key: self._filter(k))
            self.tiles[key] = t
            tile_row.addWidget(t, 1)
        self.sim_tile = StatTile("Similarity", "compare")
        tile_row.addWidget(self.sim_tile, 1)

        self.donut = charts.DonutChart()
        self.legend = charts.Legend()
        donut_box = QVBoxLayout()
        donut_box.addWidget(label("Overview", "SectionTitle"))
        donut_box.addWidget(self.donut, 1)
        donut_box.addWidget(self.legend)
        self.group = Segmented([("type", "File type"), ("folder", "Top folder"), ("size", "Size")])
        self.group.setValue("type")
        self.group.changed.connect(lambda _: self._draw_bars())
        self.bars = charts.StackedBars()
        bars_box = QVBoxLayout()
        bars_box.addLayout(hbox(label("Where the differences are", "SectionTitle"), None, self.group))
        bars_box.addSpacing(6)
        bars_box.addWidget(self.bars)
        bars_box.addStretch(1)
        chart_panel = Panel(margins=(18, 14, 18, 14))
        chart_panel.lay.addLayout(hbox(donut_box, 24, bars_box, spacing=0))
        chart_panel.lay.itemAt(0).layout().setStretch(0, 2)
        chart_panel.lay.itemAt(0).layout().setStretch(2, 5)

        self.summary = label("", "Hint", wrap=True)
        self.table = ResultTable(CompareModel(), path_cols=(1,), status_col=0, stretch_col=1,
                                 sort=(0, Qt.AscendingOrder), placeholder="Filter files…", min_height=360)
        self.table.activated.connect(self._reveal)
        self.prev_a, self.prev_b = PreviewPanel("In folder A"), PreviewPanel("In folder B")
        prev_box = QWidget()
        pl = QVBoxLayout(prev_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        pl.addWidget(self.prev_a)
        pl.addWidget(self.prev_b)
        self.split, self.preview_btn = attach_preview(self.table, prev_box)
        self.opts_row.insertWidget(self.opts_row.indexOf(self.report_btn), self.preview_btn)
        self.table.selection_changed.connect(self._row_selected)

        content = QWidget()
        content.setObjectName("Workspace")
        content.setAttribute(Qt.WA_StyledBackground, True)
        rl = QVBoxLayout(content)
        rl.setContentsMargins(0, 0, 6, 0)
        rl.setSpacing(14)
        rl.addLayout(tile_row)
        rl.addWidget(chart_panel)
        rl.addWidget(self.summary)
        rl.addWidget(self.split)
        scroll = QScrollArea()
        scroll.setObjectName("Scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setWidget(content)

        self.stack = QStackedWidget()
        self.empty = EmptyState(
            "Compare two folders",
            "Pick any two folders — on the same drive or different ones — and see what is identical, "
            "different, moved or missing.", "compare")
        self.stack.addWidget(self.empty)
        self.path_a.changed.connect(self._sync_empty)
        self.path_b.changed.connect(self._sync_empty)
        self._sync_empty()
        self.stack.addWidget(scroll)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("Compare", "H1"))
        heading.addWidget(label("See how two folders differ — visually and in numbers.", "Muted"))
        lay.addLayout(heading)
        lay.addWidget(controls)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addWidget(self.banner)
        lay.addWidget(self.stack, 1)
        self.progress.hide()
        self.status.hide()

    def _sync_empty(self, *_):
        """The empty-state button walks you through: folder A, folder B, then Compare."""
        ok = lambda p: bool(p) and os.path.isdir(p)
        ready = ok(self.path_a.text()) and ok(self.path_b.text())
        self.compare_btn.setEnabled(ready and not self.is_running())
        self.compare_btn.setToolTip("" if ready else "Choose both folders first")
        if not ok(self.path_a.text()):
            self.empty.set_action("Choose folder A…", self.path_a.browse, "folder")
        elif not ok(self.path_b.text()):
            self.empty.set_action("Choose folder B…", self.path_b.browse, "folder")
        else:
            self.empty.set_action("Compare the folders", self.start, "compare")

    # ---- state ----------------------------------------------------------------

    def state(self):
        return {"a": self.path_a.text(), "b": self.path_b.text(), "mode": self.mode.value(),
                "by_name": self.by_name.isChecked(), "skip_hidden": self.skip_hidden.isChecked(),
                "preview": self.preview_btn.isChecked()}

    def restore(self, s):
        self.path_a.setText(s.get("a", ""))
        self.path_b.setText(s.get("b", ""))
        self.mode.setValue(s.get("mode", "size"))
        self.mode_hint.setText(MODE_HINT[self.mode.value()])
        self.by_name.setChecked(s.get("by_name", False))
        self.skip_hidden.setChecked(s.get("skip_hidden", True))
        self.preview_btn.setChecked(s.get("preview", False))

    def is_running(self):
        return self.worker is not None and self.worker.isRunning()

    def stop_background_work(self):
        if self.worker:
            self.worker.stop()

    def _swap(self):
        a, b = self.path_a.text(), self.path_b.text()
        self.path_a.setText(b)
        self.path_b.setText(a)

    # ---- running ----------------------------------------------------------------

    def start(self):
        if self.is_running():
            return
        a, b = self.path_a.text(), self.path_b.text()
        import os
        for name, p in (("A", a), ("B", b)):
            if not p or not os.path.isdir(p):
                self.banner.show_message("danger", f"Folder {name} isn't set or doesn't exist")
                return
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            self.banner.show_message("warn", "Both folders are the same", "Choose two different folders to compare.")
            return
        self.banner.hide()
        mode, by_name, hidden = self.mode.value(), self.by_name.isChecked(), self.skip_hidden.isChecked()
        self.worker = Worker(lambda cancel, prog: F.compare_folders(a, b, mode, by_name, hidden, cancel, prog), self)
        self.worker.progress.connect(lambda p: self.status.setText(describe(p)))
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(lambda m: (self._busy(False), self.banner.show_message("danger", "Comparison failed", m)))
        self.worker.cancelled.connect(lambda: (self._busy(False), self.banner.show_message("warn", "Comparison cancelled")))
        self._busy(True)
        self.worker.start()

    def _busy(self, busy):
        self.progress.setVisible(busy)
        self.status.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.compare_btn.setEnabled(not busy)
        if busy:
            self.status.setText("Starting…")

    def _on_done(self, result):
        self._busy(False)
        self.result = result
        s = result["stats"]
        counts = s["counts"]
        for key, tile in self.tiles.items():
            tile.set(fmt_int(counts.get(key, 0)))
        self.sim_tile.set(f"{s['similarity']:.0f}%", "success" if s["similarity"] >= 99.5 else None)
        segs = [(STATUS[k][0], counts.get(k, 0), STATUS[k][1]) for k in ("same", "diff", "moved", "a_only", "b_only")]
        self.donut.set_data(segs, fmt_int(s["total"]), "files compared")
        self.legend.set_items([(f"{STATUS[k][0]} {fmt_int(counts.get(k, 0))}", STATUS[k][1])
                               for k in ("same", "diff", "moved", "a_only", "b_only") if counts.get(k)])
        self._draw_bars()
        mode_text = {m: t for m, t in MODES}[s["mode"]].lower()
        self.summary.setText(
            f"A: {fmt_int(s['files_a'])} files, {fmt_size(s['bytes_a'])}   ·   "
            f"B: {fmt_int(s['files_b'])} files, {fmt_size(s['bytes_b'])}   ·   compared by {mode_text}"
            f"{' (names only)' if s['by_name'] else ''} in {fmt_duration(s['elapsed'] * 1000)}"
            + (f"   ·   {fmt_int(s['errors'])} unreadable folders skipped" if s["errors"] else "")
            + ("   ·   " + scan_note(s["cloud"], s["ignored"]) if s["cloud"] or s["ignored"] else "")
            + "   ·   double-click a row to show it in your file manager")
        self.table.set_rows(result["rows"], status_of=lambda r: r.status)
        self.export_btn.setEnabled(bool(result["rows"]))
        self.report_btn.setEnabled(True)
        self.prev_a.clear()
        self.prev_b.clear()
        self.stack.setCurrentIndex(1)
        if not result["rows"]:
            self.banner.show_message("warn", "Both folders are empty")
        elif counts.get("same") == s["total"]:
            self.banner.show_message("success", "The folders are identical")

    def _filter(self, key):
        self.table.set_status(key, STATUS[key][0])

    # ---- charts -------------------------------------------------------------------

    def _draw_bars(self):
        if not self.result:
            return
        s = self.result["stats"]
        kind = self.group.value()
        data = {"type": s["by_ext"], "folder": s["by_top"], "size": s["by_size"]}[kind]
        order = ("diff", "a_only", "b_only", "moved", "same")

        def total(counts):
            return sum(counts.values())

        if kind == "size":
            names = [b for b, _ in F.SIZE_BUCKETS if b in data]
        else:
            names = sorted(data, key=lambda k: -total(data[k]))[:8]
        rows = [(("." + n if kind == "type" and n != "(none)" else n),
                 [(data[n].get(k, 0), STATUS[k][1], STATUS[k][0]) for k in order]) for n in names]
        if kind != "size" and len(data) > 8:
            rest = {k: sum(data[n].get(k, 0) for n in data if n not in names) for k in order}
            rows.append((f"{len(data) - 8} others", [(rest[k], STATUS[k][1], STATUS[k][0]) for k in order]))
        self.bars.set_data(rows)

    # ---- actions --------------------------------------------------------------------

    def _reveal(self, row):
        import os
        for root, rel in ((self.result["stats"]["a"], row.rel_a), (self.result["stats"]["b"], row.rel_b)):
            if rel:
                path = os.path.join(root, rel.replace("/", os.sep))
                if os.path.exists(path):
                    reveal(path)
                    return

    def _row_selected(self):
        row = self.table.selected()
        if row is None or not self.result:
            return
        s = self.result["stats"]
        for panel, root, rel, name in ((self.prev_a, s["a"], row.rel_a, "A"), (self.prev_b, s["b"], row.rel_b, "B")):
            if rel:
                panel.show_path(os.path.join(root, rel.replace("/", os.sep)))
            else:
                panel.clear(f"Not in folder {name}")

    def _report(self):
        if not self.result:
            return
        s, counts = self.result["stats"], self.result["stats"]["counts"]
        order = ("same", "diff", "moved", "a_only", "b_only")
        rep = R.Report("Folder comparison", f"A: {s['a']}   ↔   B: {s['b']}")
        rep.tiles([(STATUS[k][0], fmt_int(counts.get(k, 0))) for k in order] + [("Similarity", f"{s['similarity']:.0f}%")])
        mode_text = {m: t for m, t in MODES}[s["mode"]].lower()
        rep.text(f"A: {fmt_int(s['files_a'])} files, {fmt_size(s['bytes_a'])}.   B: {fmt_int(s['files_b'])} files, "
                 f"{fmt_size(s['bytes_b'])}.   Compared by {mode_text}{' (names only)' if s['by_name'] else ''}.")
        rep.heading("Overview")
        rep.svg(R.svg_donut([(STATUS[k][0], counts.get(k, 0), REPORT_COLORS[k]) for k in order],
                            fmt_int(s["total"]), "files compared"), 560)
        rep.heading("Where the differences are (by file type)")
        by_ext = sorted(s["by_ext"].items(), key=lambda kv: -sum(kv[1].values()))[:10]
        rep.svg(R.svg_bars([(("." + e) if e != "(none)" else e, [(c.get(k, 0), REPORT_COLORS[k], STATUS[k][0]) for k in
                                                                 ("diff", "a_only", "b_only", "moved", "same")])
                            for e, c in by_ext], legend=[(STATUS[k][0], REPORT_COLORS[k]) for k in order]), 640)
        rows = sorted((r for r in self.result["rows"] if r.status != "same"),
                      key=lambda r: (STATUS[r.status][2], r.rel.lower()))
        rep.heading("Differences")
        rep.table(["Status", "File", "Size in A", "Size in B"],
                  [[STATUS[r.status][0], r.rel, fmt_size(r.size_a) if r.size_a is not None else "–",
                    fmt_size(r.size_b) if r.size_b is not None else "–"] for r in rows], ["l", "l", "r", "r"],
                  note=("Identical files are not listed. " + scan_note(s["cloud"], s["ignored"])).strip())
        try:
            path = R.ask_and_save(self, rep, "comparison-report")
        except OSError as e:
            self.banner.show_message("danger", "Could not save the report", str(e))
            return
        if path:
            from .util import open_file
            self.banner.show_message("success", "Report saved", path, [("Open", lambda: open_file(path))])

    def _export(self):
        if not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export comparison", "comparison.csv", "CSV (*.csv)")
        if not path:
            return
        s = self.result["stats"]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Status", "Path in A", "Path in B", "Size in A", "Size in B", "Modified in A", "Modified in B"])
                for r in self.table.visible_rows():
                    w.writerow([STATUS[r.status][0], r.rel_a, r.rel_b, r.size_a if r.size_a is not None else "",
                                r.size_b if r.size_b is not None else "", fmt_time(r.m_a), fmt_time(r.m_b)])
        except OSError as e:
            self.banner.show_message("danger", "Could not save the file", str(e))
            return
        from .util import open_file
        self.banner.show_message("success", "Exported", f"{path}  (A = {s['a']},  B = {s['b']})",
                                 [("Open", lambda: open_file(path))])
