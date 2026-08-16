from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib import draft_style, paths  # noqa: E402


def use_temp(testcase):
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    saved = paths.DRAFT_STYLE_FILE
    testcase.addCleanup(lambda: setattr(paths, "DRAFT_STYLE_FILE", saved))
    paths.DRAFT_STYLE_FILE = tmp / ".draft_style.json"


class DraftStyle(unittest.TestCase):
    def setUp(self):
        use_temp(self)

    def test_add_and_list_notes(self):
        n = draft_style.add_notes(["Keep it short.", "State exact dates."], source="x")
        self.assertEqual(n, 2)
        self.assertIn("Keep it short.", draft_style.note_texts())

    def test_add_dedups(self):
        draft_style.add_notes(["Keep it short."])
        n = draft_style.add_notes(["Keep it short.", "New one."])
        self.assertEqual(n, 1)

    def test_pause_and_reset(self):
        draft_style.add_notes(["a"])
        draft_style.set_paused(True)
        self.assertTrue(draft_style.is_paused())
        draft_style.reset()
        self.assertEqual(draft_style.status()["count"], 0)
        self.assertFalse(draft_style.is_paused())

    def test_status_shape(self):
        s = draft_style.status()
        self.assertEqual(set(s), {"notes", "paused", "count"})


if __name__ == "__main__":
    unittest.main()
