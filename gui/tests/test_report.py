"""Tests for the treemap layout and the report builder (no GUI needed for HTML)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from picker_gui import report as R  # noqa: E402
from picker_gui.treemap import squarify  # noqa: E402
from picker_gui.util import scan_note  # noqa: E402


class TreemapTests(unittest.TestCase):
    def test_areas_are_proportional_and_inside_the_box(self):
        values = [60, 30, 20, 10, 5, 5]
        rects = squarify(values, 0, 0, 100, 50)
        self.assertEqual(len(rects), len(values))
        for (x, y, w, h), v in zip(rects, values):
            self.assertGreaterEqual(x, -1e-9)
            self.assertGreaterEqual(y, -1e-9)
            self.assertLessEqual(x + w, 100 + 1e-6)
            self.assertLessEqual(y + h, 50 + 1e-6)
            self.assertAlmostEqual(w * h, v / sum(values) * 5000, places=4)

    def test_degenerate_input(self):
        self.assertEqual(squarify([], 0, 0, 10, 10), [])
        self.assertEqual(len(squarify([1, 1], 0, 0, 0, 10)), 2)


class ReportTests(unittest.TestCase):
    def make(self):
        r = R.Report("A <b>title</b>", "C:\\a & b")
        r.tiles([("Files", "12")])
        r.svg(R.svg_donut([("Same", 3, "#059669"), ("Diff", 1, "#D97706")], "4", "files"), 560)
        r.svg(R.svg_bars([(".txt", [(2, "#5B54F0", "x")])], fmt=str), 640)
        r.svg(R.svg_trend([("a", 1), ("b", 3)]), 640)
        r.svg(R.svg_treemap([("big", 5, "#5B54F0", "5 B"), ("small", 1, "#059669", "1 B")]), 720)
        r.table(["File"], [["<script>alert(1)</script>"]] * 3)
        return r

    def test_html_is_escaped_and_self_contained(self):
        html = R.to_html(self.make())
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;b&gt;title", html)
        self.assertNotIn("http://", html.replace("http://www.w3.org/2000/svg", ""))   # nothing external

    def test_row_limit_is_announced(self):
        r = R.Report("t")
        r.table(["a"], [[i] for i in range(R.MAX_ROWS_HTML + 5)])
        self.assertIn(f"first {R.MAX_ROWS_HTML:,} of {R.MAX_ROWS_HTML + 5:,}", R.to_html(r))

    def test_write_html_by_extension(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.html")
            R.write_report(self.make(), path)
            self.assertTrue(open(path, encoding="utf-8").read().startswith("<!doctype html>"))

    def test_pdf(self):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 not installed")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])   # noqa: F841
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.pdf")
            R.write_report(self.make(), path)
            self.assertEqual(open(path, "rb").read(5), b"%PDF-")


class PdfHeadingTests(unittest.TestCase):
    """
    Regression: the app sets its font in PIXELS. <h1>/<h2> inherit that, so their text was sized in pixels - fine on
    screen, but the PDF device has 1200 dpi, where a 26 px heading is about 1.6 pt: the title and section headings of
    every PDF report printed as dashes. Headings must be sized in points.
    """

    def test_headings_are_point_sized_even_with_a_pixel_sized_app_font(self):
        try:
            from PySide6.QtGui import QFont
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 not installed")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        old_font = app.font()
        try:
            font = QFont()
            font.setPixelSize(13)            # what theme.apply() does
            app.setFont(font)
            rep = R.Report("A report title", "sub")
            rep.heading("Overview")
            doc = R.pdf_document(rep)
            sizes = {}
            block = doc.firstBlock()
            while block.isValid():
                fragment = block.begin().fragment()
                if fragment.isValid():
                    sizes[block.text()] = fragment.charFormat().font().pointSizeF()
                block = block.next()
            self.assertGreaterEqual(sizes["A report title"], 18, "title is not point-sized (it would print as a dash)")
            self.assertGreaterEqual(sizes["Overview"], 12, "section heading is not point-sized")
        finally:
            app.setFont(old_font)


class NoteTests(unittest.TestCase):
    def test_scan_note(self):
        self.assertEqual(scan_note(), "")
        self.assertEqual(scan_note(1, 0), "Skipped 1 online-only cloud file (never downloaded).")
        self.assertIn("2 items matching your ignore rules", scan_note(0, 2))


if __name__ == "__main__":
    unittest.main()
