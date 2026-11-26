from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from PIL import Image  # noqa: E402

import artlib  # noqa: E402

SAMPLE_CONFIG = {"categories": [
    {"name": "work", "label": "Work", "folders": ["/Users/sam/code/acme"], "words": ["acme"]},
    {"name": "side", "label": "Side project", "folders": ["/Users/sam/code/garden-app"], "words": []},
    {"name": "family", "label": "Family", "folders": [], "words": ["wedding", "kids"], "private": True},
]}


def write_config(cfg=SAMPLE_CONFIG):
    import json
    root = artlib.root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps(cfg))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ARTLIB_ROOT"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("ARTLIB_ROOT", None)
        self.tmp.cleanup()

    def row(self, aid):
        con = artlib.connect()
        r = con.execute("select * from artifacts where id=?", (aid,)).fetchone()
        con.close()
        return r


class TestBasics(Base):
    def test_artifact_id(self):
        self.assertEqual(artlib.artifact_id("Published x at https://claude.ai/artifact/AbC12_x-9 ok"), "AbC12_x-9")
        self.assertIsNone(artlib.artifact_id("https://claude.ai/code/artifact/9be26904-18f0-45db-9a51-6d563d2ecda5"))
        self.assertIsNone(artlib.artifact_id(""))

    def test_no_config_means_no_categories(self):
        self.assertEqual(artlib.categories(), [])
        self.assertEqual(artlib.area_for("/Users/sam/code/acme"), "")

    def test_area_from_folder_then_words(self):
        write_config()
        self.assertEqual(artlib.area_for("/Users/sam/code/acme/web"), "work")
        self.assertEqual(artlib.area_for("/Users/sam/code/acme"), "work")
        self.assertEqual(artlib.area_for("/Users/sam/code/acmeold"), "")  # a prefix is not the same folder
        self.assertEqual(artlib.area_for("/Users/sam/code/garden-app/.claude/worktrees/x"), "side")
        self.assertEqual(artlib.area_for("/Users/sam", "Wedding seating plan"), "family")
        self.assertEqual(artlib.area_for("/Users/sam/code/acme", "Wedding gift for the Acme team"), "work")  # folder wins
        self.assertEqual(artlib.area_for("/tmp/elsewhere"), "")
        self.assertEqual(artlib.area_for(None), "")

    def test_bad_config_is_ignored(self):
        root = artlib.root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "config.json").write_text("{not json")
        self.assertEqual(artlib.categories(), [])
        (root / "config.json").write_text('{"categories": [{"name": "Bad Name!"}, {"name": "ok"}]}')
        self.assertEqual(artlib.category_names(), ("ok",))

    def test_recategorize(self):
        write_config()
        artlib.upsert("R1", title="Kids birthday", project_dir="/Users/sam")
        artlib.upsert("R2", title="x", project_dir="/Users/sam/code/acme", area="side")
        con = artlib.connect()
        with con:
            con.execute("update artifacts set area='' where id='R1'")
        con.close()
        self.assertEqual(artlib.recategorize(), 1)
        self.assertEqual(artlib.get("R1")["area"], "family")
        self.assertEqual(artlib.get("R2")["area"], "side")  # set by hand: kept
        self.assertEqual(artlib.recategorize(everything=True), 1)
        self.assertEqual(artlib.get("R2")["area"], "work")

    def test_page_text_strips_script_style_svg(self):
        html = """<html><head><title>My &amp; Title</title><style>.x{color:red}</style></head>
        <body><h1>Big <b>Head</b></h1><script>var secret=1</script><svg><text>chartlabel</text></svg>
        <p>Hello   world</p><h3>Small</h3></body></html>"""
        title, heads, body = artlib.page_text(html)
        self.assertEqual(title, "My & Title")
        self.assertEqual(heads, "Big Head · Small")
        self.assertIn("Hello world", body)
        for bad in ("secret", "color:red", "chartlabel"):
            self.assertNotIn(bad, body)

    def test_page_text_caps_body(self):
        self.assertLessEqual(len(artlib.page_text("<p>" + "word " * 10000 + "</p>")[2]), artlib.BODY_CAP)


class TestTypes(Base):
    def test_type_for(self):
        self.assertEqual(artlib.type_for("/s/root/project/deck.json", True), "slides")
        self.assertEqual(artlib.type_for("/s/root/project/slides/cover.html", True), "slides")
        self.assertEqual(artlib.type_for("/s/root/project/canvas.json", True), "design")
        self.assertEqual(artlib.type_for("/s/root/project/Main.dc.html", True), "design")
        self.assertEqual(artlib.type_for("/s/data.json", True), "other")
        self.assertEqual(artlib.type_for("/s/report.html", False), "page")
        self.assertEqual(artlib.type_for(None, True, "https://claude.ai/artifact/S4skLPbN9WeoFmuiTfaEFy"), "motion")

    def test_shell_title_gives_type(self):
        artlib.upsert("S1", url="u", title="Wedding", page_html='<title>Claude Docs</title><script>"__appifact"</script>')
        artlib.upsert("S2", url="u", title="Board", page_html='<title>Design</title><script>"__appifact"</script>')
        self.assertEqual((artlib.get("S1")["type"], artlib.get("S2")["type"]), ("doc", "design"))

    def test_type_filter(self):
        artlib.upsert("D1", url="u", title="Swipe deck", type="slides", area="work")
        artlib.upsert("D2", url="u", title="Swipe page", type="page", area="work")
        self.assertEqual([r["id"] for r in artlib.search("swipe", type="slides")], ["D1"])
        self.assertEqual([r["id"] for r in artlib.search("", type="page", area="work")], ["D2"])

    def test_old_library_gets_type_column(self):
        import sqlite3
        con = sqlite3.connect(artlib.root() / "library.db")
        con.execute("create table artifacts (id text primary key, url text not null default '', title text not null default '',"
                    " about text default '', tags text default '', headings text default '', body text default '', area text default '',"
                    " kind text default 'html', source_file text, project_dir text, session_id text, host_session_id text,"
                    " session_title text default '', created_at text not null, updated_at text not null,"
                    " publish_count integer not null default 0)")
        con.commit(); con.close()
        artlib.upsert("O1", url="u", title="Old", type="page")
        self.assertEqual(artlib.get("O1")["type"], "page")


class TestUpsert(Base):
    def test_upsert_republish_keeps_created_and_about(self):
        t1 = datetime(2026, 11, 1, 10, 0)
        artlib.upsert("A1", url="https://claude.ai/artifact/A1", title="First", about="the about",
                      page_html="<h1>One</h1>", area="work", when=t1)
        artlib.upsert("A1", url="https://claude.ai/artifact/A1", title="", about="",
                      page_html="<h1>Two</h1>", when=t1 + timedelta(days=2))
        r = self.row("A1")
        self.assertEqual(r["publish_count"], 2)
        self.assertEqual(r["created_at"], t1.isoformat())
        self.assertEqual(r["about"], "the about")
        self.assertEqual(r["title"], "First")
        self.assertIn("Two", r["headings"])
        self.assertEqual((artlib.root() / "page" / "A1.html").read_text(), "<h1>Two</h1>")
        con = artlib.connect()
        self.assertEqual(con.execute("select count(*) from artifacts").fetchone()[0], 1)
        con.close()

    def test_page_title_never_replaces_real_title(self):
        artlib.upsert("T1", url="u", title="Wedding Plan")
        artlib.upsert("T1", page_html="<title>Claude Docs</title><p>x</p>", count_publish=False)
        self.assertEqual(artlib.get("T1")["title"], "Wedding Plan")

    def test_type_shell_marked_typed(self):
        artlib.upsert("T2", url="u", title="Deck", page_html='<script>var FLAGS_GLOBAL = "__appifact_flags";</script><p>app</p>')
        a = artlib.get("T2")
        self.assertEqual((a["kind"], a["body"]), ("typed", ""))

    def test_title_falls_back_to_html_title(self):
        artlib.upsert("A2", url="u", page_html="<title>From Page</title><p>x</p>")
        self.assertEqual(self.row("A2")["title"], "From Page")

    def test_set_meta_and_count_publish_false(self):
        artlib.upsert("A3", url="u", title="T")
        artlib.set_meta("A3", tags="boat yacht", area="personal")
        artlib.upsert("A3", url="u", count_publish=False)
        r = self.row("A3")
        self.assertEqual((r["tags"], r["area"], r["publish_count"]), ("boat yacht", "personal", 1))


class TestSearch(Base):
    def setUp(self):
        super().setUp()
        artlib.upsert("Y", url="u", title="Lake Yacht Day", about="boat quotes", area="personal",
                      page_html="<p>catamaran price list</p>")
        artlib.upsert("B", url="u", title="Strong applications buried", about="queue study", area="work",
                      page_html="<p>boat mentioned once in body</p>")

    def test_fts_query(self):
        self.assertEqual(artlib.fts_query("what's the boat price"), '"boat"* AND "price"*')
        self.assertEqual(artlib.fts_query("what's the boat price", " OR "), '"boat"* OR "price"*')
        self.assertEqual(artlib.fts_query("badge test"), '"badge"* AND "test"*')
        self.assertEqual(artlib.fts_query("the a"), "")
        self.assertEqual(artlib.fts_query('"); drop'), '"drop"*')
        self.assertEqual(artlib.fts_query("Übersicht"), '"Übersicht"*')

    def test_search_or_ranks_title_first(self):
        ids = [r["id"] for r in artlib.search("yacht boat")]
        self.assertEqual(ids[0], "Y")
        self.assertIn("B", ids)

    def test_all_words_rank_before_partial(self):
        artlib.upsert("Z", url="u", title="Boat boat boat boat", area="work")
        artlib.upsert("Q", url="u", title="Quiet note", about="boat catamaran", area="work")
        ids = [r["id"] for r in artlib.search("boat catamaran")]
        self.assertEqual(set(ids[:2]), {"Y", "Q"})  # both words, even though Z's title says boat 4 times
        self.assertIn("Z", ids[2:])

    def test_search_area_filter(self):
        write_config()
        self.assertEqual([r["id"] for r in artlib.search("boat", area="work")], ["B"])
        self.assertEqual(len(artlib.search("", area="all")), 2)

    def test_diacritics_fold(self):
        artlib.upsert("U", url="u", title="Übersicht Kündigung")
        self.assertEqual([r["id"] for r in artlib.search("ubersicht kundigung")], ["U"])

    def test_stopword_only_query_lists_recent(self):
        self.assertEqual(len(artlib.search("what the")), 2)


class TestImages(Base):
    def test_placeholder_on_blank(self):
        artlib.upsert("P", url="u", title="Blank page", area="work")
        png = Path(self.tmp.name) / "white.png"
        Image.new("RGB", (1280, 800), "white").save(png)
        self.assertTrue(artlib.is_blank(png))
        artlib.placeholder("P")
        self.assertTrue((artlib.root() / "thumb" / "P.png").exists())
        self.assertTrue((artlib.root() / "img" / "P.png").exists())

    def test_render_falls_back_without_page(self):
        artlib.upsert("N", url="u", title="No page")
        self.assertEqual(artlib.render("N"), "placeholder")

    def test_failed_rerender_keeps_real_thumb(self):
        artlib.upsert("K", url="u", title="Kept", page_html="<p>x</p>")
        real = artlib.files("K")["thumb"]
        Image.new("RGB", (600, 375), "red").save(real)
        self.assertEqual(artlib._fallback("K"), "kept")
        self.assertEqual(Image.open(real).convert("RGB").getpixel((5, 5)), (255, 0, 0))
        artlib.placeholder("K")  # a placeholder may be refreshed by a later placeholder
        self.assertEqual(artlib._fallback("K"), "placeholder")

    def test_placeholder_unknown_area(self):
        artlib.upsert("W", url="u", title="Odd", area="weird")
        artlib.placeholder("W")
        self.assertTrue(artlib.files("W")["thumb"].exists())

    def test_remove_trashes_files(self):
        artlib.upsert("R", url="u", title="T", page_html="<p>x</p>")
        artlib.placeholder("R")
        trashed = []
        artlib.remove("R", trash=lambda p: (trashed.append(p.name), p.unlink()))
        self.assertIsNone(self.row("R"))
        self.assertEqual(sorted(trashed), ["R.html", "R.png", "R.png"])
        self.assertFalse(artlib._marker("R").exists() and artlib.get("R"))


class TestConcurrency(Base):
    def test_parallel_upserts(self):
        import threading
        ts = [threading.Thread(target=artlib.upsert, args=(f"C{i}",), kwargs={"url": "u", "title": f"t{i}"})
              for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        con = artlib.connect()
        self.assertEqual(con.execute("select count(*) from artifacts").fetchone()[0], 8)
        con.close()


if __name__ == "__main__":
    unittest.main()
