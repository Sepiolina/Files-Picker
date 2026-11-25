"""
Regenerate the app icon (gui/assets/icon.png + icon.ico) from code, so it
matches the logo mark in the top bar. Run:  python gui/tools/make_icon.py
"""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

ACCENT = "#4F46E5"
OUT = Path(__file__).resolve().parent.parent / "assets"


def draw(size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(ACCENT))
    margin = size * 0.06
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    p.drawRoundedRect(rect, size * 0.22, size * 0.22)
    font = QFont("Segoe UI, Inter, DejaVu Sans")
    font.setPixelSize(int(size * 0.58))
    font.setWeight(QFont.Bold)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(rect, Qt.AlignCenter, "F")
    p.end()
    return img


if __name__ == "__main__":
    app = QGuiApplication([])
    OUT.mkdir(exist_ok=True)
    draw(256).save(str(OUT / "icon.png"))
    draw(256).save(str(OUT / "icon.ico"), "ICO")
    print("wrote", OUT / "icon.png", "and", OUT / "icon.ico")
