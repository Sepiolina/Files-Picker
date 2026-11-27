"""Big change lists must not freeze the Snapshots table (needs PySide6, runs headless)."""
import os
import sys
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
class ScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from picker_gui import theme
        theme.apply(cls.app, "light")

    def test_build_changes_is_capped_but_counts_are_not(self):
        from picker_gui.snapshot_page import build_changes
        new = {f"f{i}": (i, i, None) for i in range(1000)}
        changes = {"added": list(new), "removed": [], "modified": [], "moved": [], "unchanged": 0}
        self.assertEqual(len(build_changes(changes, {}, new)), 1000)
        self.assertEqual(len(build_changes(changes, {}, new, limit=100)), 100)

    def test_table_sorts_by_header_and_keeps_order_on_reload(self):
        from picker_gui.snapshot_page import Change, ChangeModel
        from picker_gui.result_table import ResultTable
        t = ResultTable(ChangeModel(), path_cols=(1,), stretch_col=1, sort=(0, Qt.AscendingOrder))
        rows = [Change("added", "b", 5, 1, ""), Change("modified", "a", 9, 2, "x"), Change("removed", "c", 1, 3, "")]
        t.set_rows(rows, status_of=lambda r: r.kind)
        self.assertEqual([r.rel for r in t.visible_rows()], ["b", "c", "a"])          # added, removed, modified
        t.view.horizontalHeader().setSortIndicator(2, Qt.DescendingOrder)               # click "Size"
        self.assertEqual([r.rel for r in t.visible_rows()], ["a", "b", "c"])
        t.set_rows(list(reversed(rows)), status_of=lambda r: r.kind)                    # reload keeps the chosen sort
        self.assertEqual([r.rel for r in t.visible_rows()], ["a", "b", "c"])
        t.set_status("removed", "Removed")
        self.assertEqual([r.rel for r in t.visible_rows()], ["c"])


if __name__ == "__main__":
    unittest.main()
