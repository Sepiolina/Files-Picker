"""
Minimal line icons (24×24, 1.75px stroke - Lucide style), rendered from SVG
in any colour and at the screen's pixel density, so they stay crisp on
high-DPI displays and follow the light/dark theme.

    icon("folder")                      -> QIcon in the theme's muted colour
    icon("download", theme.current["on_accent"])
"""

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

_PATHS = {
    # window controls
    "minimize": '<path d="M5 12h14"/>',
    "maximize": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "restore": '<rect x="5" y="8" width="11" height="11" rx="2"/><path d="M8 8V7a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-1"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    # app
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4"/>',
    "upload": '<path d="M12 15V4M7 9l5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "play": '<path d="M7 5l12 7-12 7z"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M7 7l10 10M17 7L7 17"/>',
    "alert": '<path d="M12 8v5M12 16.5h.01"/><circle cx="12" cy="12" r="9"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.5h.01"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.5 4.5L20 16M20 20v-4h-4"/>',
    "settings2": '<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="4"/>',
    "compare": '<path d="M8 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3M16 3h3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-3M12 2v20"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.5"/>',
    "drive": '<path d="M3 14h18v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M5.5 5h13L21 14H3z"/><path d="M7 17h.01"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    "swap": '<path d="M7 4L3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7"/>',
    "scan": '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M4 12h16"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>',
    "grid": '<rect x="3" y="3" width="8" height="10" rx="1.5"/><rect x="14" y="3" width="7" height="6" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="8" height="5" rx="1.5"/>',
    "file-text": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    "sparkle": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
}

_cache = {}


def svg(name, color, stroke=1.75):
    body = _PATHS[name]
    fill = color if name == "play" else "none"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


def pixmap(name, color=None, size=16, stroke=1.75):
    """A crisp pixmap at the current device pixel ratio."""
    color = color or theme.current["muted"]
    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    key = (name, color, size, stroke, dpr)
    if key not in _cache:
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.fill(Qt.transparent)
        renderer = QSvgRenderer(QByteArray(svg(name, color, stroke).encode()))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, size * dpr, size * dpr))
        p.end()
        pm.setDevicePixelRatio(dpr)
        _cache[key] = pm
    return _cache[key]


def icon(name, color=None, size=16, stroke=1.75):
    return QIcon(pixmap(name, color, size, stroke))


def icon_size(size=16):
    return QSize(size, size)


# ---- theme-aware binding: icons re-colour themselves when the theme changes ----

import weakref  # noqa: E402

from PySide6.QtWidgets import QLabel  # noqa: E402

_bound = weakref.WeakKeyDictionary()


def bind(widget, name, color="muted", size=16, stroke=1.75):
    """Give a button / action / label an icon that follows the theme.
    color is a theme key (e.g. "muted", "on_accent") or a literal "#RRGGBB"."""
    _bound[widget] = (name, color, size, stroke)
    _apply(widget, name, color, size, stroke)


def _apply(w, name, color, size, stroke):
    c = theme.current.get(color, color)
    if isinstance(w, QLabel):
        w.setPixmap(pixmap(name, c, size, stroke))
    else:
        w.setIcon(icon(name, c, size, stroke))
        if hasattr(w, "setIconSize"):
            w.setIconSize(QSize(size, size))


def refresh_all():
    for w, args in list(_bound.items()):
        try:
            _apply(w, *args)
        except RuntimeError:  # the Qt object was already deleted
            pass
