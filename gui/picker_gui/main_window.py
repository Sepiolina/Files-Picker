"""
Main window: a frameless window whose custom title bar also holds the app's
navigation, so there is a single, seamless bar at the top:

    [F] File Picker │ [▶ Run] [⟲ History 12]            [☀]  [—][▢][✕]

Settings are saved automatically (debounced) and on close; the window opens
exactly as it was left, including the name list and maximized state.
"""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QActionGroup, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QMenu, QMessageBox, QStackedWidget, QToolButton, QVBoxLayout

from . import fscore, icons, theme
from .compare_page import ComparePage
from .duplicates_page import DuplicatesPage
from .engine import app_dir
from .find_page import FindPage
from .history_page import HistoryPage
from .run_page import RunPage
from .settings_dialog import ScanSettingsDialog, apply_scan_settings
from .snapshot_page import SnapshotPage
from .space_page import SpacePage
from .widgets import NavTabs, debounce, set_prop
from .window_chrome import CustomTitleBar, FramelessWindow

PAGES = [("run", "Run", "play"), ("compare", "Compare", "compare"), ("snapshots", "Snapshots", "camera"),
         ("duplicates", "Duplicates", "layers"), ("space", "Space", "grid"), ("find", "Find", "search"),
         ("history", "History", "history")]
SCAN_PAGES = ("compare_page", "snapshot_page", "duplicates_page", "space_page", "find_page")
THEMES = [("system", "Match system", "monitor"), ("light", "Light", "sun"), ("dark", "Dark", "moon")]


class MainWindow(FramelessWindow):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.setWindowTitle("File Picker")
        self.resize(1280, 820)
        self.setMinimumSize(1040, 680)

        settings = store.load_settings()
        self.ui = settings["ui"]

        # Pages
        self.run_page = RunPage()
        self.history_page = HistoryPage(store)
        self.compare_page = ComparePage()
        self.snapshot_page = SnapshotPage(store.root)
        self.find_page = FindPage(store.root)
        self.duplicates_page = DuplicatesPage()
        self.space_page = SpacePage()
        self.page_widgets = {"run": self.run_page, "compare": self.compare_page, "snapshots": self.snapshot_page,
                             "duplicates": self.duplicates_page, "space": self.space_page, "find": self.find_page,
                             "history": self.history_page}
        self.pages = QStackedWidget()
        for key, _, _ in PAGES:
            self.pages.addWidget(self.page_widgets[key])

        # Title bar = app identity + navigation + appearance + window controls
        self.title_bar = CustomTitleBar(self, "File Picker")
        self.title_bar.set_icon(QPixmap(str(app_dir() / "assets" / "icon.png")))
        self.nav = NavTabs(PAGES)
        self.nav.changed.connect(self.show_page)
        self.title_bar.center.addWidget(self.nav)
        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("IconButton")
        self.theme_btn.setToolTip("Appearance")
        self.theme_btn.setPopupMode(QToolButton.InstantPopup)
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setFocusPolicy(Qt.NoFocus)
        menu = QMenu(self.theme_btn)
        group = QActionGroup(menu)
        self.theme_actions = {}
        for value, text, icon_name in THEMES:
            a = menu.addAction(text, lambda v=value: self.set_theme(v))
            a.setCheckable(True)
            group.addAction(a)
            self.theme_actions[value] = a
        self.theme_btn.setMenu(menu)
        self.scan_btn = QToolButton()
        self.scan_btn.setObjectName("IconButton")
        self.scan_btn.setToolTip("Scan options: ignore rules, cloud files, engine")
        self.scan_btn.setCursor(Qt.PointingHandCursor)
        self.scan_btn.setFocusPolicy(Qt.NoFocus)
        self.scan_btn.clicked.connect(self.open_scan_settings)
        self.title_bar.right.addWidget(self.scan_btn)
        self.title_bar.right.addWidget(self.theme_btn)
        self.set_title_bar(self.title_bar)

        # Frame: 1px border on macOS/Linux (Windows draws its own border + shadow)
        self.frame = QFrame()
        self.frame.setObjectName("WindowFrame" if not self._is_windows_chrome() else "Root")
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.title_bar)
        lay.addWidget(self.pages, 1)
        self.setCentralWidget(self.frame)
        self.maximized_changed.connect(lambda m: set_prop(self.frame, "maximized", m))

        # Wiring
        self.run_page.run_recorded.connect(self._record_run)
        self.history_page.load_requested.connect(self._load_job)
        self.history_page.rerun_requested.connect(self._rerun_job)
        self.history_page.count_changed.connect(self._update_history_count)
        self._save_timer = debounce(self, 800, self.save_settings)
        self.run_page.settings_changed.connect(self._save_timer.start)

        for n, (key, text, _) in enumerate(PAGES, 1):
            QShortcut(QKeySequence(f"Ctrl+{n}"), self, lambda k=key: self.show_page(k))
            self.nav.tabs[key].setToolTip(f"{text}  (Ctrl+{n})")
        for name in SCAN_PAGES:
            page = getattr(self, name)
            page.changed.connect(self._save_timer.start)
            page.settings_requested.connect(self.open_scan_settings)
        self.snapshot_page.count_changed.connect(lambda n: self.nav.tabs["snapshots"].set_badge(n))

        # Restore last session
        self.run_page.set_job(settings["job"])
        apply_scan_settings(self.ui.get("scan", {}))
        self.compare_page.restore(self.ui.get("compare", {}))
        self.duplicates_page.restore(self.ui.get("duplicates", {}))
        self.space_page.restore(self.ui.get("space", {}))
        self.snapshot_page.restore(self.ui.get("snapshots", {}))
        self.find_page.restore(self.ui.get("find", {}))
        self.nav.tabs["snapshots"].set_badge(len(self.snapshot_page.snaps.list()))
        if self.ui.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(self.ui["geometry"].encode()))
        if self.ui.get("splitter"):
            self.run_page.splitter.restoreState(QByteArray.fromBase64(self.ui["splitter"].encode()))
        self._sync_theme_ui(self.ui.get("theme", "system"))
        self._update_history_count(len(store.history()))
        self.show_page(self.ui.get("page", "run"))

        # Follow the OS when the theme is "system"
        try:
            QApplication.styleHints().colorSchemeChanged.connect(
                lambda _: self.ui.get("theme", "system") == "system" and self.set_theme("system"))
        except AttributeError:
            pass

    @staticmethod
    def _is_windows_chrome():
        from .window_chrome import IS_WINDOWS, USE_NATIVE_FRAME
        return IS_WINDOWS or USE_NATIVE_FRAME

    # ---- navigation & theme ---------------------------------------------------------

    def show_page(self, page):
        page = page if page in self.page_widgets else "run"
        self.nav.setValue(page)
        self.pages.setCurrentWidget(self.page_widgets[page])
        self.ui["page"] = page
        if page == "history":
            self.history_page.refresh()

    def open_scan_settings(self):
        dialog = ScanSettingsDialog(self)
        if dialog.exec():
            apply_scan_settings(dialog.values())
            self.save_settings()

    def _update_history_count(self, n):
        self.nav.tabs["history"].set_badge(n)

    def set_theme(self, mode):
        self.ui["theme"] = mode
        theme.apply(QApplication.instance(), mode)
        self._sync_theme_ui(mode)
        self.save_settings()

    def _sync_theme_ui(self, mode):
        self.theme_actions.get(mode, self.theme_actions["system"]).setChecked(True)
        icon_name = {"system": "monitor", "light": "sun", "dark": "moon"}.get(mode, "monitor")
        icons.bind(self.theme_btn, icon_name, "muted", size=16)
        icons.bind(self.scan_btn, "sliders", "muted", size=16)
        for value, _, name in THEMES:
            icons.bind(self.theme_actions[value], name, "muted", size=16)

    # ---- history ----------------------------------------------------------------------

    def _record_run(self, job, status, summary):
        self.store.add_run(job, status, summary)
        self._update_history_count(len(self.store.history()))

    def _load_job(self, job):
        self.run_page.set_job(job)
        self.show_page("run")

    def _rerun_job(self, job):
        self._load_job(job)
        self.run_page.start("export")

    # ---- persistence --------------------------------------------------------------------

    def save_settings(self):
        self.ui["compare"] = self.compare_page.state()
        self.ui["snapshots"] = self.snapshot_page.state()
        self.ui["find"] = self.find_page.state()
        self.ui["duplicates"] = self.duplicates_page.state()
        self.ui["space"] = self.space_page.state()
        self.ui["scan"] = {"ignore": fscore.config.ignore, "skip_cloud": fscore.config.skip_cloud,
                           "use_engine": fscore.config.use_engine}
        self.ui["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.ui["splitter"] = bytes(self.run_page.splitter.saveState().toBase64()).decode()
        self.store.save_settings(self.run_page.job(), self.ui)

    def closeEvent(self, e):
        if self.run_page.is_running():
            answer = QMessageBox.question(self, "Stop the export?",
                                          "An export is still running. Stop it and quit?")
            if answer != QMessageBox.Yes:
                e.ignore()
                return
            self.run_page.engine.cancel()
            self.run_page.engine.kill()
        self.run_page.form.stop_background_work()
        for name in SCAN_PAGES:
            getattr(self, name).stop_background_work()
        self.save_settings()
        super().closeEvent(e)
