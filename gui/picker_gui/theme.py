"""
Design system: colour tokens (light + dark), metrics, typography and the
application stylesheet. Everything visual is defined here; widgets only pick
a style by object name (e.g. QPushButton#Primary) or read `current[...]`
when they paint themselves.

Principles (Linear / Raycast inspired)
  * one accent colour - only for the primary action, focus, selection, progress
  * quiet neutral surfaces, 1px hairline borders, very soft shadows on cards
  * 8px spacing grid; 8px radius on controls, 12px on cards
  * one control height (36px) so rows line up; 13px base text, never below 12px
"""

import hashlib
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

LIGHT = {
    # surfaces
    "bg": "#F8FAFC",          # workspace behind cards
    "surface": "#FFFFFF",     # cards, inputs, title bar
    "sidebar": "#F9FAFB",     # left configuration panel
    "surface2": "#F3F4F6",    # hover fills, chips, segmented track
    "border": "#E5E7EB",
    "border_strong": "#D1D5DB",
    "shadow": "#0F172A",      # used at very low alpha
    # text
    "text": "#111827", "muted": "#6B7280", "faint": "#9CA3AF",
    # accent
    "accent": "#4F46E5", "accent_hover": "#4338CA", "accent_pressed": "#3730A3",
    "on_accent": "#FFFFFF", "accent_soft": "#EEF2FF", "accent_ring": "#C7D2FE",
    # status
    "success": "#059669", "success_soft": "#ECFDF5",
    "warn": "#D97706", "warn_soft": "#FFFBEB",
    "danger": "#DC2626", "danger_soft": "#FEF2F2",
    # window chrome
    "close_hover": "#E81123", "close_pressed": "#C50F1F",
}
DARK = {
    "bg": "#0E0F11", "surface": "#16171A", "sidebar": "#121315", "surface2": "#1F2024",
    "border": "#26272C", "border_strong": "#34353B", "shadow": "#000000",
    "text": "#EDEDEF", "muted": "#8A8F98", "faint": "#5C616B",
    "accent": "#6E6AF6", "accent_hover": "#8582F8", "accent_pressed": "#5B57E8",
    "on_accent": "#FFFFFF", "accent_soft": "#1E1D3A", "accent_ring": "#3B3A7A",
    "success": "#34D399", "success_soft": "#0D2620",
    "warn": "#FBBF24", "warn_soft": "#2A2110",
    "danger": "#F87171", "danger_soft": "#2C1416",
    "close_hover": "#E81123", "close_pressed": "#C50F1F",
}

# Metrics (logical pixels - Qt scales them for high-DPI screens).
CONTROL_H = 36
SMALL_H = 28
RADIUS = 8
CARD_RADIUS = 12
TITLEBAR_H = 44

# Updated by apply(); custom-painted widgets read colours from here.
current = dict(LIGHT)
mode_is_dark = False


def system_is_dark():
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return QGuiApplication.palette().window().color().lightness() < 128


def qcolor(key, alpha=None):
    c = QColor(current[key])
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


def apply(app, mode="system"):
    """mode: system | light | dark. Re-styles the whole app instantly."""
    global mode_is_dark
    dark = system_is_dark() if mode == "system" else mode == "dark"
    mode_is_dark = dark
    t = DARK if dark else LIGHT
    current.clear()
    current.update(t)

    app.setStyle("Fusion")  # neutral base, identical on every OS; QSS does the rest
    font = QFont()
    font.setFamilies(_ui_fonts())
    font.setPixelSize(13)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)

    pal = QPalette()
    for role, key in [(QPalette.Window, "bg"), (QPalette.Base, "surface"), (QPalette.AlternateBase, "surface2"),
                      (QPalette.Text, "text"), (QPalette.WindowText, "text"), (QPalette.ButtonText, "text"),
                      (QPalette.Button, "surface"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "on_accent"), (QPalette.PlaceholderText, "faint"),
                      (QPalette.ToolTipBase, "surface"), (QPalette.ToolTipText, "text"), (QPalette.Mid, "border")]:
        pal.setColor(role, QColor(t[key]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t, _svg_files(t)))
    from . import icons  # late import: icons depends on this module
    icons.refresh_all()
    for w in app.topLevelWidgets():  # custom-painted widgets pick up the new colours
        w.update()


def _ui_fonts():
    if sys.platform == "win32":
        return ["Segoe UI Variable Text", "Segoe UI"]
    if sys.platform == "darwin":
        return [".AppleSystemUIFont", "Helvetica Neue"]
    return ["Inter", "Cantarell", "Noto Sans", "DejaVu Sans"]


def _svg_files(t):
    """Small SVGs referenced from the stylesheet (checkbox tick, combo chevron)."""
    folder = Path(tempfile.gettempdir()) / "file_picker_icons"
    folder.mkdir(exist_ok=True)
    stroke = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    shapes = {
        "check": (f'<path d="M3.5 8.5l3 3 6-7" {stroke} stroke="{t["on_accent"]}" stroke-width="2"/>', 16),
        "check_accent": (f'<path d="M3.5 8.5l3 3 6-7" {stroke} stroke="{t["accent"]}" stroke-width="2"/>', 16),
        "chevron": (f'<path d="M4.5 6.5l3.5 3.5 3.5-3.5" {stroke} stroke="{t["muted"]}" stroke-width="1.6"/>', 16),
        "dash": (f'<path d="M4.5 8h7" {stroke} stroke="{t["on_accent"]}" stroke-width="2"/>', 16),
    }
    paths = {}
    for name, (body, size) in shapes.items():
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{body}</svg>'
        p = folder / f"{name}_{hashlib.md5(svg.encode()).hexdigest()[:10]}.svg"
        if not p.exists():
            p.write_text(svg)
        paths[name] = p.as_posix()
    return paths


def stylesheet(t, icon):
    h, sh, r = CONTROL_H, SMALL_H, RADIUS
    mono = '"Cascadia Mono", "Cascadia Code", Consolas, Menlo, "DejaVu Sans Mono", monospace'
    return f"""
/* ============================ base ============================ */
QWidget {{ color: {t['text']}; }}
QMainWindow, #Root {{ background: {t['bg']}; }}
#WindowFrame {{ background: {t['bg']}; border: 1px solid {t['border_strong']}; }}
#WindowFrame[maximized="true"] {{ border: none; }}
#Page, #Workspace {{ background: {t['bg']}; }}
#Sidebar, #Scroll, #Scroll > QWidget > QWidget, #FormPanel {{ background: {t['sidebar']}; }}
QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']};
            padding: 6px 9px; border-radius: 6px; }}

/* ============================ title bar ============================ */
#TitleBar {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}
#AppTitle {{ font-size: 13px; font-weight: 600; color: {t['text']}; }}
#TitleDivider {{ background: {t['border']}; min-width: 1px; max-width: 1px; min-height: 18px; max-height: 18px; }}
#NavTabs QPushButton {{
    background: transparent; border: 1px solid transparent; border-radius: 7px;
    padding: 0 12px; min-height: 28px; max-height: 28px; color: {t['muted']}; font-weight: 500; }}
#NavTabs QPushButton:hover {{ background: {t['surface2']}; color: {t['text']}; }}
#NavTabs QPushButton:checked {{ background: {t['surface2']}; color: {t['text']}; font-weight: 600; }}
#NavTabs QPushButton:focus {{ border-color: {t['accent_ring']}; }}
#NavBadge {{ background: {t['border']}; color: {t['muted']}; border-radius: 8px; padding: 0 6px;
             font-size: 11px; font-weight: 600; min-height: 16px; max-height: 16px; }}

/* ============================ typography ============================ */
#H1 {{ font-size: 20px; font-weight: 600; }}
#H2 {{ font-size: 15px; font-weight: 600; }}
#SectionTitle {{ font-size: 13px; font-weight: 600; }}
#Muted {{ color: {t['muted']}; }}
#Hint  {{ color: {t['muted']}; font-size: 12px; }}
#Faint {{ color: {t['faint']}; font-size: 12px; }}
#Code  {{ color: {t['muted']}; font-family: {mono}; font-size: 12px; }}
#FieldLabel {{ color: {t['muted']}; font-size: 12px; font-weight: 500; }}
#FormGrid #FieldLabel {{ min-height: {h}px; max-height: {h}px; }}

/* ============================ cards & steps ============================ */
#Card, #StatTile, #Panel {{ background: transparent; border: none; }}  /* painted in code (soft shadow) */
#StepNumber {{ background: {t['surface2']}; color: {t['muted']}; border-radius: 11px; font-size: 11px; font-weight: 700;
               min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }}
#StepNumber[done="true"] {{ background: {t['success_soft']}; color: {t['success']}; }}
#StatValue {{ font-size: 22px; font-weight: 600; }}
#StatValue[tone="danger"] {{ color: {t['danger']}; }}
#StatCaption {{ color: {t['muted']}; font-size: 12px; font-weight: 500; }}
#Token {{ background: {t['surface2']}; color: {t['text']}; border: 1px solid {t['border']}; border-radius: 6px;
          padding: 2px 8px; font-size: 12px; }}
#Token:disabled {{ color: {t['faint']}; }}

/* ============================ inputs ============================ */
QLineEdit, QComboBox {{
    background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {r}px;
    padding: 0 10px; min-height: {h - 2}px; max-height: {h - 2}px;
    selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
QPlainTextEdit {{
    background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {r}px; padding: 8px 10px;
    font-family: {mono}; font-size: 12px; selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: {t['border_strong']}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on, QPlainTextEdit:focus {{ border-color: {t['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled {{ color: {t['faint']}; background: {t['surface2']}; }}
QLineEdit#Search {{ padding-left: 4px; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({icon['chevron']}); width: 16px; height: 16px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {r}px;
    padding: 4px; outline: none; selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 8px; border-radius: 6px; }}

QCheckBox {{ spacing: 8px; min-height: 22px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {t['border_strong']};
    background: {t['surface']}; }}
QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({icon['check']}); }}
QCheckBox::indicator:checked:hover {{ background: {t['accent_hover']}; }}
QCheckBox:disabled {{ color: {t['faint']}; }}
QCheckBox::indicator:disabled {{ background: {t['surface2']}; border-color: {t['border']}; }}
QCheckBox::indicator:checked:disabled {{ background: {t['faint']}; border-color: {t['faint']}; }}

/* ============================ buttons ============================ */
QPushButton {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {r}px;
    padding: 0 14px; min-height: {h - 2}px; max-height: {h - 2}px; font-weight: 500; color: {t['text']}; }}
QPushButton:hover {{ background: {t['surface2']}; border-color: {t['border_strong']}; }}
QPushButton:pressed {{ background: {t['border']}; }}
QPushButton:focus {{ border-color: {t['accent']}; }}
QPushButton:disabled {{ color: {t['faint']}; background: {t['surface']}; border-color: {t['border']}; }}
QPushButton#Primary {{ background: {t['accent']}; border: 1px solid {t['accent']}; color: {t['on_accent']}; font-weight: 600;
    padding: 0 18px; }}
QPushButton#Primary:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton#Primary:pressed {{ background: {t['accent_pressed']}; border-color: {t['accent_pressed']}; }}
QPushButton#Primary:focus {{ border-color: {t['accent_ring']}; }}
QPushButton#Primary:disabled {{ background: {t['surface2']}; border-color: {t['border']}; color: {t['faint']}; }}
QPushButton[large="true"] {{ min-height: 38px; max-height: 38px; padding: 0 18px; }}
QPushButton#Small {{ min-height: {sh - 2}px; max-height: {sh - 2}px; padding: 0 10px; font-size: 12px; border-radius: 7px; }}
QPushButton#Link, QToolButton#Link {{ background: transparent; border: none; color: {t['accent']}; padding: 0 4px;
    min-height: 22px; font-weight: 500; }}
QPushButton#Link:hover, QToolButton#Link:hover {{ color: {t['accent_hover']}; text-decoration: underline; }}
QPushButton#Danger {{ background: transparent; border: 1px solid transparent; color: {t['danger']}; }}
QPushButton#Danger:hover {{ background: {t['danger_soft']}; }}
QToolButton#Ghost, QToolButton#IconButton {{ background: transparent; border: 1px solid transparent; border-radius: 7px;
    padding: 0; min-width: 30px; min-height: 30px; max-height: 30px; color: {t['muted']}; }}
QToolButton#Ghost:hover, QToolButton#IconButton:hover {{ background: {t['surface2']}; }}
QToolButton#Ghost:pressed, QToolButton#IconButton:pressed {{ background: {t['border']}; }}
QToolButton#IconButton::menu-indicator {{ image: none; width: 0; }}

/* segmented control (output type) */
#Segmented {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 9px; }}
#Segmented QPushButton {{ background: transparent; border: 1px solid transparent; border-radius: 7px;
    padding: 0 14px; min-height: {sh}px; max-height: {sh}px; color: {t['muted']}; font-weight: 500; }}
#Segmented QPushButton:hover {{ color: {t['text']}; }}
#Segmented QPushButton:checked {{ background: {t['surface']}; color: {t['text']}; border-color: {t['border']}; font-weight: 600; }}
#Segmented QPushButton:disabled {{ color: {t['faint']}; }}

/* ============================ path field ============================ */
#PathCard {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {r}px; }}
#PathCard:hover {{ border-color: {t['border_strong']}; }}
#PathCard[tone="danger"] {{ border-color: {t['danger']}; background: {t['danger_soft']}; }}
#PathName {{ font-weight: 600; }}
#PathField[drag="true"] QLineEdit, #PathField[drag="true"] #PathCard,
QPlainTextEdit[drag="true"] {{ border: 1px dashed {t['accent']}; background: {t['accent_soft']}; }}

/* ============================ chips, banners, checklist ============================ */
#Chip {{ background: {t['surface2']}; color: {t['muted']}; border-radius: 10px; padding: 2px 9px; font-size: 12px; font-weight: 500; }}
#Chip[tone="warn"]   {{ background: {t['warn_soft']}; color: {t['warn']}; }}
#Chip[tone="danger"] {{ background: {t['danger_soft']}; color: {t['danger']}; }}
#Banner {{ border-radius: 10px; border: 1px solid {t['border']}; background: {t['surface']}; }}
#Banner[tone="success"] {{ background: {t['success_soft']}; border-color: {t['success_soft']}; }}
#Banner[tone="warn"]    {{ background: {t['warn_soft']}; border-color: {t['warn_soft']}; }}
#Banner[tone="danger"]  {{ background: {t['danger_soft']}; border-color: {t['danger_soft']}; }}
#BannerTitle {{ font-weight: 600; }}
#Banner[tone="success"] #BannerTitle {{ color: {t['success']}; }}
#Banner[tone="warn"]    #BannerTitle {{ color: {t['warn']}; }}
#Banner[tone="danger"]  #BannerTitle {{ color: {t['danger']}; }}
#BannerIcon {{ min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; border-radius: 11px;
    background: {t['accent_soft']}; color: {t['accent']}; font-weight: 700; font-size: 12px; }}
#Banner[tone="success"] #BannerIcon {{ background: {t['success']}; color: {t['surface']}; }}
#Banner[tone="warn"] #BannerIcon    {{ background: {t['warn']}; color: {t['surface']}; }}
#Banner[tone="danger"] #BannerIcon  {{ background: {t['danger']}; color: {t['surface']}; }}
#CheckDot {{ min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; border-radius: 13px;
    border: 1px solid {t['border']}; background: {t['surface']}; color: {t['muted']}; font-size: 12px; font-weight: 600; }}
#CheckDot[done="true"] {{ background: {t['success']}; border-color: {t['success']}; color: {t['surface']}; }}
#CheckTitle {{ font-weight: 600; }}
#CheckTitle[done="true"] {{ color: {t['muted']}; text-decoration: line-through; }}
#CheckRow {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
#CheckRow[done="true"] {{ background: {t['bg']}; }}
#HeroIcon {{ background: {t['accent_soft']}; border-radius: 16px; min-width: 56px; max-width: 56px;
             min-height: 56px; max-height: 56px; }}
#Kbd {{ background: {t['surface']}; border: 1px solid {t['border']}; border-bottom-width: 2px; border-radius: 5px;
        padding: 0 6px; font-size: 11px; color: {t['muted']}; font-weight: 600; }}

/* ============================ action bar & progress ============================ */
#ActionBar {{ background: {t['sidebar']}; border-top: 1px solid {t['border']}; }}
QProgressBar {{ background: {t['surface2']}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 2px; }}

/* ============================ tables & tabs ============================ */
QTableView {{ background: {t['surface']}; border: none; gridline-color: transparent;
    selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; outline: none; }}
QTableView::item {{ padding: 0 10px; border-bottom: 1px solid {t['surface2']}; }}
QTableView::item:hover {{ background: {t['bg']}; }}
QTableView::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
QTreeView {{ background: {t['surface']}; border: none; selection-background-color: {t['accent_soft']};
    selection-color: {t['text']}; outline: none; }}
QTreeView::item {{ min-height: 30px; padding: 0 6px; border-bottom: 1px solid {t['surface2']}; }}
QTreeView::item:hover {{ background: {t['bg']}; }}
QTreeView::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
QTreeView::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {t['border_strong']};
    background: {t['surface']}; }}
QTreeView::indicator:hover {{ border-color: {t['accent']}; }}
QTreeView::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({icon['check']}); }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {t['surface']}; color: {t['muted']}; border: none;
    border-bottom: 1px solid {t['border']}; padding: 0 10px; min-height: 34px; font-weight: 600; font-size: 12px; }}
QHeaderView::section:hover {{ color: {t['text']}; }}
QTableCornerButton::section {{ background: {t['surface']}; border: none; }}
QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; border: 1px solid transparent; border-radius: 7px;
    padding: 0 12px; min-height: 28px; margin-right: 4px; color: {t['muted']}; font-weight: 500; }}
QTabBar::tab:hover {{ background: {t['surface2']}; color: {t['text']}; }}
QTabBar::tab:selected {{ background: {t['surface2']}; color: {t['text']}; font-weight: 600; }}

/* ============================ scrollbars & menus ============================ */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px 2px 2px; }}
QScrollBar::handle {{ background: {t['border']}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {t['border_strong']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; width: 0; height: 0; }}
QSplitter::handle {{ background: {t['border']}; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t['surface2']}; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}
QMenu::indicator:checked {{ image: url({icon['check_accent']}); }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 6px; }}
QMessageBox {{ background: {t['surface']}; }}
"""
