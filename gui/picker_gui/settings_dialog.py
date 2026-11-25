"""Scan options shared by every tab: ignore rules, cloud placeholders, and the fast engine."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QPlainTextEdit, QSizePolicy,
                               QVBoxLayout)

from . import fscore
from .widgets import button, hbox, label

QUICK_RULES = [".git/", "node_modules/", "venv/", "build/", "dist/", "*.log", "*.bak", "~$*"]


class ScanSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scan options")
        self.setMinimumWidth(680)
        cfg = fscore.config

        self.rules = QPlainTextEdit(cfg.ignore)
        self.rules.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.rules.setMinimumHeight(150)
        self.rules.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.rules.setPlaceholderText("node_modules/\n*.tmp")
        self.count = label("", "Hint")
        self.rules.textChanged.connect(self._count)

        quick = QGridLayout()
        quick.setHorizontalSpacing(6)
        quick.setVerticalSpacing(6)
        for i, rule in enumerate(QUICK_RULES):
            b = button(rule, kind="Small")
            b.clicked.connect(lambda _=False, r=rule: self._add(r))
            quick.addWidget(b, i // 4, i % 4)
        reset = button("Reset to defaults", kind="Small")
        reset.clicked.connect(lambda: self.rules.setPlainText(fscore.DEFAULT_IGNORE))
        quick.addWidget(reset, 0, 4, 2, 1, Qt.AlignTop)
        quick.setColumnStretch(5, 1)

        self.cloud = QCheckBox("Skip online-only cloud files (OneDrive, iCloud, …)")
        self.cloud.setChecked(cfg.skip_cloud)
        self.cloud.setToolTip("Placeholders for files that are not stored on this computer. They are never opened,\n"
                              "so nothing gets downloaded.")
        self.use_engine = QCheckBox("Use the fast Go engine for scanning and checksums when available")
        self.use_engine.setChecked(cfg.use_engine)
        ok, version, path = fscore.engine_status(refresh=True)
        if ok:
            status = f"Engine {version} found: {path}"
        elif path:
            status = f"Engine found but too old ({version}) — rebuild it with build.sh. The built-in scanner is used."
        else:
            status = "Engine not found — the built-in Python scanner is used (build it with build.sh for the fast one)."
        self.engine_status = label(status, "Hint", wrap=True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Ignore rules", "H2"))
        lay.addWidget(label("Files and folders matching these rules are skipped by Compare, Snapshots, Duplicates, "
                            "Space and Find. One rule per line:  name/  (folders only)   *.tmp   /top-level   "
                            "docs/drafts   **/cache/**   # comment", "Muted", wrap=True))
        lay.addWidget(self.rules)
        lay.addLayout(quick)
        lay.addSpacing(2)
        lay.addWidget(self.count)
        lay.addSpacing(6)
        lay.addWidget(label("Cloud files", "H2"))
        lay.addWidget(self.cloud)
        lay.addSpacing(6)
        lay.addWidget(label("Engine", "H2"))
        lay.addWidget(self.use_engine)
        lay.addWidget(self.engine_status)
        lay.addSpacing(8)
        lay.addWidget(buttons)
        self._count()
        self.resize(self.sizeHint())

    def _add(self, rule):
        lines = self.rules.toPlainText().splitlines()
        if rule not in [l.strip() for l in lines]:
            self.rules.setPlainText("\n".join(lines + [rule]).strip("\n"))

    def _count(self):
        n = len(fscore.IgnoreRules(self.rules.toPlainText()).rules)
        self.count.setText(f"{n} active rule{'s' if n != 1 else ''}. Changing rules makes the next scan re-read folders.")

    def values(self):
        return {"ignore": self.rules.toPlainText(), "skip_cloud": self.cloud.isChecked(),
                "use_engine": self.use_engine.isChecked()}


def apply_scan_settings(values):
    """Copy saved/edited values into the shared scan configuration."""
    cfg = fscore.config
    cfg.ignore = values.get("ignore", cfg.ignore)
    cfg.skip_cloud = bool(values.get("skip_cloud", cfg.skip_cloud))
    cfg.use_engine = bool(values.get("use_engine", cfg.use_engine))
