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
import backfill  # noqa: E402
from test_artlib import write_config  # noqa: E402


def line(ts, content, cwd="/Users/sam/code/acme", sid="s1"):
    return json.dumps({"timestamp": ts, "cwd": cwd, "sessionId": sid, "message": {"content": content}})


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        os.environ["ARTLIB_ROOT"] = str(t / "lib")
        self.projects = t / "projects"
        (self.projects / "p").mkdir(parents=True)
        self.page = t / "r.html"
        self.page.write_text("<title>Local Title</title><h1>Yacht day</h1>")
        rows = [
            line("2026-10-01T10:00:00Z", [{"type": "tool_use", "id": "u1", "name": "Artifact",
                                           "input": {"file_path": str(self.page), "description": "boat day"}}]),
            line("2026-10-01T10:00:01Z", [{"type": "tool_result", "tool_use_id": "u1",
                                           "content": f"Published {self.page} at https://claude.ai/artifact/Aaa111"}]),
            line("2026-10-02T10:00:00Z", [{"type": "tool_use", "id": "u2", "name": "Artifact",
                                           "input": {"file_path": "/private/tmp/gone.html"}}]),
            line("2026-10-02T10:00:01Z", [{"type": "tool_result", "tool_use_id": "u2",
                                           "content": "Published /private/tmp/gone.html at https://claude.ai/artifact/Bbb222"}]),
            line("2026-10-03T10:00:00Z", [{"type": "tool_use", "id": "u3", "name": "Artifact",
                                           "input": {"file_path": "/x.html"}}]),
            line("2026-10-03T10:00:01Z", [{"type": "tool_result", "tool_use_id": "u3",
                                           "content": "Published /x.html at https://claude.ai/code/artifact/9be26904-18f0"}]),
            line("2026-11-01T10:00:00Z", [{"type": "tool_use", "id": "u4", "name": "Artifact", "input": {"action": "list"}}]),
            line("2026-11-01T10:00:01Z", [{"type": "tool_result", "tool_use_id": "u4", "content": [{"type": "text", "text":
                 "- (mine) Boat Offers — https://claude.ai/artifact/Aaa111 — updated 2026-10-05\n"
                 "- (mine) Gone Page — https://claude.ai/artifact/Bbb222 — updated 2026-10-02\n"
                 "- (mine) Chat Only — https://claude.ai/artifact/Ccc333 — updated 2026-09-01"}]}]),
        ]
        (self.projects / "p" / "s1.jsonl").write_text("\n".join(rows))

    def tearDown(self):
        os.environ.pop("ARTLIB_ROOT", None)
        self.tmp.cleanup()

    def test_scan_pairs_tool_use_with_result(self):
        pubs, listed = backfill.scan_transcripts(self.projects)
        self.assertEqual(set(pubs), {"Aaa111", "Bbb222"})
        self.assertEqual(pubs["Aaa111"]["description"], "boat day")
        self.assertEqual(listed["Ccc333"]["title"], "Chat Only")

    def test_scan_skips_code_artifact_links(self):
        pubs, _ = backfill.scan_transcripts(self.projects)
        self.assertNotIn("9be26904", " ".join(pubs))

    def test_run_creates_rows_from_list_only_entries(self):
        write_config()
        stats = backfill.run(self.projects)
        self.assertEqual((stats["listed"], stats["with_local_page"], stats["need_page"]), (3, 1, 2))
        a = artlib.get("Aaa111")
        self.assertEqual((a["title"], a["about"], a["area"], a["publish_count"]), ("Boat Offers", "boat day", "work", 1))
        self.assertTrue(a["created_at"].startswith("2026-10-01"))
        self.assertIsNotNone(artlib.get("Ccc333"))
        need = json.loads((artlib.root() / "_import" / "needs-page.json").read_text())
        self.assertEqual(sorted(need), ["Bbb222", "Ccc333"])

    def test_import_dir(self):
        backfill.run(self.projects)
        d = Path(self.tmp.name) / "imp"
        d.mkdir()
        (d / "Bbb222.html").write_text("<h1>Fetched later</h1><p>catamaran</p>")
        (d / "Unknown.html").write_text("<p>x</p>")
        self.assertEqual(backfill.import_dir(d), 1)
        self.assertEqual([r["id"] for r in artlib.search("catamaran")], ["Bbb222"])


if __name__ == "__main__":
    unittest.main()
