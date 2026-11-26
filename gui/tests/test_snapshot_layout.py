"""
Layout of the Snapshots tab (needs PySide6, runs headless).

The screen is a two-pane master/detail: new-scan controls + scan history on the left, the selected scan's
changes on the right. These tests keep it usable at the smallest supported window size.
"""
import gc
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SnapshotLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from picker_gui import theme
        theme.apply(cls.app, "light")

    def setUp(self):
        from picker_gui.snapshot_page import SnapshotPage
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.a, self.b = os.path.join(base, "Projects"), os.path.join(base, "Photos")
        for folder, n in ((self.a, 30), (self.b, 10)):
            os.makedirs(folder)
            for i in range(n):
                with open(os.path.join(folder, f"f{i}.txt"), "wb") as f:
                    f.write(b"x" * (i + 1))
        self.page = SnapshotPage(os.path.join(base, "store"))
        store = self.page.snaps
        store.scan_and_save(self.a)
        for i in range(4):
            with open(os.path.join(self.a, f"new{i}.txt"), "wb") as f:
                f.write(b"y" * 50)
        os.remove(os.path.join(self.a, "f0.txt"))
        self.second = store.scan_and_save(self.a)["meta"]
        store.scan_and_save(self.b)
        self.page.refresh(select_id=self.second["id"])

    def tearDown(self):
        self.page.close()
        self.page.setParent(None)
        self.page.deleteLater()
        self.app.processEvents()
        gc.collect()
        self.app.processEvents()
        self.tmp.cleanup()

    def show(self, w, h):
        self.page.resize(w, h)
        self.page.show()
        deadline = time.time() + 5
        while time.time() < deadline:      # the change list is computed on a worker thread
            self.app.processEvents()
            if self.page.changes.model.rows:
                break
            time.sleep(0.02)
        for _ in range(3):
            self.app.processEvents()

    def test_history_lists_every_scan_newest_first(self):
        self.show(1280, 860)
        times = [self.page.list.item(i).data(Qt.UserRole)["time"] for i in range(self.page.list.count())]
        self.assertEqual(len(times), 3)
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(self.page.stack.currentIndex(), 1)

    def test_folder_filter_appears_with_two_folders_and_filters(self):
        self.show(1280, 860)
        self.assertTrue(self.page.folder_filter.isVisibleTo(self.page))
        self.page.folder_filter.setCurrentIndex(self.page.folder_filter.findData(self.b))
        self.page.refresh()
        self.assertEqual(self.page.list.count(), 1)

    def test_shows_what_changed_and_the_list_is_the_main_content(self):
        self.show(1280, 860)
        kinds = sorted({r.kind for r in self.page.changes.model.rows})
        self.assertEqual(kinds, ["added", "removed"])
        self.assertGreaterEqual(self.page.changes.height(), 250)

    def test_usable_at_the_smallest_window(self):
        self.show(1040, 640)
        page_rect = self.page.rect()
        for name in ("again_btn", "export_btn", "report_btn", "scan_btn", "baseline"):
            w = getattr(self.page, name)
            top_left = w.mapTo(self.page, w.rect().topLeft())
            bottom_right = w.mapTo(self.page, w.rect().bottomRight())
            self.assertTrue(page_rect.contains(top_left) and page_rect.contains(bottom_right),
                            f"{name} is outside the window")
        self.assertGreaterEqual(self.page.changes.height(), 150, "the changes list is squeezed")
        self.assertGreaterEqual(self.page.list.height(), 120, "the scan history is squeezed")
        self.assertEqual(self.page.list.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        # toolbar buttons must not overlap each other
        boxes = []
        for name in ("baseline", "history_btn", "preview_btn", "report_btn", "export_btn"):
            w = getattr(self.page, name)
            boxes.append((name, w.mapTo(self.page, w.rect().topLeft()).x(), w.mapTo(self.page, w.rect().topRight()).x()))
        boxes.sort(key=lambda b: b[1])
        for (n1, _l1, r1), (n2, l2, _r2) in zip(boxes, boxes[1:]):
            self.assertLessEqual(r1, l2, f"{n1} overlaps {n2}")

    def test_picking_another_scan_dismisses_the_old_banner(self):
        self.show(1280, 860)
        self.page.banner.show_message("success", "Saved")
        self.assertTrue(self.page.banner.isVisibleTo(self.page))
        self.page.list.setCurrentRow(2)
        self.assertFalse(self.page.banner.isVisibleTo(self.page))

    def test_history_chart_toggle(self):
        self.show(1280, 860)
        self.assertFalse(self.page.trend_panel.isVisibleTo(self.page))
        self.assertTrue(self.page.history_btn.isEnabled())       # this folder has two scans
        self.page.history_btn.setChecked(True)
        self.assertTrue(self.page.trend_panel.isVisibleTo(self.page))
        self.page.list.setCurrentRow(0)                           # the folder with a single scan
        self.assertFalse(self.page.history_btn.isEnabled())
        self.assertFalse(self.page.trend_panel.isVisibleTo(self.page))


if __name__ == "__main__":
    unittest.main()
