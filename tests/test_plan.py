from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import artlib  # noqa: E402


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ARTLIB_ROOT"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("ARTLIB_ROOT", None)
        self.tmp.cleanup()

    def test_plan_parses_json_in_prose(self):
        (Path(self.tmp.name) / "config.json").write_text('{"categories": [{"name": "work"}]}')
        out = 'Sure!\n{"keywords": "badge experiment swipe deck", "category": "work", "type": "design"}'
        p = artlib.plan("the thing for work with badges and the swipe deck", run=lambda prompt: out)
        self.assertEqual(p, {"keywords": "badge experiment swipe deck", "category": "work", "type": "design"})

    def test_plan_rejects_unknown_values(self):
        out = '{"keywords": "x\\"); drop", "category": "personal", "type": "pdf"}'
        p = artlib.plan("q", run=lambda prompt: out)
        self.assertEqual((p["keywords"], p["category"], p["type"]), ("x drop", "all", "all"))

    def test_plan_falls_back_to_request(self):
        self.assertEqual(artlib.plan("dubai flats", run=lambda prompt: "no json")["keywords"], "dubai flats")

    def test_unconfigured_category_becomes_all(self):
        p = artlib.plan("q", run=lambda prompt: '{"keywords": "a", "category": "work", "type": "all"}')
        self.assertEqual(p["category"], "all")

    def test_prompt_lists_categories(self):
        (Path(self.tmp.name) / "config.json").write_text(
            '{"categories": [{"name": "work", "description": "my job at a bakery"}]}')
        seen = []
        artlib.plan("x", run=lambda prompt: seen.append(prompt) or "{}")
        self.assertIn('"work | all"', seen[0])
        self.assertIn("my job at a bakery", seen[0])

    def test_prompt_carries_request(self):
        seen = []
        artlib.plan("badge swipe", run=lambda prompt: seen.append(prompt) or "{}")
        self.assertTrue(seen[0].endswith("badge swipe"))
