"""Stability of the scan / hash / snapshot core on huge or misbehaving trees (no Qt needed)."""
import gzip
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from picker_gui import fscore as F  # noqa: E402

def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


POSIX = sys.platform != "win32"


def fake_engine(dirpath, body):
    """A stand-in `picker` program (shell script) so engine misbehaviour can be simulated."""
    path = os.path.join(dirpath, "picker")
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


@unittest.skipUnless(POSIX, "uses a shell-script engine")
class EngineProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = (dict(F._ENGINE), F.CANCEL_GRACE_S, os.environ.get("PICKER_ENGINE"))

    def tearDown(self):
        F._ENGINE.clear()
        F._ENGINE.update(self._saved[0])
        F.CANCEL_GRACE_S = self._saved[1]
        if self._saved[2] is None:
            os.environ.pop("PICKER_ENGINE", None)
        else:
            os.environ["PICKER_ENGINE"] = self._saved[2]
        self.tmp.cleanup()

    def use(self, body):
        os.environ["PICKER_ENGINE"] = fake_engine(self.tmp.name, body)
        F._ENGINE.update(checked=True, ok=True, path=os.environ["PICKER_ENGINE"], version="2.2.1")

    def test_cancel_kills_an_engine_that_ignores_it(self):
        self.use("exec sleep 60\n")   # never reads stdin, never prints: like a worker stuck on a dead drive
        F.CANCEL_GRACE_S = 0.5
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        t0 = time.monotonic()
        with self.assertRaises(F.Cancelled):
            F._run_engine("tree", {}, cancel, lambda ev: None)
        self.assertLess(time.monotonic() - t0, 10)

    def test_crash_reports_stderr_not_a_bare_exit_code(self):
        self.use("echo 'boom: out of memory' >&2\nexit 2\n")
        with self.assertRaises(RuntimeError) as cm:
            F._run_engine("tree", {}, None, lambda ev: None)
        self.assertIn("boom", str(cm.exception))

    def test_walk_falls_back_to_python_and_says_why(self):
        self.use("exit 3\n")
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "a.txt"), "hi")
            r = F.walk(d, use_engine=True)
        self.assertFalse(r["engine"])
        self.assertEqual(r["files"], 1)
        self.assertIn("exited with code 3", r["engine_error"])

    def test_hash_keeps_what_the_engine_finished(self):
        with tempfile.TemporaryDirectory() as d:
            rels = []
            for i in range(5):
                _write(os.path.join(d, f"f{i}"), str(i))
                rels.append(f"f{i}")
            good = F.hash_many(d, rels, use_engine=False)
            # engine reports 3 checksums, then dies
            batch = json.dumps({"type": "hashes", "h": {r: good[r] for r in rels[:3]}})
            self.use(f"cat >/dev/null &\nprintf '%s\\n' '{batch}'\nexit 1\n")
            calls = []
            real = F._py_hash
            F._py_hash = lambda root, missing, *a: (calls.append(list(missing)), real(root, missing, *a))[1]
            try:
                out = F.hash_many(d, rels, use_engine=True)
            finally:
                F._py_hash = real
            self.assertEqual(out, good)
            self.assertEqual(calls, [rels[3:]])   # only the unfinished files were re-read


class CompareTests(unittest.TestCase):
    def make(self, base, files):
        for rel, data in files.items():
            os.makedirs(os.path.dirname(os.path.join(base, rel)), exist_ok=True)
            _write(os.path.join(base, rel), data)

    def test_parallel_scan_gives_the_same_answer(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.make(a, {"same.txt": "1", "x/diff.txt": "11", "only_a.txt": "a", "m/moved.txt": "mm"})
            self.make(b, {"same.txt": "1", "x/diff.txt": "2", "only_b.txt": "b", "elsewhere/moved.txt": "mm"})
            phases = []
            r = F.compare_folders(a, b, progress=lambda p: phases.append(p.get("phase")))
            self.assertEqual(r["stats"]["counts"], {"same": 1, "diff": 1, "moved": 1, "a_only": 1, "b_only": 1})
            self.assertEqual((r["stats"]["files_a"], r["stats"]["files_b"]), (4, 4))
            for name in ("Scanning both folders…", "Matching files…", "Comparing…", "Summarising…"):
                self.assertIn(name, phases)   # the status line never goes quiet

    def test_one_side_failing_reports_the_real_error_not_a_cancel(self):
        with tempfile.TemporaryDirectory() as a:
            self.make(a, {"f.txt": "1"})
            real = F.walk

            def flaky(root, *args, **kw):
                if root != a:
                    raise RuntimeError("share went away")
                return real(root, *args, **kw)

            F.walk = flaky
            try:
                with self.assertRaises(RuntimeError) as cm:
                    F.compare_folders(a, a + "-other")
            finally:
                F.walk = real
            self.assertIn("share went away", str(cm.exception))

    def test_cancel_stops_both_scans(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.make(a, {"f.txt": "1"})
            self.make(b, {"f.txt": "1"})
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(F.Cancelled):
                F.compare_folders(a, b, cancel=cancel)


class SnapshotFileTests(unittest.TestCase):
    def test_roundtrip_with_awkward_names_and_old_format(self):
        names = ['a "quoted" name.txt', "back\\slash.txt", 'x":[1,2,3].txt', "ünï/cödé/файл.txt", "sp ace/a,b.txt"]
        files = {n: (i + 1, 1_700_000_000_000_000_000 + i, ("ab" * 16) if i % 2 else None) for i, n in enumerate(names)}
        with tempfile.TemporaryDirectory() as d:
            store = F.SnapshotStore(d)
            meta = {"id": "t1", "root": d, "time": 1.0, "algo": "sha256"}
            F._write_snapshot(store.dir / "t1.json.gz", meta, files, chunk=2)   # several blocks
            self.assertEqual(store.load_files("t1"), files)
            # the previous on-disk format (one json.dump) still loads
            with gzip.open(store.dir / "t2.json.gz", "wt", encoding="utf-8") as f:
                json.dump({"meta": meta, "files": {k: list(v) for k, v in files.items()}}, f)
            self.assertEqual(store.load_files("t2"), files)
            # a damaged file is a ValueError (which the UI reports), not a crash
            raw = (store.dir / "t1.json.gz").read_bytes()
            (store.dir / "t3.json.gz").write_bytes(raw[:len(raw) // 2])
            with self.assertRaises(ValueError):
                store.load_files("t3")

    def test_names_that_are_not_valid_unicode_can_be_saved(self):
        bad = "caf\udce9.txt"   # what Linux gives for a latin-1 name in a UTF-8 locale
        with tempfile.TemporaryDirectory() as d:
            store = F.SnapshotStore(d)
            F._write_snapshot(store.dir / "t.json.gz", {"id": "t", "algo": "sha256"}, {bad: (1, 2, None)})
            self.assertEqual(list(store.load_files("t")), [bad])

    def test_cancel_during_save_leaves_no_partial_snapshot(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src:
            for i in range(20):
                _write(os.path.join(src, f"f{i}"), "x")
            store = F.SnapshotStore(d)
            cancel = threading.Event()
            seen = []

            def progress(p):
                seen.append(p.get("phase"))
                if p.get("phase") == "Saving…":
                    cancel.set()

            with self.assertRaises(F.Cancelled):
                store.scan_and_save(src, cancel=cancel, progress=progress)
            self.assertEqual(os.listdir(store.dir), [] if not (store.dir / "index.json").exists() else ["index.json"])
            self.assertEqual(store.list(), [])


if __name__ == "__main__":
    unittest.main()
