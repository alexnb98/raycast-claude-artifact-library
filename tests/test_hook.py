from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import artlib  # noqa: E402
import hook  # noqa: E402
from test_artlib import write_config  # noqa: E402


def payload(file_path=None, url="https://claude.ai/artifact/AbC123", **inp):
    inp = {"file_path": file_path, **inp} if file_path else inp
    return {"hook_event_name": "PostToolUse", "tool_name": "Artifact", "tool_input": inp,
            "tool_response": f"Published {file_path} at {url}\n\nLive subscription: on",
            "cwd": "/Users/sam/code/acme/docs", "session_id": "sess-1"}


class TestHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ARTLIB_ROOT"] = str(Path(self.tmp.name) / "lib")
        self.spawned = []
        self.html = Path(self.tmp.name) / "report.html"
        self.html.write_text("<title>Boat Offers</title><h1>Yacht quotes</h1><p>catamaran</p>")
        self.env = {"CLAUDE_CODE_HOST_SESSION_ID": "local_host1"}

    def tearDown(self):
        os.environ.pop("ARTLIB_ROOT", None)
        self.tmp.cleanup()

    def run_hook(self, p):
        return hook.handle(p, self.env, spawn=self.spawned.append)

    def test_publish_registers_and_copies_page(self):
        write_config()
        out = self.run_hook(payload(str(self.html), description="Three boat offers compared"))
        a = artlib.get("AbC123")
        self.assertEqual(a["title"], "Boat Offers")
        self.assertEqual(a["about"], "Three boat offers compared")
        self.assertEqual((a["area"], a["kind"], a["publish_count"], a["type"]), ("work", "html", 1, "page"))
        self.assertEqual((a["session_id"], a["host_session_id"]), ("sess-1", "local_host1"))
        self.assertEqual(a["source_file"], str(self.html))
        self.assertTrue(artlib.files("AbC123")["page"].exists())
        self.assertEqual(self.spawned, ["AbC123"])
        self.assertIsNotNone(out)

    def test_first_publish_asks_for_tags(self):
        out = self.run_hook(payload(str(self.html)))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("set-meta AbC123", ctx)
        self.assertIn(str(SCRIPTS / "artlib.py"), ctx)

    def test_republish_no_duplicate_and_no_ask_once_tagged(self):
        self.run_hook(payload(str(self.html)))
        artlib.set_meta("AbC123", tags="boat yacht")
        out = self.run_hook(payload(str(self.html), url="https://claude.ai/artifact/AbC123"))
        self.assertIsNone(out)
        self.assertEqual(artlib.get("AbC123")["publish_count"], 2)
        self.assertEqual(len(artlib.search("")), 1)

    def test_update_by_url_uses_input_url(self):
        p = payload(str(self.html), url="https://claude.ai/artifact/Zz9", )
        p["tool_input"]["url"] = "https://claude.ai/artifact/Zz9"
        p["tool_response"] = "Updated the artifact."
        self.run_hook(p)
        self.assertIsNotNone(artlib.get("Zz9"))

    def test_ignores_read_list_asset(self):
        for inp in ({"action": "read", "url": "https://claude.ai/artifact/AbC123"},
                    {"action": "list"},
                    {"action": "publish", "asset": True, "url": "https://claude.ai/artifact/AbC123",
                     "file_path": str(self.html)}):
            p = payload()
            p["tool_input"] = inp
            self.assertIsNone(self.run_hook(p))
        self.assertIsNone(artlib.get("AbC123"))

    def test_typed_publish(self):
        j = Path(self.tmp.name) / "deck.json"
        j.write_text("{}")
        self.run_hook(payload(str(j), title="Demo Deck"))
        a = artlib.get("AbC123")
        self.assertEqual((a["kind"], a["title"], a["type"]), ("typed", "Demo Deck", "other"))
        self.assertFalse(artlib.files("AbC123")["page"].exists())

    def test_missing_file_still_registers(self):
        self.run_hook(payload("/private/tmp/gone/x.html", description="gone file"))
        self.assertEqual(artlib.get("AbC123")["about"], "gone file")

    def test_area_from_folder(self):
        write_config()
        self.run_hook(payload(str(self.html)))
        self.assertEqual(artlib.get("AbC123")["area"], "work")

    def test_no_area_without_config(self):
        self.run_hook(payload(str(self.html)))
        self.assertEqual(artlib.get("AbC123")["area"], "")

    def test_manual_area_survives_republish(self):
        write_config()
        self.run_hook(payload(str(self.html)))
        artlib.set_meta("AbC123", area="family")
        self.run_hook(payload(str(self.html)))
        self.assertEqual(artlib.get("AbC123")["area"], "family")

    def test_area_from_title(self):
        write_config()
        p = payload(str(self.html), title="Wedding budget overview")
        p["cwd"] = "/Users/sam"
        self.run_hook(p)
        self.assertEqual(artlib.get("AbC123")["area"], "family")

    def test_never_raises_on_garbage(self):
        for stdin in ("not json", "{}", '{"tool_input": null, "tool_response": 5}'):
            r = subprocess.run([sys.executable, str(SCRIPTS / "hook.py")], input=stdin, text=True,
                               capture_output=True, env={**os.environ, "ARTLIB_NO_SPAWN": "1"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
