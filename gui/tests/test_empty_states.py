"""
The empty state of each new tab (what you see before the first scan).

Regression test for a layout bug: the word-wrapped description was given the height of ONE line (the layout
measured it at the full page width, then the label was fixed at 400 px), so on screen it was clipped and
overlapped the title. Needs PySide6; runs headless.
"""
import gc
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QLabel
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class EmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from picker_gui import theme
        theme.apply(cls.app, "light")
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.tmp.cleanup()

    def setUp(self):
        self._made = []

    def tearDown(self):
        # Destroy every widget while the QApplication still exists. Leaving them to the garbage collector at
        # interpreter exit makes Qt abort intermittently on some Python / PySide6 combinations.
        for page in self._made:
            page.close()
            page.setParent(None)
            page.deleteLater()
        self._made.clear()
        self.app.processEvents()
        gc.collect()
        self.app.processEvents()

    def pages(self):
        from picker_gui.compare_page import ComparePage
        from picker_gui.duplicates_page import DuplicatesPage
        from picker_gui.find_page import FindPage
        from picker_gui.snapshot_page import SnapshotPage
        from picker_gui.space_page import SpacePage
        store = os.path.join(self.tmp.name, "store")
        made = {"compare": ComparePage(), "snapshots": SnapshotPage(store), "duplicates": DuplicatesPage(),
                "space": SpacePage(), "find": FindPage(store)}
        self._made.extend(made.values())
        return made

    def show(self, page, w, h):
        page.resize(w, h)
        page.show()
        for _ in range(3):
            self.app.processEvents()

    def test_description_is_never_clipped(self):
        for name, page in self.pages().items():
            for size in ((1280, 860), (1040, 680), (900, 520)):
                with self.subTest(page=name, size=size):
                    self.show(page, *size)
                    body = page.empty.body
                    self.assertGreaterEqual(body.height(), body.heightForWidth(body.width()),
                                            "description text is clipped")
                    self.assertGreater(body.height(), 16, "description was squeezed to one line")
                    page.close()

    def test_buttons_guide_the_user(self):
        folder = self.tmp.name
        p = self.pages()
        for name, ask, ready in (("duplicates", "Choose a folder", "Find duplicates"),
                                 ("space", "Choose a folder", "Measure"),
                                 ("snapshots", "Choose a folder", "Scan"),
                                 ("find", "Choose a folder", "Index")):
            page = p[name]
            with self.subTest(page=name):
                self.assertIn(ask, page.empty.action.text())
                page.path.setText(folder)
                self.assertIn(ready, page.empty.action.text())
        # compare needs two folders: A, then B, then go
        c = p["compare"]
        self.assertIn("folder A", c.empty.action.text())
        self.assertFalse(c.compare_btn.isEnabled())
        c.path_a.setText(folder)
        self.assertIn("folder B", c.empty.action.text())
        self.assertFalse(c.compare_btn.isEnabled())
        c.path_b.setText(os.path.join(folder, "store"))
        os.makedirs(os.path.join(folder, "store"), exist_ok=True)
        c.path_b.setText(os.path.join(folder, "store"))
        self.assertIn("Compare", c.empty.action.text())
        self.assertTrue(c.compare_btn.isEnabled())

    def test_main_buttons_wait_for_a_folder(self):
        p = self.pages()
        for name, btn in (("duplicates", "find_btn"), ("space", "scan_btn"), ("snapshots", "scan_btn")):
            page = p[name]
            with self.subTest(page=name):
                self.assertFalse(getattr(page, btn).isEnabled())
                page.path.setText(self.tmp.name)
                self.assertTrue(getattr(page, btn).isEnabled())
                page.path.setText("")
                self.assertFalse(getattr(page, btn).isEnabled())

    def test_find_result_chip_hidden_until_it_has_text(self):
        page = self.pages()["find"]
        self.show(page, 1100, 700)
        self.assertFalse(page.result_chip.isVisibleTo(page))


if __name__ == "__main__":
    unittest.main()
