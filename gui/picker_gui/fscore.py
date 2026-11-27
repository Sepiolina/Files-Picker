"""
Core logic for the Compare, Snapshots and Find tabs - pure Python, no Qt, so it
can be tested and reused from scripts.

    walk()              recursive scan -> per-directory records (uses the Go engine when present,
                        otherwise pure Python; optionally reuses a cache)
    hash_many()         checksums in parallel (engine or threads)
    find_duplicates()   identical files in one folder tree
    build_size_tree()   folder sizes for the Space (treemap) tab
    compare_folders()   two folders -> rows + statistics
    diff_files()        two file maps -> added / removed / modified / moved
    SnapshotStore       scan logs on disk (one gzip JSON per scan + a small index)
    SearchIndex         cached, typo-tolerant "semantic" filename search
    IndexCache          persists the index per folder and the recent queries

Every long function takes `cancel` (a threading.Event) and `progress` (a callable
taking a dict) so a worker thread can stop it and show progress.
"""

import collections
import gzip
import hashlib
import heapq
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from collections import Counter, defaultdict, namedtuple
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace

IS_WINDOWS = sys.platform == "win32"
_SYSTEM_TOP = {"$recycle.bin", "system volume information", "windows", "recovery", "$windows.~bt",
               "proc", "sys", "dev", "run", "snap", "lost+found", "system", "library", "private", "cores"}
_SYSTEM_ANY = {"$recycle.bin", "system volume information", ".trash", ".trashes", ".spotlight-v100",
               ".fseventsd"}
MTIME_TOLERANCE_NS = 2_000_000_000  # FAT stores times with 2 s resolution


class Cancelled(Exception):
    pass


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


# --------------------------------------------------------------------------- #
#  Scanning                                                                     #
# --------------------------------------------------------------------------- #

def _is_hidden(name, st):
    if name.startswith("."):
        return True
    attrs = getattr(st, "st_file_attributes", 0) if IS_WINDOWS else 0
    return bool(attrs & 0x2)  # FILE_ATTRIBUTE_HIDDEN


# ---- shared options (set by the app from its Settings dialog) ------------------

DEFAULT_IGNORE = "# One rule per line. Folders end with /\nnode_modules/\n__pycache__/\nThumbs.db\ndesktop.ini\n~$*\n*.tmp\n"
config = SimpleNamespace(ignore=DEFAULT_IGNORE, skip_cloud=True, use_engine=True)


def scan_signature(skip_hidden):
    """Changes whenever a setting that changes *what a scan sees* changes (invalidates caches)."""
    raw = f"{bool(skip_hidden)}|{config.ignore}|{bool(config.skip_cloud)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ---- ignore rules (same syntax and behaviour as engine/internal/ignore) -----------

class IgnoreRules:
    """
    A small .gitignore subset, case-insensitive, one rule per line:
      name/  folders only   *.tmp  wildcards * ?   /top  anchored to the scanned folder
      a/b    any rule with a / is anchored   **  any number of folders   # comment
    "!negation" is not supported.
    """

    def __init__(self, text=""):
        self.rules = []
        for line in (text or "").splitlines():
            line = line.strip().replace("\\", "/")
            if not line or line.startswith(("#", "!")):
                continue
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchor = False
            if line.startswith("/"):
                anchor, line = True, line.lstrip("/")
            elif "/" in line:
                anchor = True
            if not line:
                continue
            try:
                rx = re.compile("^" + self._translate(line) + "$", re.IGNORECASE)
            except re.error:
                continue
            self.rules.append((rx, dir_only, anchor))

    @staticmethod
    def _translate(p):
        out, i = [], 0
        while i < len(p):
            if p.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
            elif p.startswith("**", i):
                out.append(".*")
                i += 2
            elif p[i] == "*":
                out.append("[^/]*")
                i += 1
            elif p[i] == "?":
                out.append("[^/]")
                i += 1
            else:
                out.append(re.escape(p[i]))
                i += 1
        return "".join(out)

    def __bool__(self):
        return bool(self.rules)

    def match(self, rel, is_dir):
        name = rel.rsplit("/", 1)[-1]
        for rx, dir_only, anchor in self.rules:
            if dir_only and not is_dir:
                continue
            if rx.match(rel if anchor else name):
                return True
        return False


_CLOUD_WIN = 0x1000 | 0x40000 | 0x400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
_SF_DATALESS = 0x40000000                 # macOS iCloud / File Provider placeholder


def is_cloud_placeholder(name, st):
    """An online-only file: its data is not on this computer (reading it would download it)."""
    if IS_WINDOWS:
        return bool(getattr(st, "st_file_attributes", 0) & _CLOUD_WIN)
    if sys.platform == "darwin":
        return bool(getattr(st, "st_flags", 0) & _SF_DATALESS) or (name.startswith(".") and name.endswith(".icloud"))
    return False


# ---- the Go engine (optional) ---------------------------------------------------------

_ENGINE = {"checked": False, "path": None, "version": ""}
MIN_ENGINE = (2, 1)


def app_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def find_engine():
    exe = "picker.exe" if IS_WINDOWS else "picker"
    import shutil
    for c in (os.environ.get("PICKER_ENGINE"), app_dir() / "bin" / exe,
              app_dir().parent / "engine" / "bin" / exe, shutil.which("picker")):
        if c and Path(c).is_file():
            return str(c)
    return None


def _spawn_flags():
    return {"creationflags": 0x08000000} if IS_WINDOWS else {}  # CREATE_NO_WINDOW


def engine_status(refresh=False):
    """-> (usable, version, path). usable = found and new enough to have the tree/hash commands."""
    if refresh or not _ENGINE["checked"]:
        _ENGINE.update(checked=True, path=None, version="")
        exe = find_engine()
        if exe:
            try:
                out = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=10,
                                     **_spawn_flags()).stdout.strip()
                parts = tuple(int(x) for x in re.findall(r"\d+", out)[:2])
                _ENGINE.update(path=exe, version=out if parts >= MIN_ENGINE else out + " (too old)")
                if parts >= MIN_ENGINE:
                    _ENGINE["ok"] = True
                else:
                    _ENGINE["ok"] = False
            except (OSError, subprocess.SubprocessError, ValueError):
                _ENGINE["ok"] = False
        else:
            _ENGINE["ok"] = False
    return bool(_ENGINE.get("ok")), _ENGINE["version"], _ENGINE["path"]


def _engine_usable(use_engine):
    return (config.use_engine if use_engine is None else use_engine) and engine_status()[0]


CANCEL_GRACE_S = 5.0   # after "cancel", how long the engine may take to stop before it is killed


def _run_engine(command, job, cancel, on_event, extra_files=()):
    """
    Run `picker <command>`, feed it the job, call on_event(dict) per line. Raises Cancelled / RuntimeError.

    A watchdog thread delivers the cancel request independently of the output, and kills the engine if it
    does not stop within CANCEL_GRACE_S (e.g. blocked on a sleeping disk or a dead network drive), so
    Cancel can never leave the app waiting forever.
    """
    exe = engine_status()[2]
    proc = subprocess.Popen([exe, command, "--stdin-control"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **_spawn_flags())
    err_tail = collections.deque(maxlen=20)
    finished = threading.Event()

    def drain_stderr():
        try:
            for line in proc.stderr:
                err_tail.append(line.decode("utf-8", "replace").rstrip())
        except (OSError, ValueError):
            pass

    def watchdog():
        while not finished.wait(0.2):
            if cancel is not None and cancel.is_set():
                try:
                    proc.stdin.write(b"cancel\n")
                    proc.stdin.flush()
                except (OSError, ValueError):
                    pass
                if not finished.wait(CANCEL_GRACE_S):
                    try:
                        proc.kill()
                    except OSError:
                        pass
                return

    threading.Thread(target=drain_stderr, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        try:
            proc.stdin.write((json.dumps(job, ensure_ascii=False) + "\n").encode("utf-8", "replace"))
            proc.stdin.flush()
        except OSError:
            pass  # the engine died at start-up: the exit code and stderr below say why
        for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "error":
                raise RuntimeError(ev.get("message", "engine error"))
            if kind == "cancelled":
                raise Cancelled()
            on_event(ev)
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=10)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if code != 0:
            detail = " | ".join(list(err_tail)[-3:])
            raise RuntimeError(f"engine exited with code {code}" + (f": {detail}" if detail else ""))
    finally:
        finished.set()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)   # always reap the process
        except subprocess.TimeoutExpired:
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        for f in extra_files:
            try:
                os.unlink(f)
            except OSError:
                pass


NETWORK_WORKERS = 64   # folder reads in flight on a network drive: it is latency-bound, not bandwidth-bound
_NET_FS = {"nfs", "nfs4", "cifs", "smb", "smb2", "smb3", "smbfs", "sshfs", "fuse.sshfs", "9p", "afs", "ceph",
           "davfs", "fuse.rclone", "fuseblk.smb"}


def is_network_path(path):
    """True for a UNC share (\\\\server\\share), a mapped network drive, or a network mount on Linux."""
    path = os.path.abspath(path)
    try:
        if IS_WINDOWS:
            if path.startswith("\\\\"):
                return not path.startswith("\\\\?\\") or path.upper().startswith("\\\\?\\UNC\\")
            import ctypes
            drive = os.path.splitdrive(path)[0]
            return bool(drive) and ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4   # DRIVE_REMOTE
        if sys.platform.startswith("linux"):
            best, kind = "", ""
            with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) > 2:
                        mount = parts[1].replace("\\040", " ")
                        if (path == mount or path.startswith(mount.rstrip("/") + "/")) and len(mount) >= len(best):
                            best, kind = mount, parts[2]
            return kind in _NET_FS
    except (OSError, AttributeError, ValueError):
        pass
    return False


def _engine_walk(root, cancel, progress, skip_hidden, prev, ignore, skip_cloud, dir_times=True):
    known_path = None
    if prev:
        fd, known_path = tempfile.mkstemp(suffix=".jsonl", prefix="picker-known-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rel, rec in prev.items():
                f.write(json.dumps({"rel": rel, "m": rec["m"], "d": rec["d"]}, ensure_ascii=False) + "\n")
    dirs, done = {}, {}

    def on_event(ev):
        kind = ev.get("type")
        if kind == "dir":
            rel = ev["rel"]
            if ev.get("same"):
                old = prev.get(rel) if prev else None
                dirs[rel] = {"m": ev["m"], "f": old["f"] if old else [], "d": ev["d"]}
            else:
                dirs[rel] = {"m": ev["m"], "f": ev["f"], "d": ev["d"]}
        elif kind == "progress" and progress:
            progress({"files": ev.get("files", 0), "dirs": ev.get("dirs", 0), "bytes": ev.get("bytes", 0),
                      "current": ""})
        elif kind == "done":
            done.update(ev)

    job = {"root": os.path.abspath(root), "skipHidden": bool(skip_hidden), "skipCloud": bool(skip_cloud),
           "ignore": ignore or "", "known": known_path or "", "noDirTime": not dir_times,
           "workers": NETWORK_WORKERS if is_network_path(root) else 0}
    _run_engine("tree", job, cancel, on_event, [known_path] if known_path else [])
    if not done:
        raise RuntimeError("the engine did not finish")
    files = sum(len(r["f"]) for r in dirs.values())
    return {"dirs": dirs, "files": files, "bytes": sum(f[1] for r in dirs.values() for f in r["f"]),
            "errors": done.get("errors", 0), "reused": done.get("reused", 0), "cloud": done.get("cloud", 0),
            "ignored": done.get("ignored", 0), "engine": True}


# ---- scanning -----------------------------------------------------------------------------

def walk(root, cancel=None, progress=None, skip_hidden=True, prev=None, ignore=None, skip_cloud=None, use_engine=None,
         dir_times=True):
    """
    Scan `root` recursively. Returns
        {"dirs": {rel: {"m": mtime_ns, "f": [[name, size, mtime_ns], ...], "d": [subdir names]}},
         "files", "bytes", "errors", "reused", "cloud", "ignored", "engine"}
    `rel` is "" for the root and uses "/" separators on every OS.

    Uses the Go engine when it is available (much faster on big trees), else pure Python -
    same results either way. `ignore` / `skip_cloud` default to the app-wide settings.

    prev: a previous result["dirs"]. A directory whose own mtime is unchanged is not listed
    again (adding, removing or renaming an entry changes the folder's mtime).

    dir_times=False: the folders' own modified times are not needed (everything except the Find index), so the
    engine skips reading them - one network round trip less per folder. Their "m" is then 0.
    """
    ignore = config.ignore if ignore is None else ignore
    skip_cloud = config.skip_cloud if skip_cloud is None else skip_cloud
    if _engine_usable(use_engine):
        try:
            return _engine_walk(root, cancel, progress, skip_hidden, prev, ignore, skip_cloud, dir_times or bool(prev))
        except Cancelled:
            raise
        except Exception as e:  # any engine problem: carry on in Python, but say so
            result = _py_walk(root, cancel, progress, skip_hidden, prev, ignore, skip_cloud)
            result["engine_error"] = str(e) or type(e).__name__
            return result
    return _py_walk(root, cancel, progress, skip_hidden, prev, ignore, skip_cloud)


def _py_walk(root, cancel, progress, skip_hidden, prev, ignore, skip_cloud):
    rules = IgnoreRules(ignore)
    root = os.path.abspath(root)
    dirs, files, total, errors, reused, cloud, ignored = {}, 0, 0, 0, 0, 0, 0
    last = 0.0
    stack = [""]
    while stack:
        _check(cancel)
        rel = stack.pop()
        path = os.path.join(root, rel.replace("/", os.sep)) if rel else root
        try:
            dir_m = os.stat(path).st_mtime_ns
        except OSError:
            errors += 1
            continue
        old = prev.get(rel) if prev else None
        if old is not None and old["m"] == dir_m:
            rec = old
            reused += 1
        else:
            fl, dl = [], []
            try:
                with os.scandir(path) as it:
                    for e in it:
                        name = e.name
                        try:
                            if e.is_symlink():
                                continue
                            is_dir = e.is_dir(follow_symlinks=False)
                            if rules and rules.match(f"{rel}/{name}" if rel else name, is_dir):
                                ignored += 1
                                continue
                            st = e.stat(follow_symlinks=False)
                            if skip_hidden:
                                low = name.lower()
                                if _is_hidden(name, st) or low in _SYSTEM_ANY or (not rel and low in _SYSTEM_TOP):
                                    continue
                            if is_dir:
                                dl.append(name)
                            elif e.is_file(follow_symlinks=False):
                                if skip_cloud and is_cloud_placeholder(name, st):
                                    cloud += 1
                                    continue
                                fl.append([name, st.st_size, st.st_mtime_ns])
                        except OSError:
                            errors += 1
            except OSError:
                errors += 1
            fl.sort(key=lambda x: x[0])
            dl.sort()
            rec = {"m": dir_m, "f": fl, "d": dl}
        dirs[rel] = rec
        files += len(rec["f"])
        total += sum(f[1] for f in rec["f"])
        for d in reversed(rec["d"]):
            stack.append(f"{rel}/{d}" if rel else d)
        if progress:
            now = time.monotonic()
            if now - last > 0.1:
                last = now
                progress({"files": files, "dirs": len(dirs), "bytes": total, "current": path})
    if progress:
        progress({"files": files, "dirs": len(dirs), "bytes": total, "current": ""})
    return {"dirs": dirs, "files": files, "bytes": total, "errors": errors, "reused": reused,
            "cloud": cloud, "ignored": ignored, "engine": False}


def flatten(dirs, consume=False):
    """
    walk()["dirs"] -> {rel_path: (size, mtime_ns)}
    consume=True empties `dirs` while reading it, so the per-folder records are freed as the flat map grows
    (halves the peak memory on trees with hundreds of thousands of files).
    """
    out = {}
    items = _drain(dirs) if consume else dirs.items()
    for rel, rec in items:
        prefix = rel + "/" if rel else ""
        for name, size, m in rec["f"]:
            out[prefix + name] = (size, m)
    return out


def _drain(d):
    while d:
        yield d.popitem()


PARTIAL_BYTES = 64 * 1024


def file_hash(path, cancel=None, partial=0):
    """SHA-256 shortened to 32 hex chars - identical to the engine's checksum. partial>0: first N bytes only."""
    h = hashlib.sha256()
    left = partial or None
    with open(path, "rb") as f:
        while True:
            chunk = f.read(min(1 << 20, left) if left else 1 << 20)
            if not chunk:
                break
            _check(cancel)
            h.update(chunk)
            if left:
                left -= len(chunk)
                if left <= 0:
                    break
    return h.hexdigest()[:32]


def hash_many(root, rels, cancel=None, progress=None, workers=4, partial=0, use_engine=None):
    """Hash many files in parallel. Returns {rel: hexdigest or None (unreadable)}."""
    if not rels:
        return {}
    out = {}
    if _engine_usable(use_engine):
        try:
            _engine_hash(root, rels, cancel, progress, partial, out)
        except Cancelled:
            raise
        except Exception:
            pass  # keep the checksums already received; only the rest is redone below
    missing = [r for r in rels if r not in out]
    if missing:
        done = len(rels) - len(missing)
        shifted = (lambda p: progress({**p, "hashed": done + p.get("hashed", 0), "hash_total": len(rels)})) \
            if progress else None
        out.update(_py_hash(root, missing, cancel, shifted, workers, partial))
    return out


def _engine_hash(root, rels, cancel, progress, partial, out):

    def on_event(ev):
        if ev.get("type") == "hashes":
            out.update({k: (v or None) for k, v in ev["h"].items()})
        elif ev.get("type") == "progress" and progress:
            progress({"hashed": ev.get("files", 0), "hash_total": len(rels), "current": ""})

    _run_engine("hash", {"root": os.path.abspath(root), "files": list(rels), "partial": partial, "workers": 4},
                cancel, on_event)


def _py_hash(root, rels, cancel, progress, workers, partial):
    out, done = {}, 0
    lock = threading.Lock()
    last = [0.0]

    def one(rel):
        nonlocal done
        _check(cancel)
        try:
            digest = file_hash(os.path.join(root, rel.replace("/", os.sep)), cancel, partial)
        except OSError:
            digest = None
        with lock:
            out[rel] = digest
            done += 1
            now = time.monotonic()
            if progress and now - last[0] > 0.1:
                last[0] = now
                progress({"hashed": done, "hash_total": len(rels), "current": rel})

    with ThreadPoolExecutor(workers) as ex:
        futures = [ex.submit(one, r) for r in rels]
        try:
            for f in futures:
                f.result()
        except BaseException:
            for f in futures:
                f.cancel()
            raise
    if progress:
        progress({"hashed": done, "hash_total": len(rels), "current": ""})
    return out


def ext_of(rel):
    name = rel.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name.strip(".") else ""


# --------------------------------------------------------------------------- #
#  Compare two folders                                                          #
# --------------------------------------------------------------------------- #

Row = namedtuple("Row", "status rel rel_a rel_b size_a size_b m_a m_b ext top")
STATUSES = ("same", "diff", "moved", "a_only", "b_only")
COMPARE_MODES = ("size", "date", "content")


def _pair_keys(files, by_name):
    """key -> rel. With by_name, folders are ignored and duplicates get an index."""
    out, seen = {}, defaultdict(int)
    for rel in sorted(files):
        key = rel.rsplit("/", 1)[-1] if by_name else rel
        key = key.casefold()
        if by_name:
            i = seen[key]
            seen[key] += 1
            key = f"{key}\0{i}"
        out[key] = rel
    return out


class _AnyEvent:
    """Looks like a threading.Event that is set when any of the given ones is."""

    def __init__(self, *events):
        self.events = [e for e in events if e is not None]

    def is_set(self):
        return any(e.is_set() for e in self.events)


def _walk_both(a, b, cancel, progress, skip_hidden):
    """
    Scan two folders at the same time. Each scan spends most of its time waiting (on a network drive, mostly for
    replies), so running both together takes about as long as the slower one instead of the sum of the two.
    Returns (walk of a, walk of b).
    """
    stop = threading.Event()               # one side failed: stop the other
    either = _AnyEvent(cancel, stop)
    results, errors = {}, {}
    state, lock = {"A": {}, "B": {}}, threading.Lock()

    def report(side):
        def cb(p):
            if not progress:
                return
            with lock:
                state[side] = p
                both = state["A"], state["B"]
            progress({"phase": "Scanning both folders…", "current": "",
                      "files": sum(x.get("files", 0) for x in both), "dirs": sum(x.get("dirs", 0) for x in both),
                      "bytes": sum(x.get("bytes", 0) for x in both)})
        return cb

    def run(side, root):
        try:
            results[side] = walk(root, either, report(side), skip_hidden, dir_times=False)
        except BaseException as e:  # noqa: BLE001 - handed back to the caller's thread below
            errors[side] = e
            stop.set()

    other = threading.Thread(target=run, args=("B", b), daemon=True)
    other.start()
    run("A", a)
    other.join()
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    for side in ("A", "B"):   # a real failure beats the Cancelled it caused in the other side
        if side in errors and not isinstance(errors[side], Cancelled):
            raise errors[side]
    return results["A"], results["B"]


def compare_folders(a, b, mode="size", by_name=False, skip_hidden=True, cancel=None, progress=None):
    """
    Compare folder `a` with folder `b`.
    mode: size (fast) | date (size + modified time) | content (hash files whose sizes match)
    by_name: ignore sub-folders and pair files by name only (for differently organised copies)
    """
    def phase(text):
        _check(cancel)
        if progress:
            progress({"phase": text})

    t0 = time.monotonic()
    wa, wb = _walk_both(a, b, cancel, progress, skip_hidden)
    phase("Matching files…")
    fa, fb = flatten(wa.pop("dirs"), consume=True), flatten(wb.pop("dirs"), consume=True)
    ka, kb = _pair_keys(fa, by_name), _pair_keys(fb, by_name)
    _check(cancel)

    pairs, only_a, only_b = [], [], []
    for key, ra in ka.items():
        rb = kb.get(key)
        (pairs.append((ra, rb)) if rb is not None else only_a.append(ra))
    only_b = [rb for key, rb in kb.items() if key not in ka]

    hashes_a, hashes_b = {}, {}
    if mode == "content":
        need = [(ra, rb) for ra, rb in pairs if fa[ra][0] == fb[rb][0] and fa[ra][0] > 0]
        phase("Reading file contents…")
        hashes_a = hash_many(a, [p[0] for p in need], cancel, progress)
        hashes_b = hash_many(b, [p[1] for p in need], cancel, progress)

    rows = []
    phase("Comparing…")

    def top_of(rel):
        return rel.split("/", 1)[0] if "/" in rel else "(top level)"

    for ra, rb in pairs:
        (sa, ma), (sb, mb) = fa[ra], fb[rb]
        if sa != sb:
            status = "diff"
        elif mode == "date":
            status = "same" if abs(ma - mb) <= MTIME_TOLERANCE_NS else "diff"
        elif mode == "content" and sa > 0:
            ha, hb = hashes_a.get(ra), hashes_b.get(rb)
            status = "same" if (ha and ha == hb) else "diff"
        else:
            status = "same"
        rows.append(Row(status, ra, ra, rb, sa, sb, ma, mb, ext_of(ra), top_of(ra)))

    # Same file under a different folder -> "moved" (only meaningful when folders matter)
    if not by_name:
        index = defaultdict(list)
        for rb in only_b:
            index[(rb.rsplit("/", 1)[-1].casefold(), fb[rb][0])].append(rb)
        still_a = []
        for ra in only_a:
            bucket = index.get((ra.rsplit("/", 1)[-1].casefold(), fa[ra][0]))
            if bucket:
                rb = bucket.pop(0)
                (sa, ma), (sb, mb) = fa[ra], fb[rb]
                rows.append(Row("moved", f"{ra}  →  {rb}", ra, rb, sa, sb, ma, mb, ext_of(ra), top_of(ra)))
            else:
                still_a.append(ra)
        only_a = still_a
        only_b = [rb for bucket in index.values() for rb in bucket]

    for ra in only_a:
        s, m = fa[ra]
        rows.append(Row("a_only", ra, ra, "", s, None, m, None, ext_of(ra), top_of(ra)))
    for rb in only_b:
        s, m = fb[rb]
        rows.append(Row("b_only", rb, "", rb, None, s, None, m, ext_of(rb), top_of(rb)))

    phase("Summarising…")
    stats = compare_stats(rows)
    stats.update({"files_a": wa["files"], "files_b": wb["files"], "bytes_a": wa["bytes"], "bytes_b": wb["bytes"],
                  "errors": wa["errors"] + wb["errors"], "elapsed": time.monotonic() - t0,
                  "cloud": wa["cloud"] + wb["cloud"], "ignored": wa["ignored"] + wb["ignored"],
                  "engine": wa["engine"],
                  "mode": mode, "by_name": by_name, "a": a, "b": b})
    return {"rows": rows, "stats": stats}


SIZE_BUCKETS = [("< 1 KB", 1 << 10), ("1–100 KB", 100 << 10), ("100 KB–1 MB", 1 << 20),
                ("1–10 MB", 10 << 20), ("10–100 MB", 100 << 20), ("> 100 MB", None)]


def size_bucket(n):
    for label, limit in SIZE_BUCKETS:
        if limit is None or n < limit:
            return label


def compare_stats(rows):
    counts, sizes = Counter(), Counter()
    by_ext, by_top, by_size = (defaultdict(Counter) for _ in range(3))
    for r in rows:
        counts[r.status] += 1
        size = r.size_a if r.size_a is not None else (r.size_b or 0)
        sizes[r.status] += size
        by_ext[r.ext or "(none)"][r.status] += 1
        by_top[r.top][r.status] += 1
        by_size[size_bucket(size)][r.status] += 1
    total = len(rows)
    return {"counts": dict(counts), "bytes": dict(sizes), "total": total,
            "similarity": (counts["same"] / total * 100) if total else 100.0,
            "by_ext": {k: dict(v) for k, v in by_ext.items()},
            "by_top": {k: dict(v) for k, v in by_top.items()},
            "by_size": {k: dict(v) for k, v in by_size.items()}}


# --------------------------------------------------------------------------- #
#  Snapshots (scan logs) and their differences                                  #
# --------------------------------------------------------------------------- #

def diff_files(old, new):
    """
    old / new: {rel: (size, mtime_ns, hash_or_None)}.
    Returns {"added": [...], "removed": [...], "modified": [...], "moved": [(old, new)...], "unchanged": n}
    A file is "moved" when a removed and an added file have the same content hash,
    or - without hashes - the same name, size and modified time.
    """
    added = [r for r in new if r not in old]
    removed = [r for r in old if r not in new]
    modified, unchanged = [], 0
    for rel, (s1, m1, h1) in old.items():
        n = new.get(rel)
        if n is None:
            continue
        s2, m2, h2 = n
        if h1 and h2:
            changed = h1 != h2
        else:
            changed = s1 != s2 or abs(m1 - m2) > MTIME_TOLERANCE_NS
        (modified.append(rel) if changed else None)
        unchanged += 0 if changed else 1

    def key(rec, rel):
        s, m, h = rec
        return ("h", h) if h else ("n", rel.rsplit("/", 1)[-1].casefold(), s, m // 2_000_000_000)

    pool = defaultdict(list)
    for rel in added:
        pool[key(new[rel], rel)].append(rel)
    moved, really_removed = [], []
    for rel in removed:
        bucket = pool.get(key(old[rel], rel))
        if bucket:
            moved.append((rel, bucket.pop(0)))
        else:
            really_removed.append(rel)
    moved_new = {n for _, n in moved}
    added = [r for r in added if r not in moved_new]
    return {"added": sorted(added), "removed": sorted(really_removed), "modified": sorted(modified),
            "moved": moved, "unchanged": unchanged}


class SnapshotStore:
    """
    One gzip-JSON file per scan under <root>/snapshots/, plus index.json with
    the small metadata used by the list (so opening the tab never loads big files).
    """

    def __init__(self, root):
        self.dir = Path(root) / "snapshots"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.dir / "index.json"

    def list(self):
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _write_index(self, metas):
        tmp = self.index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(metas, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.index_file)

    def load_files(self, snap_id):
        """-> {rel: (size, mtime_ns, hash_or_None)}"""
        try:
            with gzip.open(self.dir / f"{snap_id}.json.gz", "rt", encoding="utf-8") as f:
                data = json.load(f)
        except (EOFError, zlib.error) as e:   # a truncated / damaged file
            raise ValueError(f"the scan file is damaged ({e})") from e
        modern = data.get("meta", {}).get("algo") == "sha256"  # older scans used another checksum
        files = data["files"]
        for rel, v in files.items():   # convert in place: no second 600,000-entry dict next to the first
            files[rel] = (v[0], v[1], (v[2] if len(v) > 2 else None) if modern else None)
        return files

    def previous_for(self, root, before_time=None):
        """Newest snapshot of the same folder (older than before_time if given)."""
        same = [m for m in self.list() if _same_path(m["root"], root)
                and (before_time is None or m["time"] < before_time)]
        return max(same, key=lambda m: m["time"]) if same else None

    def scan_and_save(self, root, hashed=False, skip_hidden=True, cancel=None, progress=None):
        """Scan `root`, save it, and diff against the previous scan of that folder."""
        def phase(text):
            if progress:
                progress({"phase": text})

        t0 = time.monotonic()
        root = os.path.abspath(root)
        result = walk(root, cancel, lambda p: progress and progress({**p, "phase": "Scanning…"}), skip_hidden,
                      dir_times=False)
        n_dirs, n_bytes = len(result["dirs"]), result["bytes"]
        phase("Organising…")
        flat = flatten(result.pop("dirs"), consume=True)   # frees the folder records as it goes
        _check(cancel)
        prev_meta = self.previous_for(root)
        prev_files = None
        if prev_meta:
            phase("Loading the previous scan…")
            try:
                prev_files = self.load_files(prev_meta["id"])
            except (OSError, ValueError):
                prev_files = None
        _check(cancel)
        hashes = {}
        if hashed:
            reuse = {}
            todo = []
            for rel, (s, m) in flat.items():
                old = prev_files.get(rel) if prev_files else None
                if old and old[2] and old[0] == s and old[1] == m:
                    reuse[rel] = old[2]  # unchanged since the last scan: no need to read it again
                else:
                    todo.append(rel)
            todo.sort()   # folder by folder: far fewer disk seeks on a spinning drive
            hashes = reuse
            hashes.update(hash_many(root, todo, cancel, lambda p: progress and progress({**p, "phase": "Reading file contents…"})))
        files = {rel: (s, m, hashes.get(rel)) for rel, (s, m) in flat.items()}
        del flat, hashes
        _check(cancel)

        phase("Comparing with the previous scan…")
        changes = diff_files(prev_files, files) if prev_files is not None else None
        snap_id = uuid.uuid4().hex[:12]
        meta = {"id": snap_id, "root": root, "time": time.time(), "files": len(files), "bytes": n_bytes,
                "dirs": n_dirs, "hashed": bool(hashed), "errors": result["errors"],
                "algo": "sha256", "cloud": result["cloud"], "ignored": result["ignored"],
                "engine": result["engine"], "engine_error": result.get("engine_error"),
                "elapsed": time.monotonic() - t0, "prev": prev_meta["id"] if prev_meta else None,
                "summary": ({k: (len(v) if isinstance(v, list) else v) for k, v in changes.items()}
                            if changes else None)}
        phase("Saving…")
        tmp = self.dir / f"{snap_id}.tmp"
        try:
            _write_snapshot(tmp, meta, files)
            _check(cancel)
            os.replace(tmp, self.dir / f"{snap_id}.json.gz")
        finally:
            try:
                tmp.unlink()   # only exists if something above failed
            except OSError:
                pass
        self._write_index(self.list() + [meta])
        return {"meta": meta, "changes": changes, "files": files, "prev_files": prev_files}

    def delete(self, snap_id):
        try:
            (self.dir / f"{snap_id}.json.gz").unlink()
        except OSError:
            pass
        self._write_index([m for m in self.list() if m["id"] != snap_id])


def _jstr(text):
    """A JSON string literal. File names that are not valid Unicode (possible on Linux) are escaped so they
    can still be written and read back unchanged."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(text)
    return json.dumps(text, ensure_ascii=False)


def _write_snapshot(path, meta, files, chunk=5000):
    """
    Stream the scan to disk, one file per line, in blocks. Same JSON as before ({"meta":..., "files":{...}}) but
    without building a second 600,000-entry structure in memory just to serialise it.
    """
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=3, newline="\n") as f:
        f.write('{"meta":' + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + ',\n"files":{\n')
        block, first = [], True
        for rel, (size, m, h) in files.items():
            block.append(f'{_jstr(rel)}:[{size},{m},{_jstr(h) if h else "null"}]')
            if len(block) >= chunk:
                f.write(("" if first else ",\n") + ",\n".join(block))
                block, first = [], False
        if block:
            f.write(("" if first else ",\n") + ",\n".join(block))
        f.write("\n}}\n")


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# --------------------------------------------------------------------------- #
#  Duplicate finder                                                             #
# --------------------------------------------------------------------------- #

def find_duplicates(root, min_size=1, skip_hidden=True, cancel=None, progress=None):
    """
    Find files with identical content under `root`.

    Cheap checks first, so most files are never read in full:
      1. group by size            (a file with a unique size cannot have a twin)
      2. checksum of the first 64 KB
      3. checksum of the whole file, only for the files that still match
    Returns {"groups": [{"size", "hash", "files": [(rel, mtime_ns)]}], "stats": {...}},
    groups sorted by wasted space (largest first).
    """
    t0 = time.monotonic()
    result = walk(root, cancel, lambda p: progress and progress({**p, "phase": "Scanning…"}), skip_hidden,
                  dir_times=False)
    flat = flatten(result["dirs"])
    by_size = defaultdict(list)
    for rel, (size, _m) in flat.items():
        if size >= max(1, min_size):
            by_size[size].append(rel)
    candidates = [r for rels in by_size.values() if len(rels) > 1 for r in rels]

    def staged(rels, partial, phase):
        return hash_many(root, rels, cancel,
                         lambda p: progress and progress({**p, "phase": phase.format(n=len(rels))}), partial=partial)

    quick = staged(candidates, PARTIAL_BYTES, "Quick-checking {n:,} files…") if candidates else {}
    buckets = defaultdict(list)
    for rel in candidates:
        if quick.get(rel):
            buckets[(flat[rel][0], quick[rel])].append(rel)
    full = {}
    need = [r for (size, _), rels in buckets.items() if len(rels) > 1 and size > PARTIAL_BYTES for r in rels]
    if need:
        full = staged(need, 0, "Confirming {n:,} files…")
    groups = defaultdict(list)
    for (size, qhash), rels in buckets.items():
        if len(rels) < 2:
            continue
        for rel in rels:
            digest = full.get(rel) if size > PARTIAL_BYTES else qhash
            if digest:
                groups[(size, digest)].append(rel)
    out = [{"size": size, "hash": digest, "files": [(r, flat[r][1]) for r in sorted(rels)]}
           for (size, digest), rels in groups.items() if len(rels) > 1]
    out.sort(key=lambda g: -g["size"] * (len(g["files"]) - 1))
    extra = sum(len(g["files"]) - 1 for g in out)
    wasted = sum(g["size"] * (len(g["files"]) - 1) for g in out)
    by_ext = defaultdict(int)
    for g in out:
        by_ext[ext_of(g["files"][0][0]) or "(none)"] += g["size"] * (len(g["files"]) - 1)
    return {"groups": out, "stats": {
        "files_scanned": result["files"], "bytes_scanned": result["bytes"], "groups": len(out),
        "extra_copies": extra, "wasted": wasted, "by_ext": dict(by_ext), "root": os.path.abspath(root),
        "elapsed": time.monotonic() - t0, "cloud": result["cloud"], "ignored": result["ignored"],
        "engine": result["engine"], "errors": result["errors"]}}


# --------------------------------------------------------------------------- #
#  Folder sizes (treemap)                                                       #
# --------------------------------------------------------------------------- #

class Node:
    """A folder in the size tree. `size` / `count` include everything below it."""
    __slots__ = ("name", "rel", "size", "count", "children", "files", "parent")

    def __init__(self, name, rel, parent=None):
        self.name, self.rel, self.parent = name, rel, parent
        self.size = self.count = 0
        self.children = {}
        self.files = []        # [(name, size, mtime_ns)] directly in this folder

    def path_parts(self):
        node, parts = self, []
        while node.parent is not None:
            parts.append(node)
            node = node.parent
        return parts[::-1]


def build_size_tree(dirs, root_name="."):
    """walk()["dirs"] -> root Node with sizes and file counts rolled up."""
    nodes = {"": Node(root_name, "")}
    for rel in sorted(dirs, key=lambda r: r.count("/") if r else -1):
        node = nodes.get(rel)
        if node is None:
            parent_rel, _, name = rel.rpartition("/")
            node = nodes[rel] = Node(name, rel, nodes.get(parent_rel))
            if node.parent is not None:
                node.parent.children[name] = node
        node.files = [tuple(f) for f in dirs[rel]["f"]]
        node.size = sum(f[1] for f in node.files)
        node.count = len(node.files)
    for rel in sorted(nodes, key=lambda r: -(r.count("/") if r else -1)):
        node = nodes[rel]
        if node.parent is not None:
            node.parent.size += node.size
            node.parent.count += node.count
    return nodes[""]


def scan_sizes(root, skip_hidden=True, cancel=None, progress=None):
    """Scan `root` and return {"root": Node (sizes rolled up), "files", "bytes", ...} for the Space tab."""
    t0 = time.monotonic()
    result = walk(root, cancel, lambda p: progress and progress({**p, "phase": "Measuring…"}), skip_hidden,
                  dir_times=False)
    tree = build_size_tree(result["dirs"], os.path.basename(os.path.abspath(root).rstrip("/\\")) or os.path.abspath(root))
    return {"root": tree, "path": os.path.abspath(root), "files": result["files"], "bytes": result["bytes"],
            "dirs": len(result["dirs"]), "errors": result["errors"], "cloud": result["cloud"],
            "ignored": result["ignored"], "engine": result["engine"], "elapsed": time.monotonic() - t0}


# --------------------------------------------------------------------------- #
#  Semantic-ish filename search                                                 #
# --------------------------------------------------------------------------- #
# No machine-learning model is needed: names are split into words, reduced to a
# stem, expanded with synonyms and file-type concepts ("photo" -> jpg, png...),
# and typos are tolerated through character-trigram similarity on the *word
# list* (small), not on every file (huge).

EXT_GROUPS = {
    "images": "jpg jpeg png gif bmp tif tiff webp heic heif svg raw cr2 nef arw dng ico psd ai",
    "documents": "doc docx odt rtf txt md pdf pages tex epub mobi",
    "spreadsheets": "xls xlsx xlsm ods csv tsv numbers",
    "presentations": "ppt pptx odp key",
    "audio": "mp3 wav flac aac ogg m4a wma aiff opus mid midi",
    "video": "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp",
    "archives": "zip rar 7z tar gz tgz bz2 xz iso dmg cab",
    "code": "py js ts jsx tsx java c cpp h hpp cs go rs rb php swift kt sh bat ps1 html css json xml yaml yml sql",
    "ebooks": "epub mobi azw3 pdf djvu",
    "fonts": "ttf otf woff woff2",
    "databases": "db sqlite sqlite3 mdb accdb sql bak",
}
EXT_GROUPS = {k: set(v.split()) for k, v in EXT_GROUPS.items()}
GROUP_WORDS = {
    "images": "photo photos picture pictures image images img pic pics screenshot screenshots wallpaper icon logo",
    "documents": "document documents doc docs text letter essay paper notes note word",
    "spreadsheets": "spreadsheet spreadsheets excel sheet sheets table budget ledger",
    "presentations": "presentation presentations slides slide deck powerpoint pitch",
    "audio": "audio music song songs sound sounds podcast recording track tracks",
    "video": "video videos movie movies film films clip clips footage recording",
    "archives": "archive archives compressed zip backup backups",
    "code": "code source script scripts program programs software",
    "ebooks": "ebook ebooks book books",
    "fonts": "font fonts typeface",
    "databases": "database databases",
}
GROUP_WORDS = {w: g for g, ws in GROUP_WORDS.items() for w in ws.split()}
SYNONYMS = [set(s.split()) for s in (
    "invoice bill receipt statement payment",
    "resume cv curriculum",
    "contract agreement lease deal",
    "picture photo image pic img",
    "report summary review",
    "budget finance financial expense expenses",
    "tax taxes return",
    "presentation slides deck ppt",
    "screenshot screen capture snip",
    "backup bak archive copy",
    "photo photos holiday vacation trip travel",
    "doc document file",
    "song music track audio",
    "movie film video",
    "note notes memo",
    "todo task tasks checklist",
    "cert certificate diploma",
    "id passport license",
)]
_SYN = defaultdict(set)
for _group in SYNONYMS:
    for _w in _group:
        _SYN[_w] |= _group - {_w}

_WORD = re.compile(r"[^\W\d_]+|\d+")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def words(text):
    return [w.lower() for w in _WORD.findall(_CAMEL.sub(" ", text))]


def stem(w):
    if len(w) <= 3 or w.isdigit():
        return w
    for suffix, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)] + repl
    return w


def _trigrams(w):
    w = f"^{w}$"
    return {w[i:i + 3] for i in range(len(w) - 2)}


SearchHit = namedtuple("SearchHit", "rel size mtime score")


class SearchIndex:
    """In-memory inverted index over file names (weighted x2) and folder names (x1)."""

    def __init__(self, dirs, root="", stamp=0.0):
        self.root, self.stamp = root, stamp
        self.rels, self.sizes, self.mtimes, self.exts = [], [], [], []
        self.dir_ranges = []                      # per directory: (first doc, end doc)
        name_post = defaultdict(list)             # word -> [doc ids]
        dir_post = defaultdict(list)              # word -> [dir ids]
        ext_docs = defaultdict(list)
        stem_of = {}
        for rel in sorted(dirs):
            rec = dirs[rel]
            start = len(self.rels)
            prefix = rel + "/" if rel else ""
            for name, size, m in rec["f"]:
                doc = len(self.rels)
                self.rels.append(prefix + name)
                self.sizes.append(size)
                self.mtimes.append(m)
                ext = name.rsplit(".", 1)[-1].lower() if "." in name.strip(".") else ""
                self.exts.append(ext)
                if ext:
                    ext_docs[ext].append(doc)
                seen = set()
                for w in words(name):
                    if w not in seen:
                        seen.add(w)
                        name_post[w].append(doc)
            if len(self.rels) > start:
                dir_id = len(self.dir_ranges)
                self.dir_ranges.append((start, len(self.rels)))
                for w in set(words(rel)):
                    dir_post[w].append(dir_id)
        self.n = len(self.rels)
        self.name_post, self.dir_post, self.ext_docs = dict(name_post), dict(dir_post), dict(ext_docs)
        self.vocab = set(self.name_post) | set(self.dir_post)
        self.stems = defaultdict(set)
        for w in self.vocab:
            self.stems[stem(w)].add(w)
        self.tri = defaultdict(set)
        for w in self.vocab:
            if not w.isdigit():
                for t in _trigrams(w):
                    self.tri[t].add(w)

    # ---- query expansion ------------------------------------------------------

    def _df(self, w):
        return len(self.name_post.get(w, ())) + sum(self.dir_ranges[d][1] - self.dir_ranges[d][0]
                                                    for d in self.dir_post.get(w, ())[:50])

    def _idf(self, w):
        return math.log(1 + self.n / (1 + self._df(w)))

    def expand(self, q, smart=True):
        """One query word -> {vocabulary word: weight}"""
        out = {}
        if q in self.vocab:
            out[q] = 1.0
        if not smart:
            return out
        for w in self.stems.get(stem(q), ()):
            out.setdefault(w, 0.9)
        for syn in _SYN.get(q, set()) | _SYN.get(stem(q), set()):
            if syn in self.vocab:
                out.setdefault(syn, 0.7)
        if len(q) >= 3:
            for w in self.vocab:  # prefix / substring: "invo" -> "invoice"
                if w not in out and (w.startswith(q) or (len(q) >= 4 and q in w)):
                    out[w] = 0.75 if w.startswith(q) else 0.6
                    if len(out) > 400:
                        break
        if len(q) >= 4 and not q.isdigit():  # typo tolerance: "recipt" -> "receipt"
            tq = _trigrams(q)
            cands = Counter(w for t in tq for w in self.tri.get(t, ()))
            for w, shared in cands.items():
                if w in out:
                    continue
                dice = 2 * shared / (len(tq) + len(_trigrams(w)))
                if dice >= 0.55:
                    out[w] = 0.8 * dice
        return out

    # ---- searching -------------------------------------------------------------

    def search(self, query, smart=True, ext_filter=None, limit=500):
        """-> list[SearchHit], best first"""
        query = query.strip()
        if not query or not self.n:
            return []
        allowed = ext_filter if ext_filter else None
        if not smart or any(c in query for c in "*?"):
            return self._exact(query, allowed, limit)
        qwords = list(dict.fromkeys(words(query)))
        if not qwords:
            return []
        total, cover = defaultdict(float), defaultdict(int)
        for q in qwords:
            local = {}
            for w, weight in self.expand(q, True).items():
                idf = self._idf(w) * weight
                for d in self.name_post.get(w, ()):
                    if idf * 2 > local.get(d, 0):
                        local[d] = idf * 2
                for dir_id in self.dir_post.get(w, ()):
                    a, b = self.dir_ranges[dir_id]
                    for d in range(a, b):
                        if idf > local.get(d, 0):
                            local[d] = idf
            group = GROUP_WORDS.get(q) or GROUP_WORDS.get(stem(q))
            if group:  # "photos" also finds IMG_0001.jpg: file-type concept
                for ext in EXT_GROUPS[group]:
                    for d in self.ext_docs.get(ext, ()):
                        local[d] = max(local.get(d, 0), 1.2)
            for d, s in local.items():
                total[d] += s
                cover[d] += 1
        joined = "".join(qwords)
        n_q = len(qwords)
        scored = []
        for d, s in total.items():
            if allowed and self.exts[d] not in allowed:
                continue
            score = s * (0.35 + 0.65 * cover[d] / n_q)
            name = self.rels[d].rsplit("/", 1)[-1].lower()
            if joined in "".join(words(name)):
                score += 3.0
            scored.append((score, d))
        return [SearchHit(self.rels[d], self.sizes[d], self.mtimes[d], s)
                for s, d in heapq.nlargest(limit, scored)]

    def _exact(self, query, allowed, limit):
        q = query.lower()
        hits = []
        if "*" in q or "?" in q:
            for d, rel in enumerate(self.rels):
                if allowed and self.exts[d] not in allowed:
                    continue
                if fnmatchcase(rel.rsplit("/", 1)[-1].lower(), q):
                    hits.append(SearchHit(rel, self.sizes[d], self.mtimes[d], 1.0))
                    if len(hits) >= limit:
                        break
            return hits
        parts = q.split()
        for d, rel in enumerate(self.rels):
            if allowed and self.exts[d] not in allowed:
                continue
            low = rel.lower()
            if all(p in low for p in parts):
                name = low.rsplit("/", 1)[-1]
                score = 2.0 if all(p in name for p in parts) else 1.0
                hits.append(SearchHit(rel, self.sizes[d], self.mtimes[d], score))
        hits.sort(key=lambda h: (-h.score, len(h.rel)))
        return hits[:limit]


class IndexCache:
    """
    Persists the scanned directory records per folder (so reopening the app or
    refreshing only re-reads folders that changed) and remembers recent queries
    with their results (so repeating a search is instant, even after a restart).
    """

    MAX_QUERIES = 60

    def __init__(self, root):
        self.dir = Path(root) / "search_cache"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, folder):
        return hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()[:16]

    def load(self, folder):
        path = self.dir / f"{self._key(folder)}.json.gz"
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            return data if data.get("version") == 1 else None
        except (OSError, ValueError):
            return None

    def save(self, folder, dirs, sig, stamp):
        path = self.dir / f"{self._key(folder)}.json.gz"
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=3) as f:
            json.dump({"version": 1, "folder": os.path.abspath(folder), "sig": sig,
                       "stamp": stamp, "dirs": dirs}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        self.clear_queries(folder)  # old results belong to the old index

    def drop(self, folder):
        for p in (self.dir / f"{self._key(folder)}.json.gz", self.dir / f"{self._key(folder)}.queries.json"):
            try:
                p.unlink()
            except OSError:
                pass

    # ---- query results ---------------------------------------------------------

    def _qfile(self, folder):
        return self.dir / f"{self._key(folder)}.queries.json"

    def load_queries(self, folder, stamp):
        try:
            data = json.loads(self._qfile(folder).read_text(encoding="utf-8"))
            return data["items"] if data.get("stamp") == stamp else {}
        except (OSError, ValueError, KeyError):
            return {}

    def save_queries(self, folder, stamp, items):
        recent = dict(list(items.items())[-self.MAX_QUERIES:])
        try:
            self._qfile(folder).write_text(json.dumps({"stamp": stamp, "items": recent}, ensure_ascii=False),
                                           encoding="utf-8")
        except OSError:
            pass

    def clear_queries(self, folder):
        try:
            self._qfile(folder).unlink()
        except OSError:
            pass


def build_index(folder, cache, cancel=None, progress=None, skip_hidden=True, full=False):
    """
    Load the cached scan and bring it up to date (only changed folders are
    re-read unless full=True), then build the in-memory index.
    Returns (SearchIndex, info) with info = {"reused", "dirs", "from_cache", "elapsed"}.
    """
    t0 = time.monotonic()
    old = None if full else cache.load(folder)
    sig = scan_signature(skip_hidden)
    if old and old.get("sig") != sig:   # options (hidden / ignore rules / cloud) changed: rescan fully
        old = None
    result = walk(folder, cancel, lambda p: progress and progress({**p, "phase": "Indexing…"}), skip_hidden,
                  prev=old["dirs"] if old else None)
    stamp = time.time()
    changed = result["reused"] != len(result["dirs"]) or not old
    if changed:
        cache.save(folder, result["dirs"], sig, stamp)
    else:
        stamp = old["stamp"]  # nothing changed: keep the stamp so cached queries stay valid
    if progress:
        progress({"phase": "Building search index…", "files": result["files"]})
    index = SearchIndex(result["dirs"], folder, stamp)
    return index, {"reused": result["reused"], "dirs": len(result["dirs"]), "files": result["files"],
                   "from_cache": bool(old), "changed": changed, "errors": result["errors"],
                   "cloud": result["cloud"], "ignored": result["ignored"], "engine": result["engine"],
                   "elapsed": time.monotonic() - t0, "stamp": stamp}
