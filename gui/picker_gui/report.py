"""
Shareable reports for Compare, Snapshots, Duplicates and Space.

A page builds a `Report` (headings, number tiles, charts, tables); this module
turns it into either

    * a single self-contained .html file (charts are inline SVG, no external files), or
    * a .pdf file (charts are rasterised from the same SVG; needs Qt).

Reports always use a light, print-friendly palette. Everything that comes from
file names is HTML-escaped.
"""

import html
import math
from datetime import datetime

from .treemap import squarify

INK, MUTED, LINE, PAPER, SOFT = "#1B1D26", "#6B7080", "#E3E5EC", "#FFFFFF", "#F5F6FA"
ACCENT = "#5B54F0"
FONT = "Segoe UI, Helvetica, Arial, sans-serif"
MAX_ROWS_HTML, MAX_ROWS_PDF = 3000, 800


def esc(x):
    return html.escape(str(x), quote=True)


# --------------------------------------------------------------------------- #
#  The report model                                                             #
# --------------------------------------------------------------------------- #

class Report:
    def __init__(self, title, subtitle=""):
        self.title, self.subtitle = title, subtitle
        self.blocks = []
        self.created = datetime.now().strftime("%d %B %Y, %H:%M")

    def heading(self, text):
        self.blocks.append(("heading", text))
        return self

    def text(self, text):
        self.blocks.append(("text", text))
        return self

    def tiles(self, items):
        """items: [(label, value)]"""
        self.blocks.append(("tiles", items))
        return self

    def svg(self, svg, width):
        self.blocks.append(("svg", svg, width))
        return self

    def table(self, header, rows, aligns=None, note=""):
        """rows: list of lists of plain values (escaped for you); aligns: 'l' / 'r' per column"""
        self.blocks.append(("table", header, rows, aligns or ["l"] * len(header), note))
        return self


# --------------------------------------------------------------------------- #
#  SVG charts (literal colours, so they look the same in a browser and a PDF)   #
# --------------------------------------------------------------------------- #

def _svg(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="{FONT}">{body}</svg>')


def _t(x, y, text, size=12, fill=INK, anchor="start", weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{esc(text)}</text>')


def svg_donut(segments, center_top="", center_bottom="", width=560, height=230):
    """segments: [(label, value, color)] -> donut + legend"""
    segs = [s for s in segments if s[1] > 0]
    total = sum(v for _, v, _ in segs)
    cx, cy, r, thick = 115, height / 2, 84, 28
    body = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{LINE}" stroke-width="{thick}"/>']
    if total:
        angle = -90.0
        for _, v, color in segs:
            span = v / total * 360
            if span >= 359.99:
                body.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="{thick}"/>')
                break
            a0, a1 = math.radians(angle), math.radians(angle + max(span - 1.0, 0.5))
            x0, y0, x1, y1 = cx + r * math.cos(a0), cy + r * math.sin(a0), cx + r * math.cos(a1), cy + r * math.sin(a1)
            body.append(f'<path d="M {x0:.2f} {y0:.2f} A {r} {r} 0 {1 if span > 180 else 0} 1 {x1:.2f} {y1:.2f}" '
                        f'fill="none" stroke="{color}" stroke-width="{thick}"/>')
            angle += span
    body.append(_t(cx, cy + 4, center_top, 24, INK, "middle", "bold"))
    body.append(_t(cx, cy + 24, center_bottom, 11, MUTED, "middle"))
    y = max(30, cy - len(segs) * 13)
    for label, v, color in segs:
        pct = f"  ({v / total * 100:.0f}%)" if total else ""
        body.append(f'<circle cx="250" cy="{y - 4}" r="5" fill="{color}"/>')
        body.append(_t(264, y, f"{label}{pct}", 13))
        body.append(_t(width - 10, y, f"{v:,}" if isinstance(v, int) else str(v), 13, MUTED, "end"))
        y += 26
    return _svg(width, height, "".join(body))


def svg_bars(rows, width=640, fmt="{:,}".format, legend=()):
    """rows: [(label, [(value, color, name)])]; legend: [(name, color)]"""
    top = max((sum(v for v, _, _ in segs) for _, segs in rows), default=0) or 1
    label_w, value_w, row_h = 150, 90, 26
    bar_x, bar_w = label_w + 10, width - label_w - value_w - 20
    y0 = 24 if legend else 4
    body, x = [], 0
    for name, color in legend:
        body.append(f'<circle cx="{x + 5}" cy="11" r="5" fill="{color}"/>')
        body.append(_t(x + 15, 15, name, 12, MUTED))
        x += 30 + len(name) * 7
    for i, (label, segs) in enumerate(rows):
        y = y0 + i * row_h
        shown = label if len(label) <= 22 else label[:21] + "…"
        body.append(_t(0, y + 16, shown, 12))
        xx = float(bar_x)
        for value, color, _ in segs:
            if value:
                w = bar_w * value / top
                body.append(f'<rect x="{xx:.1f}" y="{y + 4}" width="{max(w, 1):.1f}" height="{row_h - 10}" fill="{color}"/>')
                xx += w
        body.append(_t(width, y + 16, fmt(sum(v for v, _, _ in segs)), 12, MUTED, "end"))
    return _svg(width, y0 + max(len(rows), 1) * row_h + 4, "".join(body))


def svg_trend(points, width=640, height=150, fmt=str):
    """points: [(label, value)] oldest first"""
    if len(points) < 2:
        return _svg(width, 40, _t(0, 24, "Only one scan so far - no trend to show yet.", 12, MUTED))
    pad_l, pad_r, pad_t, pad_b = 10, 14, 28, 28
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or max(hi, 1)
    lo -= span * 0.15
    span *= 1.3
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    xy = [(pad_l + w * i / (len(points) - 1), pad_t + h - h * (v - lo) / span) for i, (_, v) in enumerate(points)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    area = f"{xy[0][0]:.1f},{pad_t + h} {line} {xy[-1][0]:.1f},{pad_t + h}"
    body = [f'<line x1="{pad_l}" y1="{pad_t + h}" x2="{pad_l + w}" y2="{pad_t + h}" stroke="{LINE}"/>',
            f'<polygon points="{area}" fill="{ACCENT}" fill-opacity="0.12"/>',
            f'<polyline points="{line}" fill="none" stroke="{ACCENT}" stroke-width="2.5" stroke-linejoin="round"/>']
    body += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{PAPER}" stroke="{ACCENT}" stroke-width="2"/>' for x, y in xy]
    body.append(_t(pad_l, 16, f"{fmt(values[0])}  →  {fmt(values[-1])}", 12, MUTED))
    body.append(_t(pad_l, height - 8, points[0][0], 11, MUTED))
    body.append(_t(width - pad_r, height - 8, points[-1][0], 11, MUTED, "end"))
    return _svg(width, height, "".join(body))


def svg_treemap(items, width=720, height=400):
    """items: [(label, size, color, sublabel)] sorted largest first"""
    items = [i for i in items if i[1] > 0]
    rects = squarify([i[1] for i in items], 0, 0, width, height)
    body = []
    for (label, _size, color, sub), (x, y, w, h) in zip(items, rects):
        body.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{max(w - 2, 0):.1f}" height="{max(h - 2, 0):.1f}" '
                    f'rx="3" fill="{color}"/>')
        chars = int((w - 12) / 6.6)
        if chars >= 4 and h >= 22:
            body.append(_t(x + 7, y + 17, label if len(label) <= chars else label[:max(chars - 1, 1)] + "…", 12, "#FFFFFF", weight="600"))
            if h >= 38 and sub:
                body.append(_t(x + 7, y + 33, sub, 11, "#FFFFFF"))
    return _svg(width, height, "".join(body))


# --------------------------------------------------------------------------- #
#  HTML                                                                         #
# --------------------------------------------------------------------------- #

CSS = f"""
body{{font-family:{FONT};color:{INK};background:{SOFT};margin:0;padding:32px 16px;line-height:1.5}}
main{{max-width:960px;margin:0 auto;background:{PAPER};border:1px solid {LINE};border-radius:14px;padding:36px 40px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:17px;margin:30px 0 10px}}
.sub{{color:{MUTED};margin:0 0 4px}} .created{{color:{MUTED};font-size:12px;margin:0 0 22px}}
.tiles{{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}}
.tile{{flex:1 1 130px;border:1px solid {LINE};border-radius:10px;padding:12px 14px;background:{SOFT}}}
.tile b{{display:block;font-size:22px}} .tile span{{color:{MUTED};font-size:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}}
th{{text-align:left;color:{MUTED};font-weight:600;border-bottom:2px solid {LINE};padding:6px 8px}}
td{{border-bottom:1px solid {LINE};padding:5px 8px;vertical-align:top;word-break:break-all}}
tr:nth-child(even) td{{background:{SOFT}}} .r{{text-align:right;white-space:nowrap}}
.note{{color:{MUTED};font-size:12px}} svg{{max-width:100%;height:auto;display:block;margin:10px 0}}
@media print{{body{{background:#fff;padding:0}} main{{border:0;padding:0}}}}
"""


def _table_html(header, rows, aligns, note, limit):
    shown = rows[:limit]
    head = "".join(f'<th class="{"r" if a == "r" else ""}">{esc(h)}</th>' for h, a in zip(header, aligns))
    body = "".join("<tr>" + "".join(f'<td class="{"r" if a == "r" else ""}">{esc(c)}</td>'
                                    for c, a in zip(r, aligns)) + "</tr>" for r in shown)
    extra = f'<p class="note">Showing the first {len(shown):,} of {len(rows):,} rows.</p>' if len(rows) > len(shown) else ""
    return f"<table><tr>{head}</tr>{body}</table>{extra}" + (f'<p class="note">{esc(note)}</p>' if note else "")


def to_html(report):
    parts = [f"<h1>{esc(report.title)}</h1>"]
    if report.subtitle:
        parts.append(f'<p class="sub">{esc(report.subtitle)}</p>')
    parts.append(f'<p class="created">Created {esc(report.created)} with File Picker</p>')
    for block in report.blocks:
        kind = block[0]
        if kind == "heading":
            parts.append(f"<h2>{esc(block[1])}</h2>")
        elif kind == "text":
            parts.append(f"<p>{esc(block[1])}</p>")
        elif kind == "tiles":
            parts.append('<div class="tiles">' + "".join(f'<div class="tile"><b>{esc(v)}</b><span>{esc(k)}</span></div>'
                                                         for k, v in block[1]) + "</div>")
        elif kind == "svg":
            parts.append(block[1])
        elif kind == "table":
            parts.append(_table_html(block[1], block[2], block[3], block[4], MAX_ROWS_HTML))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(report.title)}</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head>'
            f'<body><main>{"".join(parts)}</main></body></html>')


# --------------------------------------------------------------------------- #
#  PDF (Qt)                                                                     #
# --------------------------------------------------------------------------- #

def pdf_document(report):
    """The report as a QTextDocument (charts rasterised from the SVG at 2x for sharp output)."""
    from PySide6.QtCore import QByteArray, QUrl
    from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QPainter, QTextDocument)
    from PySide6.QtSvg import QSvgRenderer

    doc = QTextDocument()
    # The app sets its font in pixels; <h1>/<h2> are sized relative to a POINT-based default font, so with the
    # app font they collapse to a few pixels. Give the document an explicit point-sized default.
    base = QFont(QGuiApplication.font())
    base.setPointSizeF(10)
    doc.setDefaultFont(base)
    parts = [f'<h1 style="font-size:22pt">{esc(report.title)}</h1>']
    if report.subtitle:
        parts.append(f'<p style="color:{MUTED}">{esc(report.subtitle)}</p>')
    parts.append(f'<p style="color:{MUTED};font-size:8pt">Created {esc(report.created)} with File Picker</p>')
    n_img = 0
    for block in report.blocks:
        kind = block[0]
        if kind == "heading":
            parts.append(f'<h2 style="font-size:14pt;margin-top:18px">{esc(block[1])}</h2>')
        elif kind == "text":
            parts.append(f"<p>{esc(block[1])}</p>")
        elif kind == "tiles":
            cells = "".join(f'<td bgcolor="{SOFT}" align="center" width="{100 // len(block[1])}%">'
                            f'<span style="font-size:16pt;font-weight:bold">{esc(v)}</span><br>'
                            f'<span style="color:{MUTED};font-size:8pt">{esc(k)}</span></td>' for k, v in block[1])
            parts.append(f'<table width="100%" cellspacing="6" cellpadding="8"><tr>{cells}</tr></table>')
        elif kind == "svg":
            renderer = QSvgRenderer(QByteArray(block[1].encode("utf-8")))
            size = renderer.defaultSize()
            if size.isValid() and size.width() > 0:
                img = QImage(size.width() * 2, size.height() * 2, QImage.Format_ARGB32)
                img.fill(QColor(PAPER))
                painter = QPainter(img)
                renderer.render(painter)
                painter.end()
                n_img += 1
                name = f"chart{n_img}"
                doc.addResource(QTextDocument.ImageResource, QUrl(name), img)
                parts.append(f'<p><img src="{name}" width="{min(block[2], 500)}"></p>')
        elif kind == "table":
            _, header, rows, aligns, note = block
            shown = rows[:MAX_ROWS_PDF]
            th = "".join(f'<th align="{"right" if a == "r" else "left"}" bgcolor="{SOFT}">{esc(h)}</th>'
                         for h, a in zip(header, aligns))
            trs = "".join("<tr>" + "".join(f'<td align="{"right" if a == "r" else "left"}">{esc(c)}</td>'
                                           for c, a in zip(r, aligns)) + "</tr>" for r in shown)
            extra = (f'<p style="color:{MUTED};font-size:8pt">Showing the first {len(shown):,} of {len(rows):,} rows. '
                     f'Export HTML for the full list.</p>') if len(rows) > len(shown) else ""
            parts.append(f'<table width="100%" cellspacing="0" cellpadding="4" style="font-size:8pt"><tr>{th}</tr>{trs}</table>{extra}')
            if note:
                parts.append(f'<p style="color:{MUTED};font-size:8pt">{esc(note)}</p>')
    doc.setDefaultStyleSheet(f"body{{font-family:'{FONT.split(',')[0]}';color:{INK};font-size:10pt}} "
                             f"td{{border-bottom:1px solid {LINE}}}")
    doc.setHtml(f"<body>{''.join(parts)}</body>")
    return doc


def to_pdf(report, path):
    """Write the report as a PDF."""
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

    doc = pdf_document(report)
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)
    writer.setTitle(report.title)
    doc.print_(writer)


def write_report(report, path):
    """Save as PDF or HTML depending on the extension. Raises OSError on failure."""
    if path.lower().endswith(".pdf"):
        to_pdf(report, path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_html(report))


def ask_and_save(parent, report, default_name):
    """Show a Save dialog (HTML or PDF), write the report. -> path, or None if cancelled."""
    from PySide6.QtWidgets import QFileDialog
    path, chosen = QFileDialog.getSaveFileName(parent, "Export report", default_name + ".html",
                                               "Web page (*.html);;PDF document (*.pdf)")
    if not path:
        return None
    if "pdf" in chosen.lower() and not path.lower().endswith(".pdf"):
        path += ".pdf"
    elif "html" in chosen.lower() and not path.lower().endswith((".html", ".htm")):
        path += ".html"
    write_report(report, path)
    return path
