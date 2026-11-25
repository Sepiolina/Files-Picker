"""
Left side of the Run page: the four steps that make up a job.

    1 Search in     2 Names to find     3 Matching     4 Output

Design notes
  * Steps 3 and 4 are collapsed to a row of chips by default (progressive
    disclosure): the defaults are right for most runs, and the chips still show
    what will happen. Click the chips or "Customize" to change them.
  * A step's number turns into ✓ when it is complete, so the user can see at a
    glance what is still missing.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QLineEdit, QMessageBox,
                               QToolButton, QVBoxLayout, QWidget)

from . import options as O
from .engine import EngineProcess
from .util import fmt_int, guess_separator, read_text_file
from .widgets import Card, Chip, ListEditor, PathField, Segmented, TokenRow, button, debounce, hbox, label


def combo(choices):
    """QComboBox whose items carry engine values (and optional tooltips)."""
    c = QComboBox()
    for opt in choices:
        c.addItem(opt[1], opt[0])
        if len(opt) > 2:
            c.setItemData(c.count() - 1, opt[2], Qt.ToolTipRole)
    return c


def set_combo(c, value):
    i = c.findData(value)
    if i >= 0:
        c.setCurrentIndex(i)


def disclosure(text="Customize"):
    b = QToolButton()
    b.setObjectName("Link")
    b.setText(text)
    b.setCheckable(True)
    b.setCursor(Qt.PointingHandCursor)
    return b


def form_layout(widget):
    widget.setObjectName("FormGrid")  # lets the stylesheet centre labels on 36px controls
    form = QFormLayout(widget)
    form.setContentsMargins(0, 2, 0, 0)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(8)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return form


class FormPanel(QWidget):
    changed = Signal()          # any setting changed (autosave, action bar)
    stats_changed = Signal()    # name-list counts updated

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FormPanel")
        self._loading = False
        self.unique_count = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 14, 16)
        lay.setSpacing(12)
        lay.addWidget(self._build_source())
        lay.addWidget(self._build_names(), 1)
        lay.addWidget(self._build_matching())
        lay.addWidget(self._build_output())

        # Live "entries · unique · duplicates" counter, computed by the engine
        # so the GUI and the engine can never disagree on how a list is parsed.
        self._inspector = EngineProcess(self)
        self._inspector.event.connect(self._on_inspect)
        self._inspect_timer = debounce(self, 350, self._inspect)
        self._list_dirty = True

    # ---- 1. source ----------------------------------------------------------------

    def _build_source(self):
        self.source_card = card = Card(1, "Search in")
        self.source = PathField(PathField.FOLDER, "Choose a folder, paste a path, or drop one here")
        self.recursive = QCheckBox("Include sub-folders")
        card.body.addWidget(self.source)
        card.body.addWidget(self.recursive)
        self.source.changed.connect(self._emit)
        self.recursive.toggled.connect(self._emit)
        return card

    # ---- 2. names -----------------------------------------------------------------

    def _build_names(self):
        self.names_card = card = Card(2, "Names to find")
        self.list_stats = Chip("Empty")
        card.add_to_header(self.list_stats)

        self.list_edit = ListEditor()
        self.list_edit.setPlaceholderText("Paste names, one per line…\n\nABC\nHWUDS\nJIKWLQ\n\n"
                                          "Or drop a .txt / .csv file here.")
        self.list_edit.setLineWrapMode(ListEditor.NoWrap)
        self.list_edit.setMinimumHeight(120)
        self.list_edit.fileDropped.connect(self.import_list)
        self.list_edit.textChanged.connect(self._on_list_text)

        imp = button("Import file", "upload", "Small", tooltip="Load names from a .txt or .csv file")
        imp.clicked.connect(lambda: self.import_list())
        self.separator = combo(O.SEPARATORS)
        self.custom_sep = QLineEdit()
        self.custom_sep.setPlaceholderText("e.g. ::")
        self.custom_sep.setFixedWidth(70)

        # CSV options only appear for tabular separators.
        self.header = QCheckBox("Header row")
        self.column = QLineEdit()
        self.column.setPlaceholderText("All columns")
        self.column.setToolTip("Column number (1, 2, …) or header name. Empty = every column.")
        self.column.setFixedWidth(110)
        self.csv_quotes = QCheckBox("Quoted values")
        self.csv_quotes.setToolTip('Treat "a, b" as one value')
        self.csv_row = QWidget()
        self.csv_row.setLayout(hbox(label("Column", "FieldLabel"), self.column, 8, self.header, 8, self.csv_quotes, None))

        card.body.addWidget(self.list_edit, 1)
        card.body.addLayout(hbox(imp, None, label("Split by", "FieldLabel"), self.separator, self.custom_sep))
        card.body.addWidget(self.csv_row)

        self.separator.currentIndexChanged.connect(self._on_list_option)
        for w in (self.custom_sep, self.column):
            w.textChanged.connect(self._on_list_option)
        for w in (self.header, self.csv_quotes):
            w.toggled.connect(self._on_list_option)
        return card

    def import_list(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Import names", str(Path.home()),
                                                  "Text or CSV (*.txt *.csv *.tsv);;All files (*)")
        if not path:
            return
        try:
            text = read_text_file(path)
        except OSError as e:
            QMessageBox.warning(self, "Import", f"Could not read the file:\n{e}")
            return
        set_combo(self.separator, guess_separator(path, text))
        self.list_edit.setPlainText(text)

    def _on_list_text(self):
        self._on_list_option()

    def _on_list_option(self, *_):
        self._list_dirty = True
        self._inspect_timer.start()
        self._emit()

    def _on_inspect(self, ev):
        if ev["type"] == "list":
            n = ev["entries"]
            self.unique_count = ev["unique"]
            parts = f"{fmt_int(n)} {'name' if n == 1 else 'names'}"
            if ev["duplicated"]:
                groups = "\n".join("  " + " = ".join(g) for g in ev["duplicateGroups"][:25])
                d = ev["duplicated"]
                self.list_stats.set(f"{parts} · {fmt_int(d)} {'duplicate' if d == 1 else 'duplicates'}", "warn",
                                    f"{fmt_int(ev['unique'])} unique. Duplicates are merged into one:\n{groups}")
            elif n:
                self.list_stats.set(parts, None, "No duplicates")
            else:
                self.list_stats.set("Empty")
            self.names_card.set_done(self.unique_count > 0)
            self.stats_changed.emit()
        elif ev["type"] == "error" and "duplicate" not in ev["message"]:
            self.unique_count = 0
            self.list_stats.set("Can't read list", "danger", ev["message"])
            self.names_card.set_done(False)
            self.stats_changed.emit()

    def _inspect(self):
        try:
            self._inspector.start("inspect", {"list": self.list_settings(), "match": self.match_settings()})
        except FileNotFoundError:
            self.list_stats.set("Engine missing", "danger", "Run build.sh / build.ps1 once.")

    # ---- 3. matching ----------------------------------------------------------------

    def _build_matching(self):
        self.match_card = card = Card(3, "Matching")
        self.match_toggle = disclosure()
        card.add_to_header(self.match_toggle)
        self.match_tokens = TokenRow()
        self.match_tokens.clicked.connect(lambda: self.match_toggle.setChecked(True))
        card.body.addWidget(self.match_tokens)

        self.mode = combo(O.MATCH_MODES)
        self.mode_example = label("", "Code")
        self.target = combo(O.TARGETS)
        self.case = QCheckBox("Case-sensitive  (ABC ≠ abc)")
        self.duplicates = combo(O.DUPLICATES)
        self.overlap = combo(O.OVERLAP)
        self.overlap.setToolTip("When a file matches several names (e.g. ABC and ABCDE).\n"
                                "Decides its sub-folder when “Folder per name” is on.")
        self.extensions = QLineEdit()
        self.extensions.setPlaceholderText("All types — or e.g.  pdf, jpg")

        self.match_details = QWidget()
        form = form_layout(self.match_details)
        form.addRow(label("Match", "FieldLabel"), self.mode)
        form.addRow("", self.mode_example)
        form.addRow(label("Compare with", "FieldLabel"), self.target)
        form.addRow("", self.case)
        form.addRow(label("File types", "FieldLabel"), self.extensions)
        form.addRow(label("Duplicates", "FieldLabel"), self.duplicates)
        form.addRow(label("Several match", "FieldLabel"), self.overlap)
        card.body.addWidget(self.match_details)
        self.match_details.hide()
        self.match_toggle.toggled.connect(
            lambda on: self._toggle(on, self.match_toggle, self.match_tokens, self.match_details))

        for w in (self.mode, self.target, self.overlap):
            w.currentIndexChanged.connect(self._emit)
        self.duplicates.currentIndexChanged.connect(self._on_list_option)  # changes the counts
        self.case.toggled.connect(self._on_list_option)
        self.extensions.textChanged.connect(self._emit)
        return card

    @staticmethod
    def _toggle(open_, button, tokens, details):
        details.setVisible(open_)
        tokens.setVisible(not open_)
        button.setText("Done" if open_ else "Customize")

    # ---- 4. output ------------------------------------------------------------------

    def _build_output(self):
        self.output_card = card = Card(4, "Output")
        self.out_toggle = disclosure()
        card.add_to_header(self.out_toggle)

        self.out_type = Segmented(O.OUTPUT_TYPES)
        self.out_path = PathField(PathField.SAVE_FILE, "Where to save…")
        self.out_tokens = TokenRow()
        self.out_tokens.clicked.connect(lambda: self.out_toggle.setChecked(True))

        self.compression = combo(O.COMPRESSION)
        self.compression.setToolTip("Photos, video and PDFs are already compressed —\n"
                                    "“Fastest” is usually the right choice.")
        self.conflict = combo(O.CONFLICT)
        self.group = QCheckBox("One folder per name")
        self.group.setToolTip("ABC/…, HWUDS/… — each file goes under the name it matched,\n"
                              "keeping its original sub-folders inside.")
        self.report = QCheckBox("Write CSV report")
        self.report.setToolTip("…_files.csv and …_names.csv next to the output.\n"
                               "Names that found nothing are marked NO.")

        self.out_details = QWidget()
        form = form_layout(self.out_details)
        self.compression_label = label("Compression", "FieldLabel")
        self.conflict_label = label("If a file exists", "FieldLabel")
        form.addRow(self.compression_label, self.compression)
        form.addRow(self.conflict_label, self.conflict)
        form.addRow(label("Structure", "FieldLabel"), self.group)
        form.addRow(label("Report", "FieldLabel"), self.report)
        self.out_details.hide()
        self.out_toggle.toggled.connect(
            lambda on: self._toggle(on, self.out_toggle, self.out_tokens, self.out_details))

        card.body.addWidget(self.out_type)
        card.body.addWidget(self.out_path)
        card.body.addWidget(self.out_tokens)
        card.body.addWidget(self.out_details)

        self.out_type.changed.connect(self._on_out_type)
        self.out_path.changed.connect(self._emit)
        for w in (self.compression, self.conflict):
            w.currentIndexChanged.connect(self._emit)
        for w in (self.group, self.report):
            w.toggled.connect(self._emit)
        return card

    def _on_out_type(self, t):
        # Keep the path but switch its extension: out.zip -> out.tar.gz
        path = self.out_path.text()
        if path and t != "folder":
            stem = path
            for ext in (".tar.gz", ".zip", ".tar"):
                if stem.lower().endswith(ext):
                    stem = stem[: -len(ext)]
            self.out_path.setText(stem + O.ARCHIVE_EXT[t])
        self._emit()

    # ---- job <-> widgets --------------------------------------------------------------

    def list_settings(self):
        return {"text": self.list_edit.toPlainText(), "file": "",
                "separator": self.separator.currentData(), "custom": self.custom_sep.text(),
                "csvQuotes": self.csv_quotes.isChecked(), "header": self.header.isChecked(),
                "column": self.column.text().strip(), "trim": True}

    def match_settings(self):
        raw = self.extensions.text().replace(";", ",").replace(" ", ",")
        return {"mode": self.mode.currentData(), "target": self.target.currentData(),
                "caseSensitive": self.case.isChecked(), "duplicates": self.duplicates.currentData(),
                "overlap": self.overlap.currentData(), "extensions": [e for e in raw.split(",") if e.strip()]}

    def output_settings(self):
        return {"type": self.out_type.value(), "path": self.out_path.text(),
                "groupByName": self.group.isChecked(), "report": self.report.isChecked(),
                "compression": self.compression.currentData(), "conflict": self.conflict.currentData()}

    def job(self):
        return {"source": self.source.text(), "recursive": self.recursive.isChecked(),
                "list": self.list_settings(), "match": self.match_settings(),
                "output": self.output_settings(), "previewLimit": 5000}

    def set_job(self, job):
        job = O.merged_job(job)
        self._loading = True
        try:
            self.source.setText(job["source"])
            self.recursive.setChecked(job["recursive"])
            L, M, U = job["list"], job["match"], job["output"]
            if self.list_edit.toPlainText() != L["text"]:
                self.list_edit.setPlainText(L["text"])
            set_combo(self.separator, L["separator"])
            self.custom_sep.setText(L["custom"])
            self.csv_quotes.setChecked(L["csvQuotes"])
            self.header.setChecked(L["header"])
            self.column.setText(L["column"])
            set_combo(self.mode, M["mode"])
            set_combo(self.target, M["target"])
            self.case.setChecked(M["caseSensitive"])
            set_combo(self.duplicates, M["duplicates"])
            set_combo(self.overlap, M["overlap"])
            self.extensions.setText(", ".join(M["extensions"]))
            self.out_type.setValue(U["type"])
            self.out_path.setText(U["path"])
            self.group.setChecked(U["groupByName"])
            self.report.setChecked(U["report"])
            set_combo(self.compression, U["compression"])
            set_combo(self.conflict, U["conflict"])
        finally:
            self._loading = False
        self._inspect_timer.start()
        self._emit()

    def _emit(self, *_):
        self._sync()
        if not self._loading:
            self.changed.emit()

    def _sync(self):
        """Keep dependent widgets, summaries and step checkmarks in line with the settings."""
        sep = self.separator.currentData()
        self.custom_sep.setVisible(sep == "custom")
        self.csv_row.setVisible(sep in O.TABULAR_SEPARATORS)

        m, o = self.match_settings(), self.output_settings()
        self.match_tokens.set_tokens(O.matching_tokens(m))
        self.mode_example.setText(next(x[2] for x in O.MATCH_MODES if x[0] == m["mode"]))
        self.out_tokens.set_tokens(O.output_tokens(o))

        t = o["type"]
        for w in (self.compression, self.compression_label):
            w.setVisible(t == "zip")
        for w in (self.conflict, self.conflict_label):
            w.setVisible(t == "folder")
        if t == "folder":
            self.out_path.mode = PathField.FOLDER
            self.out_path.edit.setPlaceholderText("Folder to copy the files into…")
        else:
            ext = O.ARCHIVE_EXT[t]
            self.out_path.mode = PathField.SAVE_FILE
            self.out_path.file_filter = f"{O.label(O.OUTPUT_TYPES, t)} (*{ext})"
            self.out_path.default_name = "matched_files" + ext
            self.out_path.edit.setPlaceholderText(f"Where to save the {ext} file…")
        self.out_path.refresh()

        self.source_card.set_done(bool(self.source.text()) and Path(self.source.text()).is_dir())
        self.output_card.set_done(bool(o["path"]))
        self.match_card.set_done(True)  # always valid: the defaults work

    def has_source(self):
        return bool(self.source.text()) and Path(self.source.text()).is_dir()

    def stop_background_work(self):
        self._inspect_timer.stop()
        self._inspector.kill()

    def set_enabled(self, enabled):
        for card in (self.source_card, self.names_card, self.match_card, self.output_card):
            card.setEnabled(enabled)
