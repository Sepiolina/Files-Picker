"""Small helpers shared by the pages."""

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_duration(ms):
    s = int((ms or 0) / 1000)
    if s < 60:
        return f"{(ms or 0) / 1000:.1f}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def fmt_int(n):
    return f"{int(n or 0):,}"


def reveal(path):
    """Show a file in Explorer / Finder (selected), or open its folder."""
    p = Path(path)
    if not p.exists():
        p = p.parent
    if sys.platform == "win32":
        target = os.path.normpath(str(p))  # Explorer needs backslashes
        if p.is_file():
            # One string on purpose: Explorer parses its own command line and
            # expects exactly  /select,"C:\path\file"
            subprocess.Popen(f'explorer /select,"{target}"')
        else:
            os.startfile(target)
    elif sys.platform == "darwin" and p.is_file():
        subprocess.Popen(["open", "-R", str(p)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p if p.is_dir() else p.parent)))


def open_file(path):
    """Open a file with its default program (e.g. a .csv report in Excel)."""
    if sys.platform == "win32":
        os.startfile(os.path.normpath(os.fspath(path)))
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(path)))


def read_text_file(path):
    """Read a .txt/.csv the way Excel / Notepad might have saved it."""
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def guess_separator(path, text):
    """Pick a sensible separator for an imported file."""
    lower = str(path).lower()
    if lower.endswith(".tsv"):
        return "tab"
    if not lower.endswith(".csv"):
        return "newline"
    first = text.splitlines()[0] if text else ""
    counts = {"comma": first.count(","), "semicolon": first.count(";"),
              "tab": first.count("\t"), "pipe": first.count("|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else "newline"


def scan_note(cloud=0, ignored=0):
    """One sentence about what a scan deliberately skipped ('' if nothing)."""
    parts = []
    if cloud:
        parts.append(f"{fmt_int(cloud)} online-only cloud file{'s' if cloud != 1 else ''} (never downloaded)")
    if ignored:
        parts.append(f"{fmt_int(ignored)} item{'s' if ignored != 1 else ''} matching your ignore rules")
    return ("Skipped " + " and ".join(parts) + ".") if parts else ""
