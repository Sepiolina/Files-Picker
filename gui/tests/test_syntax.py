"""
Every module must at least compile on the Python running the tests.

The unit tests only import the Qt-free modules, so a syntax error in a page module (for example an f-string
that is valid on Python 3.12 but not on 3.10 / 3.11) would otherwise only show up when the app starts.
CI runs this on the oldest supported Python as well as the newest.
"""
import os
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


class CompileAllTests(unittest.TestCase):
    def test_every_module_compiles(self):
        checked = 0
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "bin") and not d.startswith(".")]
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(base, name)
                    with self.subTest(module=os.path.relpath(path, ROOT)):
                        with open(path, encoding="utf-8") as f:
                            compile(f.read(), path, "exec")
                    checked += 1
        self.assertGreater(checked, 20)   # make sure the walk actually found the app


if __name__ == "__main__":
    unittest.main()
