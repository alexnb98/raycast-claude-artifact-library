#!/usr/bin/env python3
"""Artifact Library: one searchable place for every claude.ai artifact published from Claude Code.

Data lives in ~/Documents/Artifacts/ (override with ARTLIB_ROOT):
  config.json      optional categories (see README); no file = no categories
  library.db       SQLite, table `artifacts` + FTS5 index `artifacts_fts`
  page/<id>.html   copy of the published page (latest version)
  img/<id>.png     1280x800 render of the top of the page (Quick Look)
  thumb/<id>.png   600 px thumbnail for the Raycast grid
Originals and the artifacts themselves are never modified.

Search rule (mirrored in raycast/src/fts-artifacts.ts, a test fails if they drift):
  Unicode word tokens, drop STOPWORDS and single letters, quote each token with a prefix star,
  then: artifacts matching ALL tokens
  first, then those matching ANY, each group by bm25. "what's the boat price" -> all '"boat"* AND
  "price"*', any '"boat"* OR "price"*'. Empty -> newest first by updated_at.

CLI:
  artlib.py thumb <id>                       render thumbnail (the hook runs this detached)
  artlib.py set-meta <id> [--about A] [--tags "a b c"] [--area <category>]
  artlib.py ingest <id> <html> [--url U --title T --about A --area X --kind K]
  artlib.py search [query] [--area all|<category>] [--type all|<type>]
  artlib.py remove <id>                      move its library files to the Trash, drop the row
  artlib.py categories                       print the configured categories as JSON
  artlib.py recategorize [--all]             apply the category rules to uncategorised rows (or all)
"""
from __future__ import annotations

import argparse
import shutil
import html as htmllib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

TYPES = ("page", "slides", "design", "doc", "motion", "data", "sheet", "whiteboard", "other")
# Artifact type links (Artifact action:list scope:types, 18 Nov 2026) and app-shell <title>s -> type.
TYPE_URLS = {"8jTsAFQMFDb2oA8MsPJ2eL": "slides", "QKN21svewxgyPb6SYRqWnd": "design", "5M7UeXXcx16TP3vzVFNDzd": "design",
             "Rp9naXUCj2xozpUkyQy19W": "doc", "S4skLPbN9WeoFmuiTfaEFy": "motion", "GZLZthZ2sQjn7T16skfdeF": "data",
             "RoVz4QPcPto4DDQBtvX1Jf": "sheet", "PLsEyfjXbDUM1iasW7rjLR": "whiteboard"}
SHELL_TYPES = {"slides": "slides", "design": "design", "design system": "design", "claude docs": "doc", "docs": "doc",
               "motion": "motion", "data": "data", "sheets": "sheet", "whiteboard": "whiteboard"}
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
URL_RE = re.compile(r"https://claude\.ai/artifact/([A-Za-z0-9_-]+)")
BROWSERS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
THUMB_W = 900
TRIM_PAD = 24  # px of white kept around the cropped page in thumbnails
COLORS = 128
VIEW_W, VIEW_H = 1280, 800
BODY_CAP = 20000
BLANK_SHARE = 0.97
RENDER_TIMEOUT = 20
STOPWORDS = frozenset("""a an the and or of to in on for with at by from is are was were be been it its this that
these those what which who whom whose how why when where do does did i me my we our you your he she they them
their there here as about into than then so not no yes can could should would will just also any some all
der die das den dem des ein eine einer eines einem und oder zu im in am an auf für mit von ist sind war wie was
wer wo wann warum ich wir du sie es nicht kein keine auch nur noch""".split())

SCHEMA = """
create table if not exists artifacts (
  id text primary key, url text not null default '', title text not null default '',
  about text default '', tags text default '', headings text default '', body text default '',
  area text default '', kind text default 'html', source_file text, project_dir text,
  session_id text, host_session_id text, session_title text default '',
  created_at text not null, updated_at text not null, publish_count integer not null default 0,
  type text default ''
);
create index if not exists artifacts_updated on artifacts(updated_at);
create virtual table if not exists artifacts_fts using fts5(
  title, about, tags, headings, body, folder, session_title,
  content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2'
);
"""
FTS_COLS = ("title", "about", "tags", "headings", "body", "folder", "session_title")
RANK = "bm25(artifacts_fts, 10, 6, 5, 4, 1, 2, 2)"
SEARCH_SQL = f"""with m as (select f.rowid rid, 0 grp, {RANK} r from artifacts_fts f where artifacts_fts match ?),
o as (select f.rowid rid, 1 grp, {RANK} r from artifacts_fts f where artifacts_fts match ?
      and f.rowid not in (select rid from m))
select a.* from (select * from m union all select * from o) x join artifacts a on a.rowid = x.rid
{{area}} order by x.grp, x.r limit ?"""
RECENT_SQL = "select * from artifacts {area} order by updated_at desc limit ?"


# ---------- paths ----------

def root() -> Path:
    return Path(os.environ.get("ARTLIB_ROOT") or Path.home() / "Documents" / "Artifacts")


def files(aid: str) -> dict[str, Path]:
    r = root()
    return {"page": r / "page" / f"{aid}.html", "img": r / "img" / f"{aid}.png", "thumb": r / "thumb" / f"{aid}.png"}


def connect() -> sqlite3.Connection:
    r = root()
    for d in ("page", "img", "thumb"):
        (r / d).mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(r / "library.db", timeout=10)
    con.row_factory = sqlite3.Row
    # Switching to WAL and creating the schema can hit "database is locked" without waiting
    # for the busy timeout when two hooks open a fresh library at once: retry briefly.
    for attempt in range(20):
        try:
            if con.execute("pragma journal_mode").fetchone()[0] != "wal":
                con.execute("pragma journal_mode=wal")
            con.executescript(SCHEMA)
            if "type" not in {r[1] for r in con.execute("pragma table_info(artifacts)")}:
                con.execute("alter table artifacts add column type text default ''")  # libraries from before 18 Nov
            return con
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == 19:
                con.close()
                raise
            time.sleep(0.05 * (attempt + 1))
    return con


def log(msg: str) -> None:
    try:
        root().mkdir(parents=True, exist_ok=True)
        with (root() / "errors.log").open("a") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


# ---------- config: optional categories ----------

def config() -> dict:
    """<root>/config.json. {"categories": [{"name", "label", "description", "folders", "words", "private"}]}.
    Missing or broken file: no categories."""
    try:
        d = json.loads((root() / "config.json").read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def categories() -> list[dict]:
    out = []
    for c in config().get("categories") or []:
        if isinstance(c, dict) and NAME_RE.fullmatch(str(c.get("name", ""))):
            out.append({"name": c["name"], "label": str(c.get("label") or c["name"].title()),
                        "description": str(c.get("description") or ""),
                        "folders": [str(x) for x in c.get("folders") or [] if str(x).strip()],
                        "words": [str(x) for x in c.get("words") or [] if str(x).strip()],
                        "private": bool(c.get("private"))})
    return out


def category_names() -> tuple[str, ...]:
    return tuple(c["name"] for c in categories())


def _in_folder(cwd: str, folder: str) -> bool:
    f = os.path.normpath(os.path.expanduser(folder)).rstrip("/")
    return bool(f) and (cwd == f or cwd.startswith(f + "/"))


# ---------- small image and macOS helpers ----------

def trim(im: Image.Image, pad: int = TRIM_PAD) -> Image.Image:
    """Crop near-white margins so the page fills the thumbnail."""
    diff = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).convert("L").point(
        lambda v: 255 if v > 12 else 0)
    box = diff.getbbox()
    if not box:
        return im
    l, t, r, b = box
    return im.crop((max(0, l - pad), max(0, t - pad), min(im.width, r + pad), min(im.height, b + pad)))


def compress(src: Path, dst: Path) -> None:
    Image.open(src).convert("RGB").quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT).save(dst, optimize=True)


def thumbnail(src: Path, dst: Path) -> None:
    im = trim(Image.open(src).convert("RGB"))
    h = max(1, round(im.height * THUMB_W / im.width))
    im = im.resize((THUMB_W, h), Image.Resampling.LANCZOS)
    im.quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT).save(dst, optimize=True)


def finder_trash(path: Path) -> None:
    if not path.exists():
        return
    r = subprocess.run(["osascript", "-e", f'tell application "Finder" to delete POSIX file "{path}"'],
                       capture_output=True, timeout=30)
    if r.returncode != 0 or path.exists():
        dst = Path.home() / ".Trash" / f"{path.stem}-{datetime.now():%H%M%S}{path.suffix}"
        shutil.move(str(path), dst)


def sidebar_files() -> list[Path]:
    """Claude desktop app session index files, newest first (empty without the desktop app)."""
    base = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
    return sorted(base.glob("*/*/local_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)


def browser() -> str | None:
    return next((b for b in BROWSERS if Path(b).exists()), None)


# ---------- parsing ----------

def artifact_id(text: str | None) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(1) if m else None


def area_for(cwd: str | None, text: str = "") -> str:
    """Category from the publishing folder first (any category), then words in the folder path,
    title or description. First match in config order wins; no match or no categories: ''."""
    cats = categories()
    cwd = os.path.normpath(cwd) if cwd else ""
    for c in cats:
        if cwd and any(_in_folder(cwd, f) for f in c["folders"]):
            return c["name"]
    hay = f"{cwd} {text}"
    for c in cats:
        if any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", hay, re.I) for w in c["words"]):
            return c["name"]
    return ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def page_text(html: str) -> tuple[str, str, str]:
    """(title, headings joined with ' · ', visible body text capped at BODY_CAP)."""
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    stripped = re.sub(r"(?is)<(script|style|svg|template|noscript|head)\b[^>]*>.*?</\1>", " ", html)
    heads = [_clean(h) for h in re.findall(r"(?is)<h[1-3]\b[^>]*>(.*?)</h[1-3]>", stripped)]
    body = _clean(stripped)
    return (_clean(title.group(1)) if title else ""), " · ".join(h for h in heads if h), body[:BODY_CAP]


# ---------- write ----------

def type_for(file_path: str | None, typed: bool, type_url: str | None = None) -> str:
    """Artifact type from what was published: Slides decks send deck.json and slides/*.html, Design
    canvases canvas.json or *.dc.html; other typed artifacts are 'other', plain pages 'page'."""
    tid = artifact_id(type_url)
    if tid in TYPE_URLS:
        return TYPE_URLS[tid]
    fp = file_path or ""
    if "/project/" in fp:
        if fp.endswith("/deck.json") or "/project/slides/" in fp:
            return "slides"
        if fp.endswith(("/canvas.json", ".dc.html")):
            return "design"
    if fp.endswith(".dc.html"):
        return "design"
    return "other" if typed else "page"


def shell_type(html: str) -> str:
    """Type of an app-shell page from its <title> (the type's own name)."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:20000], re.S | re.I)
    return SHELL_TYPES.get(_clean(m.group(1)).lower(), "other") if m else "other"


ASSET_RE = re.compile(r'"([^"\n]+)" \([^)\n]*\) \u2192 "/_blob/([0-9a-f]{32})"')
ASSET_W = 700


def asset_dir() -> Path:
    d = root() / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_assets(upload_result: str) -> int:
    """Keep a small local copy of each uploaded asset (path → /_blob/<id> lines of an Artifact asset upload),
    so thumbnails can show a canvas's or deck's images later. Originals are only read, never changed."""
    n = 0
    for m in ASSET_RE.finditer(upload_result or ""):
        src, bid = Path(m.group(1)), m.group(2)
        if not src.is_file() or list(asset_dir().glob(f"{bid}.*")):
            continue
        try:
            im = Image.open(src)
            im.thumbnail((ASSET_W, ASSET_W * 4))
            im.convert("RGB").quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT).save(
                asset_dir() / f"{bid}.png", optimize=True)
        except Exception:  # not a raster image (svg, font): keep it as is if small
            if src.stat().st_size < 2_000_000:
                (asset_dir() / f"{bid}{src.suffix.lower()}").write_bytes(src.read_bytes())
        n += 1
    return n


def local_blobs(html: str) -> str:
    """/_blob/<id> → the cached local copy where one exists."""
    def sub(m):
        hit = next(iter(sorted(asset_dir().glob(f"{m.group(1)}.*"))), None)
        return hit.as_uri() if hit else m.group(0)
    return re.sub(r"/_blob/([0-9a-f]{32})", sub, html)


def _with_base(html: str, base: Path) -> str:
    """Let relative images and CSS of a board load while its project folder still exists."""
    tag = f'<base href="{base.as_uri()}/">'
    m = re.search(r"<head[^>]*>", html, re.I)
    return html[:m.end()] + tag + html[m.end():] if m else tag + html


def typed_page(project: Path) -> dict | None:
    """Renderable page + search text for a typed artifact's local project folder, or None.
    Design canvas (canvas.json): the first board of the page the canvas opens on (what the viewer sees first),
    text from every board. Slides (deck.json): the cover slide with the deck's CSS and fonts at 1280 px,
    text from every slide."""
    import json
    project = Path(project)
    try:
        if (project / "canvas.json").is_file():
            c = json.loads((project / "canvas.json").read_text())
            boards, order = c.get("boards") or {}, c.get("order") or list(c.get("boards") or {})
            launch = (c.get("launch") or {}).get("page")
            first = next((b for b in order if (boards.get(b) or {}).get("page") == launch), order[0] if order else None)
            if not first or not (project / first).is_file():
                return None
            texts = [page_text((project / b).read_text(errors="ignore"))[2] for b in order if (project / b).is_file()]
            heads = [p.get("name", "") for p in c.get("pages") or []] + [(boards.get(b) or {}).get("title", "") for b in order]
            w = (boards.get(first) or {}).get("w") or VIEW_W
            board = (project / first).read_text(errors="ignore")
            zoom = f"<style>html{{zoom:{min(1.0, VIEW_W / w):.4f}}}</style>"
            board = re.sub(r"(<head[^>]*>)", r"\1" + zoom, board, count=1, flags=re.I) if re.search(r"<head", board, re.I) else zoom + board
            return {"html": _with_base(board, project),
                    "headings": " · ".join(h for h in heads if h), "body": " ".join(texts)[:BODY_CAP],
                    "title": c.get("title", ""), "type": "design"}
        if (project / "deck.json").is_file():
            d = json.loads((project / "deck.json").read_text())
            order = d.get("order") or []
            slides = [project / "slides" / f"{s}.html" for s in order]
            slides = [s for s in slides if s.is_file()]
            if not slides:
                return None
            css = (project / "deck.css").read_text(errors="ignore") if (project / "deck.css").is_file() else ""
            fonts = "".join(f'<link rel="stylesheet" href="{f["href"]}">' for f in (d.get("faces") or {}).values()
                            if isinstance(f, dict) and str(f.get("href", "")).startswith("https://fonts.googleapis.com/"))
            z = VIEW_W / 1920
            html = (f"<!doctype html><html><head><meta charset='utf-8'>{fonts}<style>{css}</style><style>"
                    f"html,body{{margin:0;background:#fff}} body{{zoom:{z:.4f}}} section{{width:1920px;height:1080px;"
                    f"box-sizing:border-box;position:relative;overflow:hidden}}</style></head><body>"
                    f"{slides[0].read_text(errors='ignore')}</body></html>")
            heads = [d.get("title", "")] + [s.get("description", "") for s in (d.get("sections") or {}).values()
                                            if isinstance(s, dict)]
            texts = [page_text(s.read_text(errors="ignore"))[2] for s in slides]
            return {"html": _with_base(html, project), "headings": " · ".join(h for h in heads if h),
                    "body": " ".join(texts)[:BODY_CAP], "title": d.get("title", ""), "type": "slides"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


def project_dir(file_path: str | None, root_dir: str | None = None) -> Path | None:
    """The `project/` folder a typed publish came from (from its root, or the file's path)."""
    if root_dir and (Path(root_dir) / "project").is_dir():
        return Path(root_dir) / "project"
    fp = file_path or ""
    i = fp.find("/project/")
    return Path(fp[: i + len("/project")]) if i >= 0 else None


def is_type_shell(html: str) -> bool:
    """True for the page of an artifact made from an Artifact type (Slides, Docs, Design): an app shell."""
    return "__appifact" in html[:200000] or "appifact-flags" in html[:200000]


def _fts_values(r: sqlite3.Row) -> tuple:
    folder = Path(r["project_dir"]).name if r["project_dir"] else ""
    return (r["title"], r["about"], r["tags"], r["headings"], r["body"], folder, r["session_title"])


def _reindex(con: sqlite3.Connection, aid: str) -> None:
    r = con.execute("select rowid, * from artifacts where id=?", (aid,)).fetchone()
    con.execute("delete from artifacts_fts where rowid=?", (r["rowid"],))
    con.execute(f"insert into artifacts_fts(rowid, {', '.join(FTS_COLS)}) values (?,?,?,?,?,?,?,?)",
                (r["rowid"], *_fts_values(r)))


def upsert(aid: str, *, url: str = "", title: str = "", about: str = "", tags: str = "",
           source_file: str | None = None, page_html: str | None = None, kind: str = "",
           area: str = "", project_dir: str | None = None, session_id: str | None = None,
           host_session_id: str | None = None, session_title: str = "",
           when: datetime | None = None, count_publish: bool = True, type: str = "",
           headings: str = "", body: str = "") -> str:
    """Insert or update one artifact by id. Empty values never blank out what an earlier call set."""
    now = (when or datetime.now()).replace(microsecond=0).isoformat()
    con = connect()
    fields = {"url": url, "about": about, "tags": tags, "source_file": source_file, "kind": kind,
              "area": area, "project_dir": project_dir, "session_id": session_id,
              "host_session_id": host_session_id, "session_title": session_title, "type": type}
    page_title = ""
    if page_html is not None:
        if is_type_shell(page_html):  # a typed artifact's page is the type's app, not its content
            fields["kind"] = "typed"
            if not type or type == "other":
                fields["type"] = shell_type(page_html)
        else:
            page_title, p_heads, p_body = page_text(page_html)
            fields.update(headings=p_heads, body=p_body)
        files(aid)["page"].write_text(page_html)
    if headings or body:  # a typed artifact's text comes from all its boards/slides, not the rendered one
        fields.update(headings=headings, body=body)
    fields["title"] = title
    fields = {k: v for k, v in fields.items() if v}
    try:
        with con:
            con.execute("insert or ignore into artifacts (id, created_at, updated_at) values (?,?,?)", (aid, now, now))
            if count_publish:
                con.execute("update artifacts set publish_count = publish_count + 1, updated_at = max(updated_at, ?)"
                            " where id=?", (now, aid))
            if fields:
                con.execute(f"update artifacts set {', '.join(k + '=?' for k in fields)} where id=?",
                            (*fields.values(), aid))
            if page_title:  # the page's own <title> only fills a missing title, never replaces claude.ai's
                con.execute("update artifacts set title=? where id=? and title=''", (page_title, aid))
            con.execute("update artifacts set created_at = min(created_at, ?) where id=?", (now, aid))
            _reindex(con, aid)
    finally:
        con.close()
    return aid


def set_meta(aid: str, *, about: str = "", tags: str = "", area: str = "") -> bool:
    con = connect()
    try:
        if not con.execute("select 1 from artifacts where id=?", (aid,)).fetchone():
            return False
    finally:
        con.close()
    upsert(aid, about=about, tags=" ".join(re.split(r"[,\s]+", tags.strip())) if tags else "",
           area=area, count_publish=False)
    return True


def remove(aid: str, trash=finder_trash) -> None:
    for f in files(aid).values():
        if f.exists():
            trash(f)
    con = connect()
    try:
        with con:
            r = con.execute("select rowid from artifacts where id=?", (aid,)).fetchone()
            if r:
                con.execute("delete from artifacts_fts where rowid=?", (r["rowid"],))
                con.execute("delete from artifacts where id=?", (aid,))
    finally:
        con.close()


def recent_session(session_id: str | None, limit: int = 60) -> tuple[str, str]:
    """(host id, title) from the desktop session index, looking only at the most recently
    touched files: the publishing session is near the top, and a full scan of ~2,400 files
    takes ~4 s on a miss (CLI sessions are not in the index)."""
    if not session_id:
        return "", ""
    import json
    for f in sidebar_files()[:limit]:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if d.get("cliSessionId") == session_id:
            return d.get("sessionId", ""), d.get("title", "") or ""
    return "", ""


# ---------- search ----------

def _tokens(q: str | None) -> list[str]:
    # Single letters ("what's" -> "s") would prefix-match nearly everything; digits stay.
    return [t for t in re.findall(r"\w+", q or "")
            if t.lower() not in STOPWORDS and (len(t) > 1 or t.isdigit())]


def fts_query(q: str | None, op: str = " AND ") -> str:
    """Tokens minus stop-words, each "tok"* (a prefix match), joined by op."""
    return op.join(f'"{t}"*' for t in _tokens(q))


def search(q: str = "", area: str = "all", limit: int = 60, type: str = "all") -> list[dict]:
    """Artifacts matching all words first, then those matching only some, each group by bm25.
    Empty query: newest update first. area/type filter when a configured category / one of TYPES."""
    all_q, any_q = fts_query(q), fts_query(q, " OR ")
    conds, params = [], []
    if area in category_names():
        conds.append("{p}area = ?"); params.append(area)
    if type in TYPES:
        conds.append("{p}type = ?"); params.append(type)
    con = connect()
    try:
        if all_q:
            where = ("where " + " and ".join(conds)).format(p="a.") if conds else ""
            rows = con.execute(SEARCH_SQL.format(area=where), (all_q, any_q, *params, limit))
        else:
            where = ("where " + " and ".join(conds)).format(p="") if conds else ""
            rows = con.execute(RECENT_SQL.format(area=where), (*params, limit))
        return [dict(r) for r in rows]
    finally:
        con.close()


def recategorize(everything: bool = False) -> int:
    """Set each row's category from the rules (rows without one, or every row with everything=True)."""
    con = connect()
    try:
        rows = con.execute("select id, project_dir, title, about, area from artifacts").fetchall()
        changed = 0
        with con:
            for r in rows:
                if r["area"] and not everything:
                    continue
                new = area_for(r["project_dir"], f"{r['title']} {r['about'] or ''}")
                if new != (r["area"] or ""):
                    con.execute("update artifacts set area=? where id=?", (new, r["id"]))
                    changed += 1
        return changed
    finally:
        con.close()


def get(aid: str) -> dict | None:
    con = connect()
    try:
        r = con.execute("select * from artifacts where id=?", (aid,)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


# ---------- images ----------

def is_blank(png: Path) -> bool:
    im = Image.open(png).convert("RGB").resize((160, 100))
    top = max(im.getcolors(160 * 100), key=lambda c: c[0])[0]
    return top / (160 * 100) > BLANK_SHARE


def _font(size: int) -> ImageFont.FreeTypeFont:
    for f in (Path("/System/Library/Fonts/Helvetica.ttc"), Path("/System/Library/Fonts/SFNS.ttf")):
        try:
            return ImageFont.truetype(str(f), size)
        except OSError:
            continue
    return ImageFont.load_default()


# Placeholder card colours: categories take these in config order; no category and typed artifacts use grey.
PALETTE = (("#5B3E96", "#F1EDF8"), ("#1F7A5C", "#E9F5EF"), ("#C2510A", "#FDF0E7"), ("#B4235B", "#FBEAF1"),
           ("#1D5FA8", "#EAF1FA"), ("#7A6A12", "#F7F4E3"))
GREY = ("#55565B", "#EFEFF1")


def colors_for(area: str) -> tuple[str, str]:
    names = category_names()
    return PALETTE[names.index(area) % len(PALETTE)] if area in names else GREY


def placeholder(aid: str) -> None:
    """Title card used when a page can't be rendered (typed artifact, runtime-only page, missing copy)."""
    a = get(aid) or {"title": aid, "area": "", "kind": "", "updated_at": ""}
    accent, bg = GREY if a.get("kind") == "typed" else colors_for(a.get("area") or "")
    im = Image.new("RGB", (VIEW_W, VIEW_H), bg)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, VIEW_W, 14), fill=accent)
    words, lines, cur = (a.get("title") or aid).split(), [], ""
    big = _font(72)
    for w in words:
        trial = f"{cur} {w}".strip()
        if d.textlength(trial, font=big) > VIEW_W - 200 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    lines.append(cur)
    y = 220
    for line in lines[:4]:
        d.text((100, y), line, font=big, fill=accent)
        y += 92
    meta = " · ".join(x for x in (a.get("area") or "", "deck/doc" if a.get("kind") == "typed" else "",
                                  (a.get("updated_at") or "")[:10]) if x)
    d.text((100, VIEW_H - 140), meta, font=_font(36), fill="#55565B")
    f = files(aid)
    tmp = f["img"].with_suffix(".raw.png")
    im.save(tmp)
    compress(tmp, f["img"])
    thumbnail(tmp, f["thumb"])
    tmp.unlink(missing_ok=True)
    _marker(aid).touch()


def _source_for_render(a: dict) -> Path | None:
    """Prefer the original file (relative images load), else the library copy. A typed artifact
    always renders its library copy: its last published file is just one board or slide."""
    src = a.get("source_file")
    if a.get("kind") != "typed" and src and src.endswith(".html") and Path(src).exists():
        return Path(src)
    page = files(a["id"])["page"]
    return page if page.exists() else None


def _fallback(aid: str) -> str:
    """Placeholder, unless a real render already exists: a failed re-render never replaces a good image."""
    f = files(aid)
    if f["thumb"].exists() and not _marker(aid).exists():
        return "kept"
    placeholder(aid)
    return "placeholder"


def _marker(aid: str) -> Path:
    return files(aid)["thumb"].with_suffix(".placeholder")


def render(aid: str) -> str:
    """Screenshot the top of the page. Returns 'render', 'kept' or 'placeholder'. Never raises.
    Renders are serialised with a file lock: concurrent headless Chromes on one profile drop screenshots."""
    import fcntl
    try:
        a = get(aid)
        src = _source_for_render(a) if a else None
        if not a or not src:
            placeholder(aid)
            return "placeholder"
        f = files(aid)
        exe = browser()
        if not exe:  # no Chromium-based browser installed: title card instead of a screenshot
            return _fallback(aid)
        with open(root() / ".render.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            raw = f["img"].with_name(f"{aid}.{os.getpid()}.raw.png")
            raw.unlink(missing_ok=True)
            tmp_page = None
            html = src.read_text(errors="ignore")
            if "/_blob/" in html:  # use cached copies of the artifact's uploaded images
                tmp_page = src.with_name(f".{aid}.{os.getpid()}.render.html")
                tmp_page.write_text(local_blobs(html))
                src = tmp_page
            try:
                subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                                f"--user-data-dir={root() / '.chrome'}", "--force-device-scale-factor=1",
                                f"--window-size={VIEW_W},{VIEW_H}", "--virtual-time-budget=4000",
                                "--blink-settings=preferredColorScheme=1", "--default-background-color=ffffffff",
                                f"--screenshot={raw}", src.as_uri()],
                               capture_output=True, timeout=RENDER_TIMEOUT, check=False)
                if not raw.exists() or is_blank(raw):
                    return _fallback(aid)
                compress(raw, f["img"])
                thumbnail(raw, f["thumb"])
                _marker(aid).unlink(missing_ok=True)
                return "render"
            finally:
                raw.unlink(missing_ok=True)
                if tmp_page:
                    tmp_page.unlink(missing_ok=True)
    except Exception:
        log(f"render {aid}: {traceback.format_exc()}")
        try:
            return _fallback(aid)
        except Exception:
            log(f"placeholder {aid}: {traceback.format_exc()}")
        return "placeholder"


# ---------- natural-language query (Claude Haiku) ----------

PLAN_PROMPT = """You turn someone's vague request for one of their saved claude.ai artifacts into a search plan.
Answer with ONLY a JSON object, no prose:
{"keywords": "4-10 lowercase words: the topic words from the request plus close synonyms (experiment -> test, flat -> apartment); never filler like thing, made, showing, page",
 "category": "{categories} | all",
 "type": "page | slides | design | doc | motion | data | sheet | whiteboard | all"}
Use category/type only when the request clearly says so (e.g. "deck" or "slides" -> slides, "canvas" or "board" -> design).
{category_help}Request: """


def _run_claude(prompt: str, timeout: int = 45) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_SESSION")}
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):  # a token copied from a wrapped Terminal line can carry breaks/spaces
        env["CLAUDE_CODE_OAUTH_TOKEN"] = re.sub(r"\s+", "", env["CLAUDE_CODE_OAUTH_TOKEN"])
    r = subprocess.run(["claude", "-p", "--model", "haiku", "--tools", "", "--setting-sources", "",
                        "--no-session-persistence"], input=prompt, capture_output=True, text=True,
                       timeout=timeout, env=env, cwd=str(root()))
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "claude failed")
    return r.stdout


def plan(request: str, run=_run_claude) -> dict:
    """Search plan for a natural-language request: {"keywords", "category", "type"} with safe values."""
    cats = categories()
    help_ = "".join(f"Category {c['name']}: {c['description']}\n" for c in cats if c["description"])
    prompt = PLAN_PROMPT.replace("{categories}", " | ".join(c["name"] for c in cats) or "(none)").replace(
        "{category_help}", help_)
    out = run(prompt + request.strip()[:500])
    m = re.search(r"\{.*\}", out, re.S)
    d = json.loads(m.group(0)) if m else {}
    kw = " ".join(re.findall(r"\w+", str(d.get("keywords", ""))))[:200] or request
    cat = d.get("category") if d.get("category") in category_names() else "all"
    ty = d.get("type") if d.get("type") in TYPES else "all"
    return {"keywords": kw, "category": cat, "type": ty}


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="artlib")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("thumb").add_argument("id")
    m = sub.add_parser("set-meta")
    m.add_argument("id")
    for k in ("about", "tags", "area"):
        m.add_argument(f"--{k}", default="")
    i = sub.add_parser("ingest")
    i.add_argument("id")
    i.add_argument("html", help="a page file, or a typed artifact's project/ folder")
    for k in ("url", "title", "about", "area", "kind"):
        i.add_argument(f"--{k}", default="")
    s = sub.add_parser("search")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--area", default="all")
    s.add_argument("--type", default="all")
    sub.add_parser("remove").add_argument("id")
    sub.add_parser("categories", help="configured categories as JSON")
    sub.add_parser("recategorize", help="apply the category rules").add_argument("--all", action="store_true")
    sub.add_parser("plan", help="natural-language request -> search plan JSON (Claude Haiku)").add_argument("request")
    ca = sub.add_parser("cache-asset", help="keep a small copy of one fetched /_blob/<id> image")
    ca.add_argument("blob_id")
    ca.add_argument("file")
    sub.add_parser("missing-assets").add_argument("project", help="a typed project/ folder")
    a = ap.parse_args(argv)
    if a.cmd == "thumb":
        print(render(a.id))
    elif a.cmd == "set-meta":
        if a.area and a.area not in category_names():
            ap.error(f"--area must be one of: {', '.join(category_names()) or '(no categories configured)'}")
        ok = set_meta(a.id, about=a.about, tags=a.tags, area=a.area)
        print("saved" if ok else f"unknown id {a.id}")
        return 0 if ok else 1
    elif a.cmd == "ingest":
        if a.area and a.area not in category_names():
            ap.error(f"--area must be one of: {', '.join(category_names()) or '(no categories configured)'}")
        tp = typed_page(Path(a.html)) if Path(a.html).is_dir() else None
        if Path(a.html).is_dir() and not tp:
            ap.error("no canvas.json or deck.json with readable boards/slides in that folder")
        html = tp["html"] if tp else Path(a.html).read_text(errors="ignore")
        upsert(a.id, url=a.url or f"https://claude.ai/artifact/{a.id}", title=a.title, about=a.about,
               area=a.area, kind="typed" if tp else a.kind, type=tp["type"] if tp else "", page_html=html,
               headings=tp["headings"] if tp else "", body=tp["body"] if tp else "", count_publish=False)
        print(render(a.id))
    elif a.cmd == "search":
        for r in search(a.query, a.area, type=a.type):
            print(f"{r['updated_at'][:10]}  {r['area'] or '-':8}  {r['title']}  {r['url']}")
    elif a.cmd == "cache-asset":
        if not re.fullmatch(r"[0-9a-f]{32}", a.blob_id):
            ap.error("blob_id must be 32 hex characters")
        print(cache_assets(f'"{a.file}" (x) \u2192 "/_blob/{a.blob_id}"'))
    elif a.cmd == "missing-assets":
        tp = typed_page(Path(a.project))
        ids = sorted(set(re.findall(r"/_blob/([0-9a-f]{32})", tp["html"] if tp else "")))
        print(" ".join(i for i in ids if not list(asset_dir().glob(f"{i}.*"))))
    elif a.cmd == "plan":
        try:
            print(json.dumps(plan(a.request)))
        except Exception as e:  # shown by Raycast as the error toast, and logged (never the token)
            tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            ws = bool(re.search(r"\s", tok))
            shape = f"len {len(tok)}, prefix {tok[:10]!r}, whitespace {ws}" if tok else "none"  # never the token
            log(f"plan failed (token: {shape}): {str(e)[:300]}")
            print(json.dumps({"error": str(e)[:300]}))
            return 1
    elif a.cmd == "categories":
        print(json.dumps(categories(), ensure_ascii=False))
    elif a.cmd == "recategorize":
        print(f"changed {recategorize(everything=a.all)}")
    elif a.cmd == "remove":
        remove(a.id)
        print("removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
