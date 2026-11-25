"""Tests for the Compare / Snapshots / Find logic.   python -m unittest discover gui/tests"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from picker_gui import fscore as F  # noqa: E402


def age_dirs(root, seconds=100):
    """
    Back-date every folder's modified time. Without this, a file created in the same filesystem
    timestamp tick as its folder would leave the folder's mtime unchanged and a "this folder
    changed" test could randomly fail on a fast machine.
    """
    old = time.time() - seconds
    for base, dirs, _files in os.walk(root):
        for d in dirs:
            os.utime(os.path.join(base, d), (old, old))
        os.utime(base, (old, old))


def put(root, rel, data=b"x"):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.a, self.b, self.store = (os.path.join(self.tmp, n) for n in ("a", "b", "store"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class CompareTests(Base):
    def setUp(self):
        super().setUp()
        for r in (self.a, self.b):
            put(r, "docs/same.txt", b"hello")
        put(self.a, "docs/edit.txt", b"aaaa")
        put(self.b, "docs/edit.txt", b"bbbb")          # same size, different content
        put(self.a, "only_a.txt")
        put(self.b, "only_b.txt")
        put(self.a, "old/moved.bin", b"m" * 9)
        put(self.b, "new/moved.bin", b"m" * 9)

    def counts(self, **kw):
        return F.compare_folders(self.a, self.b, **kw)["stats"]["counts"]

    def test_size_mode_cannot_see_same_size_edit(self):
        self.assertEqual(self.counts(mode="size"), {"same": 2, "a_only": 1, "b_only": 1, "moved": 1})

    def test_content_mode_sees_it(self):
        self.assertEqual(self.counts(mode="content")["diff"], 1)

    def test_by_name_ignores_folders(self):
        c = self.counts(mode="size", by_name=True)
        self.assertEqual((c["same"], c.get("moved", 0)), (3, 0))

    def test_cancel(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(F.Cancelled):
            F.compare_folders(self.a, self.b, cancel=ev)


class SnapshotTests(Base):
    def test_rescan_detects_all_change_kinds(self):
        for n in ("keep.txt", "gone.txt", "edit.txt", "mv.bin"):
            put(self.a, n, n.encode())
        store = F.SnapshotStore(self.store)
        first = store.scan_and_save(self.a, hashed=True)
        self.assertIsNone(first["changes"])
        put(self.a, "new.txt")
        os.remove(os.path.join(self.a, "gone.txt"))
        put(self.a, "edit.txt", b"changed!")
        os.makedirs(os.path.join(self.a, "sub"))
        shutil.move(os.path.join(self.a, "mv.bin"), os.path.join(self.a, "sub", "mv.bin"))
        ch = store.scan_and_save(self.a, hashed=True)["changes"]
        self.assertEqual((ch["added"], ch["removed"], ch["modified"], ch["moved"], ch["unchanged"]),
                         (["new.txt"], ["gone.txt"], ["edit.txt"], [("mv.bin", "sub/mv.bin")], 1))
        self.assertEqual(len(store.list()), 2)
        store.delete(store.list()[0]["id"])
        self.assertEqual(len(store.list()), 1)


class SearchTests(Base):
    def setUp(self):
        super().setUp()
        for rel in ("finance/Invoice_March_2024.pdf", "photos/IMG_0042.jpg", "work/resume_final.docx",
                    "work/Q3 budget.xlsx", "music/song.mp3"):
            put(self.a, rel)
        age_dirs(self.a)
        self.cache = F.IndexCache(self.store)
        self.index, _ = F.build_index(self.a, self.cache)

    def top(self, q, **kw):
        hits = self.index.search(q, **kw)
        return hits[0].rel if hits else None

    def test_smart(self):
        self.assertEqual(self.top("invoce"), "finance/Invoice_March_2024.pdf")   # typo
        self.assertEqual(self.top("bill march"), "finance/Invoice_March_2024.pdf")  # synonym
        self.assertEqual(self.top("photos"), "photos/IMG_0042.jpg")              # file-type concept
        self.assertEqual(self.top("CV"), "work/resume_final.docx")               # synonym

    def test_exact_and_wildcards(self):
        self.assertEqual(self.top("budget", smart=False), "work/Q3 budget.xlsx")
        self.assertEqual(self.top("*.mp3", smart=False), "music/song.mp3")
        self.assertIsNone(self.top("invoce", smart=False))

    def test_cache_reused_and_invalidated(self):
        _, info = F.build_index(self.a, self.cache)
        self.assertTrue(info["from_cache"] and not info["changed"])
        put(self.a, "finance/tax_2024.pdf")
        index, info = F.build_index(self.a, self.cache)
        self.assertTrue(info["changed"])
        self.assertEqual(index.search("tax")[0].rel, "finance/tax_2024.pdf")



# --------------------------------------------------------------------------- #
#  Ignore rules, duplicates, size tree, and Go-engine / Python parity           #
# --------------------------------------------------------------------------- #

class IgnoreTests(unittest.TestCase):
    def test_rules(self):
        r = F.IgnoreRules("# c\nnode_modules/\n*.TMP\n/build\ndocs/drafts\n**/cache/**\n!keep.tmp\n")
        cases = [("node_modules", True, True), ("a/b/node_modules", True, True), ("node_modules", False, False),
                 ("x/file.tmp", False, True), ("build", True, True), ("a/build", True, False),
                 ("docs/drafts", True, True), ("x/docs/drafts", True, False), ("a/cache/b.bin", False, True),
                 ("keep.tmp", False, True), ("readme.md", False, False)]
        for rel, is_dir, want in cases:
            self.assertEqual(r.match(rel, is_dir), want, rel)


class DuplicateTests(Base):
    def test_groups_and_wasted_space(self):
        big = b"B" * (F.PARTIAL_BYTES + 100)
        put(self.a, "one/a.bin", big)
        put(self.a, "two/a copy.bin", big)
        put(self.a, "three/a copy 2.bin", big)
        put(self.a, "x/same_start.bin", big[:-1] + b"Z")      # same size, same first 64 KB, different end
        put(self.a, "small1.txt", b"hello")
        put(self.a, "sub/small2.txt", b"hello")
        put(self.a, "unique.txt", b"nope!")                      # same size as "hello", different content
        put(self.a, "empty1", b"")
        put(self.a, "empty2", b"")                               # empty files are never duplicates
        res = F.find_duplicates(self.a)
        sizes = sorted((g["size"], len(g["files"])) for g in res["groups"])
        self.assertEqual(sizes, [(5, 2), (len(big), 3)])
        self.assertEqual(res["stats"]["extra_copies"], 3)
        self.assertEqual(res["stats"]["wasted"], 5 + 2 * len(big))
        self.assertEqual(res["groups"][0]["size"], len(big))     # biggest waste first


class SizeTreeTests(Base):
    def test_rollup(self):
        put(self.a, "top.txt", b"1" * 10)
        put(self.a, "d/x.bin", b"1" * 100)
        put(self.a, "d/e/y.bin", b"1" * 1000)
        root = F.build_size_tree(F.walk(self.a, use_engine=False)["dirs"], "a")
        self.assertEqual((root.size, root.count), (1110, 3))
        d = root.children["d"]
        self.assertEqual((d.size, d.count, d.children["e"].size), (1100, 2, 1000))
        self.assertEqual([n.name for n in d.children["e"].path_parts()], ["d", "e"])


class IgnoreAndCloudInScans(Base):
    def test_ignore_applies_to_walk(self):
        put(self.a, "keep.txt")
        put(self.a, "node_modules/x/dep.js")
        put(self.a, "sub/skip.tmp")
        for engine in (False, True):
            r = F.walk(self.a, ignore="node_modules/\n*.tmp", use_engine=engine)
            self.assertEqual(sorted(F.flatten(r["dirs"])), ["keep.txt"], f"engine={engine}")
            self.assertEqual(r["ignored"], 2)

    def test_cloud_detection_helper(self):
        class St:  # a fake stat result
            st_file_attributes = 0x400000
            st_flags = 0x40000000
        if F.IS_WINDOWS or sys.platform == "darwin":
            self.assertTrue(F.is_cloud_placeholder("a.docx", St()))
        else:
            self.assertFalse(F.is_cloud_placeholder("a.docx", St()))   # not a thing on Linux


@unittest.skipUnless(F.engine_status()[0], "Go engine not built (run build.sh)")
class EngineParityTests(Base):
    def setUp(self):
        super().setUp()
        for rel in ("a.txt", "docs/b.pdf", "docs/deep/c.bin", "ünï/cødé.txt", "sp ace/d e.txt", ".hidden/h.txt"):
            put(self.a, rel, rel.encode() * 3)
        age_dirs(self.a)

    def test_walk_identical(self):
        e = F.walk(self.a, use_engine=True)
        p = F.walk(self.a, use_engine=False)
        self.assertTrue(e["engine"] and not p["engine"])
        self.assertEqual(F.flatten(e["dirs"]), F.flatten(p["dirs"]))
        self.assertEqual({k: v["d"] for k, v in e["dirs"].items()}, {k: v["d"] for k, v in p["dirs"].items()})
        self.assertEqual((e["files"], e["bytes"]), (p["files"], p["bytes"]))

    def test_hashes_identical(self):
        rels = sorted(F.flatten(F.walk(self.a, use_engine=False)["dirs"]))
        for partial in (0, 10):
            self.assertEqual(F.hash_many(self.a, rels, use_engine=True, partial=partial),
                             F.hash_many(self.a, rels, use_engine=False, partial=partial))

    def test_known_dirs_reuse_and_change_detection(self):
        first = F.walk(self.a, use_engine=True)
        again = F.walk(self.a, prev=first["dirs"], use_engine=True)
        self.assertEqual(again["reused"], len(first["dirs"]))
        self.assertEqual(F.flatten(again["dirs"]), F.flatten(first["dirs"]))
        put(self.a, "docs/new.txt")
        third = F.walk(self.a, prev=first["dirs"], use_engine=True)
        self.assertIn("docs/new.txt", F.flatten(third["dirs"]))
        self.assertLess(third["reused"], len(first["dirs"]))

    def test_cancel(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(F.Cancelled):
            F.walk(self.a, ev, use_engine=True)

    def test_falls_back_when_engine_breaks(self):
        old = F._ENGINE.copy()
        try:
            F._ENGINE.update(checked=True, ok=True, path=sys.executable)   # "engine" that is not one
            r = F.walk(self.a, use_engine=True)
            self.assertFalse(r["engine"])
            self.assertEqual(r["files"], 5)
        finally:
            F._ENGINE.clear()
            F._ENGINE.update(old)


if __name__ == "__main__":
    unittest.main()
