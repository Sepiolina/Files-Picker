"""
Custom window chrome: a frameless main window with an integrated title bar.

    FramelessWindow   QMainWindow base class - removes the OS title bar but keeps
                      native behaviour (move, resize, snap, shadow, animations)
    CustomTitleBar    icon + title on the left, any widgets in the middle,
                      Minimize / Maximize-Restore / Close on the right
    TitleBarButton    one window control, painted in code (crisp at any DPI)

How native behaviour is kept
----------------------------
Windows 10 / 11 (the important case)
  Qt.FramelessWindowHint alone would also drop the native frame styles - no Aero
  Snap, no Snap Layouts, no shadow, no minimize/maximize animations. So we:
    1. add WS_THICKFRAME | WS_CAPTION | ... back to the window style,
    2. answer WM_NCCALCSIZE with "the client area is the whole window"
       (so no native title bar is drawn) - when maximized, clamp it to the
       monitor's work area so nothing hangs off-screen or under the taskbar,
    3. answer WM_NCHITTEST: edges/corners -> resize, empty title bar -> HTCAPTION
       (Windows then does dragging, double-click maximize, snap, the system menu),
       the maximize button -> HTMAXBUTTON (shows the Windows 11 Snap Layouts flyout),
    4. ask DWM for the drop shadow and (Windows 11) rounded corners.
  The window controls still look and behave like ours: hover/press for the
  maximize button is driven from the WM_NC* messages Windows sends for it.

macOS / Linux
  Qt.FramelessWindowHint + QWindow.startSystemMove()/startSystemResize(), which
  hand the drag to the window manager. Thin invisible grips on the edges and
  corners provide resizing; a manual fallback is used where the platform has
  no system resize (macOS).

Escape hatch: set FILEPICKER_NATIVE_FRAME=1 to use the normal OS title bar.
"""

import os
import sys

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QWidget)

from . import icons, theme

IS_WINDOWS = sys.platform == "win32"
USE_NATIVE_FRAME = os.environ.get("FILEPICKER_NATIVE_FRAME") == "1"
RESIZE_BORDER = 6  # logical px from the window edge that resize


# --------------------------------------------------------------------------- #
#  Pure geometry helper (shared by all platforms, unit-testable)               #
# --------------------------------------------------------------------------- #

def edges_at(x, y, width, height, border=RESIZE_BORDER):
    """Which window edges a point is on: a Qt.Edges value (0 = none)."""
    edges = Qt.Edges()
    if x < border:
        edges |= Qt.LeftEdge
    elif x >= width - border:
        edges |= Qt.RightEdge
    if y < border:
        edges |= Qt.TopEdge
    elif y >= height - border:
        edges |= Qt.BottomEdge
    return edges


def _is_interactive(widget, stop_at):
    """True if the widget (or a parent below the title bar) takes clicks itself."""
    w = widget
    while w is not None and w is not stop_at:
        if isinstance(w, (QAbstractButton, QLineEdit, QComboBox)) or w.property("interactive"):
            return True
        w = w.parentWidget()
    return False


# --------------------------------------------------------------------------- #
#  Title bar                                                                    #
# --------------------------------------------------------------------------- #

class TitleBarButton(QAbstractButton):
    """Minimize / Maximize-Restore / Close, painted in code (46 × title-bar height)."""

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind  # "minimize" | "maximize" | "close"
        self.setFixedSize(46, theme.TITLEBAR_H)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip({"minimize": "Minimize", "maximize": "Maximize", "close": "Close"}[kind])
        self._hover = False
        self._pressed = False

    # Hover/press can be set from outside: on Windows the maximize button is a
    # native "non-client" area (for Snap Layouts) so Qt never sees the mouse there.
    def set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self.update()

    def set_pressed(self, on):
        if on != self._pressed:
            self._pressed = on
            self.update()

    def enterEvent(self, e):
        self.set_hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.set_hover(False)
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        pressed = self._pressed or self.isDown()
        is_close = self.kind == "close"
        if pressed or self._hover:
            if is_close:
                bg = QColor(theme.current["close_pressed" if pressed else "close_hover"])
            else:
                bg = theme.qcolor("border" if pressed else "surface2")
            p.fillRect(self.rect(), bg)
        name = self.kind
        if name == "maximize" and self.window().isMaximized():
            name = "restore"
        color = "#FFFFFF" if is_close and (self._hover or pressed) else theme.current["text"]
        pm = icons.pixmap(name, color, size=14, stroke=1.5)
        x = (self.width() - 14) // 2
        y = (self.height() - 14) // 2
        p.setOpacity(1.0 if self.window().isActiveWindow() or self._hover else 0.55)
        p.drawPixmap(x, y, pm)


class CustomTitleBar(QWidget):
    """
    [icon] File Picker │ <center widgets…>          <right widgets…> [—][▢][✕]

    Empty areas (and labels) drag the window; buttons and inputs stay clickable.
    """

    def __init__(self, window, title, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(theme.TITLEBAR_H)
        self._window = window

        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        self.icon.setScaledContents(True)
        self.title = QLabel(title)
        self.title.setObjectName("AppTitle")
        divider = QFrame()
        divider.setObjectName("TitleDivider")

        self.center = QHBoxLayout()
        self.center.setSpacing(2)
        self.right = QHBoxLayout()
        self.right.setSpacing(4)

        self.min_button = TitleBarButton("minimize")
        self.max_button = TitleBarButton("maximize")
        self.close_button = TitleBarButton("close")
        self.min_button.clicked.connect(window.showMinimized)
        self.max_button.clicked.connect(window.toggle_maximized)
        self.close_button.clicked.connect(window.close)
        self.controls = QWidget()
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        for b in (self.min_button, self.max_button, self.close_button):
            controls.addWidget(b)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.icon)
        lay.addSpacing(10)
        lay.addWidget(self.title)
        lay.addSpacing(16)
        lay.addWidget(divider)
        lay.addSpacing(12)
        lay.addLayout(self.center)
        lay.addStretch(1)
        lay.addLayout(self.right)
        lay.addSpacing(8)
        lay.addWidget(self.controls, 0, Qt.AlignTop)

        self.controls.setVisible(not USE_NATIVE_FRAME)

    def set_icon(self, pixmap):
        self.icon.setPixmap(pixmap)

    def is_drag_area(self, pos):
        """pos is in title-bar coordinates."""
        if not self.rect().contains(pos):
            return False
        child = self.childAt(pos)
        return child is None or not _is_interactive(child, self)

    # ---- macOS / Linux: hand moves to the window manager --------------------------
    # (On Windows these areas are native "caption" areas, so Qt never gets these events.)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not USE_NATIVE_FRAME and self.is_drag_area(e.position().toPoint()):
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                e.accept()
                return
            self._manual_drag = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        offset = getattr(self, "_manual_drag", None)
        if offset is not None and e.buttons() & Qt.LeftButton and not self.window().isMaximized():
            self.window().move(e.globalPosition().toPoint() - offset)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._manual_drag = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and not USE_NATIVE_FRAME and self.is_drag_area(e.position().toPoint()):
            self._window.toggle_maximized()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


# --------------------------------------------------------------------------- #
#  macOS / Linux resize grips                                                  #
# --------------------------------------------------------------------------- #

class _EdgeGrip(QWidget):
    """Invisible strip along one edge/corner; dragging it resizes the window."""

    CURSORS = {
        Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor,
        Qt.TopEdge: Qt.SizeVerCursor, Qt.BottomEdge: Qt.SizeVerCursor,
        Qt.LeftEdge | Qt.TopEdge: Qt.SizeFDiagCursor, Qt.RightEdge | Qt.BottomEdge: Qt.SizeFDiagCursor,
        Qt.RightEdge | Qt.TopEdge: Qt.SizeBDiagCursor, Qt.LeftEdge | Qt.BottomEdge: Qt.SizeBDiagCursor,
    }

    def __init__(self, window, edges):
        super().__init__(window)
        self.edges = edges
        self.setCursor(self.CURSORS[edges])
        self._start = None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        handle = self.window().windowHandle()
        if handle is not None and handle.startSystemResize(self.edges):
            return
        # Fallback (e.g. macOS): resize manually.
        self._start = (e.globalPosition().toPoint(), self.window().geometry())

    def mouseMoveEvent(self, e):
        if not self._start:
            return
        origin, geo = self._start
        d = e.globalPosition().toPoint() - origin
        r = QRect(geo)
        win = self.window()
        if self.edges & Qt.LeftEdge:
            r.setLeft(min(r.left() + d.x(), r.right() - win.minimumWidth()))
        if self.edges & Qt.RightEdge:
            r.setRight(max(r.right() + d.x(), r.left() + win.minimumWidth()))
        if self.edges & Qt.TopEdge:
            r.setTop(min(r.top() + d.y(), r.bottom() - win.minimumHeight()))
        if self.edges & Qt.BottomEdge:
            r.setBottom(max(r.bottom() + d.y(), r.top() + win.minimumHeight()))
        win.setGeometry(r)

    def mouseReleaseEvent(self, e):
        self._start = None


# --------------------------------------------------------------------------- #
#  Frameless main window                                                        #
# --------------------------------------------------------------------------- #

class FramelessWindow(QMainWindow):
    maximized_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.title_bar = None
        self._native_installed = False
        self._grips = []
        if USE_NATIVE_FRAME:
            return
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint)
        if not IS_WINDOWS:
            corners = [Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.TopEdge,
                       Qt.LeftEdge | Qt.BottomEdge, Qt.RightEdge | Qt.BottomEdge]
            sides = [Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge]
            self._grips = [_EdgeGrip(self, e) for e in sides + corners]

    def set_title_bar(self, bar):
        self.title_bar = bar

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    # ---- state ------------------------------------------------------------------

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            self.maximized_changed.emit(self.isMaximized())
            if self.title_bar:
                self.title_bar.max_button.setToolTip("Restore" if self.isMaximized() else "Maximize")
                self.title_bar.max_button.update()
            self._place_grips()
        elif e.type() == QEvent.ActivationChange and self.title_bar:
            for b in (self.title_bar.min_button, self.title_bar.max_button, self.title_bar.close_button):
                b.update()  # dim the controls when the window is inactive, like native apps
        super().changeEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_grips()

    def _place_grips(self):
        if not self._grips:
            return
        b, w, h = RESIZE_BORDER, self.width(), self.height()
        rects = {
            Qt.LeftEdge: QRect(0, b, b, h - 2 * b), Qt.RightEdge: QRect(w - b, b, b, h - 2 * b),
            Qt.TopEdge: QRect(b, 0, w - 2 * b, b), Qt.BottomEdge: QRect(b, h - b, w - 2 * b, b),
            Qt.LeftEdge | Qt.TopEdge: QRect(0, 0, b * 2, b * 2),
            Qt.RightEdge | Qt.TopEdge: QRect(w - b * 2, 0, b * 2, b * 2),
            Qt.LeftEdge | Qt.BottomEdge: QRect(0, h - b * 2, b * 2, b * 2),
            Qt.RightEdge | Qt.BottomEdge: QRect(w - b * 2, h - b * 2, b * 2, b * 2),
        }
        for g in self._grips:
            g.setGeometry(rects[g.edges])
            g.setVisible(not self.isMaximized() and not self.isFullScreen())
            g.raise_()

    # ---- Windows native integration ------------------------------------------------

    def showEvent(self, e):
        super().showEvent(e)
        if IS_WINDOWS and not USE_NATIVE_FRAME and not self._native_installed:
            self._native_installed = True
            try:
                _win.install(int(self.winId()))
            except Exception as err:  # never let window chrome take the app down
                print("window chrome: native setup failed:", err, file=sys.stderr)

    def nativeEvent(self, event_type, message):
        if IS_WINDOWS and self._native_installed and bytes(event_type) == b"windows_generic_MSG":
            try:
                handled, result = _win.handle(self, message)
                if handled:
                    return True, result
            except Exception as err:
                print("window chrome: native event failed:", err, file=sys.stderr)
        return super().nativeEvent(event_type, message)

    def hit_test(self, pos=None):
        """Win32 hit-test code for a window position (default: the cursor). See _win.handle."""
        if pos is None:
            pos = self.mapFromGlobal(QCursor.pos())
        if not self.isMaximized():
            edges = edges_at(pos.x(), pos.y(), self.width(), self.height())
            code = _win.EDGE_CODES.get(edges.value if hasattr(edges, "value") else int(edges))
            if code:
                return code
        bar = self.title_bar
        if bar is not None:
            p = bar.mapFrom(self, pos)
            if bar.max_button.isVisible() and bar.max_button.geometry().contains(bar.controls.mapFrom(bar, p)):
                return _win.HTMAXBUTTON
            if bar.is_drag_area(p):
                return _win.HTCAPTION
        return _win.HTCLIENT


# --------------------------------------------------------------------------- #
#  Win32 details                                                                #
# --------------------------------------------------------------------------- #

class _Win32:
    """Everything that talks to the Windows API. Only used on Windows."""

    HTCLIENT, HTCAPTION, HTMAXBUTTON = 1, 2, 9
    HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = range(10, 18)

    def __init__(self):
        L, R, T, B = (int(e.value) if hasattr(e, "value") else int(e)
                      for e in (Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge))
        self.EDGE_CODES = {
            L: self.HTLEFT, R: self.HTRIGHT, T: self.HTTOP, B: self.HTBOTTOM,
            L | T: self.HTTOPLEFT, R | T: self.HTTOPRIGHT, L | B: self.HTBOTTOMLEFT, R | B: self.HTBOTTOMRIGHT,
        }
        self._max_pressed = False
        if not IS_WINDOWS:
            return

        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.user32 = ctypes.windll.user32
        self.dwmapi = ctypes.windll.dwmapi
        self.shell32 = ctypes.windll.shell32

        class MARGINS(ctypes.Structure):
            _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                        ("top", ctypes.c_int), ("bottom", ctypes.c_int)]

        class NCCALCSIZE_PARAMS(ctypes.Structure):
            _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        class APPBARDATA(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                        ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]

        class TRACKMOUSEEVENT(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]

        self.TRACKMOUSEEVENT = TRACKMOUSEEVENT
        self.MARGINS, self.NCCALCSIZE_PARAMS = MARGINS, NCCALCSIZE_PARAMS
        self.MONITORINFO, self.APPBARDATA = MONITORINFO, APPBARDATA

        u = self.user32
        self._get_style = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        self._set_style = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        self._get_style.restype = ctypes.c_ssize_t
        self._get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self._set_style.restype = ctypes.c_ssize_t
        self._set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.MonitorFromRect.restype = wintypes.HMONITOR
        u.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
        u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        u.IsZoomed.argtypes = [wintypes.HWND]
        self.shell32.SHAppBarMessage.restype = ctypes.c_size_t
        self.shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]

    # ---- setup ----

    def install(self, hwnd):
        ct, u = self.ct, self.user32
        GWL_STYLE = -16
        WS_THICKFRAME, WS_CAPTION, WS_SYSMENU = 0x00040000, 0x00C00000, 0x00080000
        WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
        style = self._get_style(hwnd, GWL_STYLE)
        self._set_style(hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_CAPTION | WS_SYSMENU
                        | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)

        # Drop shadow + rounded corners (Windows 11; ignored on Windows 10).
        margins = self.MARGINS(-1, -1, -1, -1)
        self.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ct.byref(margins))
        DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
        pref = ct.c_int(DWMWCP_ROUND)
        self.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ct.byref(pref), ct.sizeof(pref))

        SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
        u.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                       SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)

    # ---- messages ----

    def handle(self, window, message):
        """Returns (handled, result) for one Windows message."""
        WM_NCCALCSIZE, WM_NCHITTEST = 0x0083, 0x0084
        WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP, WM_NCLBUTTONDBLCLK = 0x00A0, 0x00A1, 0x00A2, 0x00A3
        WM_NCMOUSELEAVE = 0x02A2

        msg = self.wt.MSG.from_address(int(message))
        m = msg.message
        bar = window.title_bar
        max_btn = bar.max_button if bar else None

        if m == WM_NCCALCSIZE:
            if msg.wParam:
                params = self.NCCALCSIZE_PARAMS.from_address(msg.lParam)
                if self.user32.IsZoomed(msg.hWnd):
                    self._fit_to_work_area(params.rgrc[0])
            return True, 0  # client area = whole window: no native title bar or border

        if m == WM_NCHITTEST:
            code = window.hit_test()
            if max_btn:
                max_btn.set_hover(code == self.HTMAXBUTTON)
                if code != self.HTMAXBUTTON:
                    max_btn.set_pressed(False)
            return True, code

        if max_btn is None:
            return False, 0
        if m == WM_NCMOUSELEAVE:
            max_btn.set_hover(False)
            max_btn.set_pressed(False)
            return False, 0
        if m in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) and msg.wParam == self.HTMAXBUTTON:
            max_btn.set_pressed(True)
            return True, 0
        if m == WM_NCLBUTTONUP and msg.wParam == self.HTMAXBUTTON:
            if max_btn._pressed:
                max_btn.set_pressed(False)
                window.toggle_maximized()
            return True, 0
        if m == WM_NCMOUSEMOVE:
            if msg.wParam == self.HTMAXBUTTON:
                self._track_nc_leave(msg.hWnd)  # so we hear about it if the mouse leaves the window
            else:
                max_btn.set_hover(False)
        return False, 0

    def _track_nc_leave(self, hwnd):
        TME_LEAVE, TME_NONCLIENT = 0x2, 0x10
        t = self.TRACKMOUSEEVENT()
        t.cbSize = self.ct.sizeof(t)
        t.dwFlags = TME_LEAVE | TME_NONCLIENT
        t.hwndTrack = hwnd
        self.user32.TrackMouseEvent(self.ct.byref(t))

    def _fit_to_work_area(self, rect):
        """Maximized: use exactly the monitor's work area (not the oversized frame)."""
        ct = self.ct
        MONITOR_DEFAULTTONEAREST = 2
        monitor = self.user32.MonitorFromRect(ct.byref(rect), MONITOR_DEFAULTTONEAREST)
        info = self.MONITORINFO()
        info.cbSize = ct.sizeof(info)
        if not monitor or not self.user32.GetMonitorInfoW(monitor, ct.byref(info)):
            return
        work = info.rcWork
        rect.left, rect.top, rect.right, rect.bottom = work.left, work.top, work.right, work.bottom
        # An auto-hidden taskbar needs a 2px gap on its edge to be able to pop up.
        edge = self._autohide_taskbar_edge(info.rcMonitor)
        if edge == 0:
            rect.left += 2
        elif edge == 1:
            rect.top += 2
        elif edge == 2:
            rect.right -= 2
        elif edge == 3:
            rect.bottom -= 2

    def _autohide_taskbar_edge(self, monitor_rect):
        ABM_GETSTATE, ABM_GETAUTOHIDEBAREX, ABS_AUTOHIDE = 0x4, 0xB, 0x1
        data = self.APPBARDATA()
        data.cbSize = self.ct.sizeof(data)
        if not self.shell32.SHAppBarMessage(ABM_GETSTATE, self.ct.byref(data)) & ABS_AUTOHIDE:
            return None
        for edge in range(4):  # left, top, right, bottom
            d = self.APPBARDATA()
            d.cbSize = self.ct.sizeof(d)
            d.uEdge = edge
            d.rc = monitor_rect
            if self.shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX, self.ct.byref(d)):
                return edge
        return None


_win = _Win32()
