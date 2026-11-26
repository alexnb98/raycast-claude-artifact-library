from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import artlib  # noqa: E402
import hook  # noqa: E402


class TestTyped(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        os.environ["ARTLIB_ROOT"] = str(t / "lib")
        self.canvas = t / "canvas" / "project"
        self.canvas.mkdir(parents=True)
        (self.canvas / "Main.dc.html").write_text("<html><head></head><body><h1>Mobile swipe deck</h1></body></html>")
        (self.canvas / "Badge121.dc.html").write_text("<h1>Good match badge</h1><p>match ring</p>")
        (self.canvas / "canvas.json").write_text(json.dumps({
            "title": "Talent feature uplifts", "launch": {"page": "swipe"},
            "pages": [{"id": "swipe", "name": "1 · Swipe deck flow"}, {"id": "badges", "name": "2 · Badge tests"}],
            "boards": {"Badge121.dc.html": {"page": "badges", "title": "Good match badge (EXP-121)"},
                       "Main.dc.html": {"page": "swipe", "title": "Mobile swipe deck"}},
            "order": ["Badge121.dc.html", "Main.dc.html"]}))
        self.deck = t / "deck" / "project"
        (self.deck / "slides").mkdir(parents=True)
        (self.deck / "slides" / "cover.html").write_text('<section id="cover"><h1>Top Match</h1></section>')
        (self.deck / "slides" / "plan.html").write_text('<section id="plan"><p>results table</p></section>')
        (self.deck / "deck.css").write_text("section{background:#5e2f82}")
        (self.deck / "deck.json").write_text(json.dumps({"title": "Top Match Deck", "order": ["cover", "plan"],
            "sections": {"s1": {"description": "Numbers", "start": "cover"}},
            "faces": {"x": {"family": "X", "href": "https://fonts.googleapis.com/css2?family=X"}}}))

    def tearDown(self):
        os.environ.pop("ARTLIB_ROOT", None)
        self.tmp.cleanup()

    def test_canvas_renders_launch_page_board(self):
        tp = artlib.typed_page(self.canvas)
        self.assertIn("Mobile swipe deck", tp["html"])  # launch page 'swipe', not order[0]
        self.assertIn("<base href=", tp["html"])
        self.assertIn("match ring", tp["body"])
        self.assertIn("Good match badge (EXP-121)", tp["headings"])
        self.assertEqual((tp["type"], tp["title"]), ("design", "Talent feature uplifts"))

    def test_deck_renders_cover_with_css(self):
        tp = artlib.typed_page(self.deck)
        self.assertIn('<section id="cover">', tp["html"])
        self.assertIn("section{background:#5e2f82}", tp["html"])
        self.assertIn("fonts.googleapis.com", tp["html"])
        self.assertIn("results table", tp["body"])
        self.assertEqual(tp["type"], "slides")

    def test_hook_canvas_publish_of_one_board(self):
        p = {"tool_input": {"file_path": str(self.canvas / "Badge121.dc.html"), "url": "https://claude.ai/artifact/Cv1"},
             "tool_response": "Published ... at https://claude.ai/artifact/Cv1", "cwd": "/Users/sam/code/acme"}
        hook.handle(p, {}, spawn=lambda aid: None)
        a = artlib.get("Cv1")
        self.assertEqual((a["kind"], a["type"], a["title"]), ("typed", "design", "Talent feature uplifts"))
        self.assertIn("Mobile swipe deck", artlib.files("Cv1")["page"].read_text())
        self.assertEqual([r["id"] for r in artlib.search("match ring")], ["Cv1"])

    def test_typed_renders_library_copy_not_last_file(self):
        artlib.upsert("Cv2", url="u", kind="typed", source_file=str(self.canvas / "Badge121.dc.html"),
                      page_html=artlib.typed_page(self.canvas)["html"])
        self.assertEqual(artlib._source_for_render(artlib.get("Cv2")), artlib.files("Cv2")["page"])

    def test_ingest_project_folder(self):
        artlib.upsert("Dk1", url="u", title="Deck")
        self.assertEqual(artlib.main(["ingest", "Dk1", str(self.deck)]), 0)
        self.assertEqual(artlib.get("Dk1")["type"], "slides")

    def test_asset_upload_cached_and_swapped(self):
        from PIL import Image
        png = Path(self.tmp.name) / "shot.png"
        Image.new("RGB", (2000, 1000), "red").save(png)
        resp = [{"type": "text", "text": f'Assets uploaded: 1 of 1.\n- "{png}" (9 bytes, image/png) \u2192 '
                 '"/_blob/ac458a265d0ebb9ff7a5a4dc69ed3a3b" (id ac458a265d0ebb9ff7a5a4dc69ed3a3b)'}]
        p = {"tool_input": {"action": "publish", "asset": True, "url": "https://claude.ai/artifact/Cv9", "file_paths": [str(png)]},
             "tool_response": resp}
        self.assertIsNone(hook.handle(p, {}, spawn=lambda aid: None))
        cached = artlib.asset_dir() / "ac458a265d0ebb9ff7a5a4dc69ed3a3b.png"
        self.assertEqual(Image.open(cached).size, (700, 350))
        self.assertIn(cached.as_uri(), artlib.local_blobs('<img src="/_blob/ac458a265d0ebb9ff7a5a4dc69ed3a3b">'))
        self.assertIn("/_blob/ffffffffffffffffffffffffffffffff", artlib.local_blobs('<img src="/_blob/ffffffffffffffffffffffffffffffff">'))
        self.assertIsNone(artlib.get("Cv9"))

    def test_canvas_board_scaled_to_fit(self):
        c = json.loads((self.canvas / "canvas.json").read_text())
        c["boards"]["Main.dc.html"]["w"] = 2560
        (self.canvas / "canvas.json").write_text(json.dumps(c))
        self.assertIn("html{zoom:0.5000}", artlib.typed_page(self.canvas)["html"])

    def test_broken_project_is_none(self):
        (self.canvas / "canvas.json").write_text("{not json")
        self.assertIsNone(artlib.typed_page(self.canvas))


if __name__ == "__main__":
    unittest.main()
