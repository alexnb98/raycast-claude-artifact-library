#!/usr/bin/env python3
"""One-time backfill of the Artifact Library from Claude Code transcripts.

  backfill.py scan                 rows for every artifact seen in a transcript (publish calls and
                                   `Artifact list` results), local HTML ingested; prints JSON stats
                                   and writes <root>/_import/needs-page.json (ids with no page copy)
  backfill.py import <dir>         ingest <dir>/<id>.html for each file (pages fetched with Artifact read)
  backfill.py render               render every thumbnail that is missing (slow: run in the background)
  backfill.py needs-meta           JSON of rows without tags/about/area, for the keyword pass

Only ids seen in an `Artifact list` result are kept (the current account), so dead
claude.ai/code/artifact links and deleted artifacts stay out.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import artlib  # noqa: E402

LIST_ROW = re.compile(r"- \((?:mine|shared)\) (.+?) — (https://claude\.ai/artifact/[A-Za-z0-9_-]+) — updated (\d{4}-\d{2}-\d{2})")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return json.dumps(content)


def scan_transcripts(projects: Path) -> tuple[dict, dict]:
    """(publishes, listed). publishes: id -> latest publish {file, description, title, cwd, session_id,
    first_ts, last_ts, count}. listed: id -> {title, updated} from the newest list result seen."""
    pubs: dict[str, dict] = {}
    listed: dict[str, dict] = {}
    for f in projects.rglob("*.jsonl"):
        uses: dict[str, tuple] = {}
        try:
            fh = f.open(errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"Artifact"' not in line and "claude.ai/artifact/" not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                content = (d.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and b.get("name") == "Artifact":
                        uses[b.get("id")] = (b.get("input") or {}, d.get("timestamp") or "", d.get("cwd"),
                                             d.get("sessionId"))
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                        inp, ts, cwd, sid = uses[b["tool_use_id"]]
                        text = _text(b.get("content"))
                        action = inp.get("action") or "publish"
                        if action == "list":
                            for title, url, upd in LIST_ROW.findall(text):
                                aid = artlib.artifact_id(url)
                                if aid and ts >= listed.get(aid, {}).get("ts", ""):
                                    listed[aid] = {"title": title, "updated": upd, "ts": ts}
                            continue
                        if action != "publish" or inp.get("asset") or text.startswith("<tool_use_error>"):
                            continue
                        aid = artlib.artifact_id(text) or artlib.artifact_id(inp.get("url"))
                        if not aid:
                            continue
                        p = pubs.setdefault(aid, {"count": 0, "first_ts": ts, "last_ts": ""})
                        p["count"] += 1
                        p["first_ts"] = min(p["first_ts"] or ts, ts)
                        if ts >= p["last_ts"]:
                            p.update(last_ts=ts, cwd=cwd, session_id=sid, file=inp.get("file_path") or "",
                                     type_url=inp.get("type_url"), files=bool(inp.get("files")))
                        for k in ("description", "title"):
                            if inp.get(k):
                                p[k] = inp[k]
    return pubs, listed


def _session_index() -> dict[str, tuple[str, str]]:
    """cli session id -> (desktop session id, title), from every desktop app session file."""
    out = {}
    for f in artlib.sidebar_files():
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if d.get("cliSessionId"):
            out[d["cliSessionId"]] = (d.get("sessionId", ""), d.get("title", "") or "")
    return out


def _dt(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def run(projects: Path | None = None) -> dict:
    projects = projects or Path.home() / ".claude" / "projects"
    pubs, listed = scan_transcripts(projects)
    index = _session_index()  # one full scan of the desktop session index
    stats = {"listed": len(listed), "published_in_transcripts": len(pubs), "with_local_page": 0,
             "with_session": 0, "need_page": 0, "typed": 0}
    need = []
    for aid, li in listed.items():
        p = pubs.get(aid, {})
        fp = p.get("file", "")
        typed = fp.endswith(".json") or bool(p.get("type_url")) or (not fp and p.get("files"))
        page = None
        if fp.endswith((".html", ".htm")) and Path(fp).is_file():
            page = Path(fp).read_text(errors="ignore")
            stats["with_local_page"] += 1
        sid = p.get("session_id")
        host, stitle = index.get(sid or "", ("", ""))
        stats["with_session"] += bool(sid)
        stats["typed"] += bool(typed)
        created = _dt(p.get("first_ts", "")) or datetime.fromisoformat(li["updated"])
        artlib.upsert(aid, url=f"https://claude.ai/artifact/{aid}", title=li["title"],
                      about=p.get("description", ""), source_file=fp or None, page_html=page,
                      kind="typed" if typed else "html", area=artlib.area_for(p.get("cwd"), f"{li['title']} {p.get('description', '')}"),
                      project_dir=p.get("cwd"), session_id=sid, host_session_id=host or None,
                      session_title=stitle, when=created, count_publish=False)
        con = artlib.connect()
        with con:  # backfill knows the real history: publish count and last update
            upd = _dt(p.get("last_ts", "")) or datetime.fromisoformat(li["updated"])
            con.execute("update artifacts set publish_count=?, updated_at=?, title=? where id=?",
                        (max(p.get("count", 0), 1), upd.replace(microsecond=0).isoformat(), li["title"], aid))
        con.close()
        if page is None and not typed:
            need.append(aid)
    stats["need_page"] = len(need)
    imp = artlib.root() / "_import"
    imp.mkdir(parents=True, exist_ok=True)
    (imp / "needs-page.json").write_text(json.dumps(need, indent=0))
    return stats


def import_dir(d: Path) -> int:
    n = 0
    for f in sorted(d.glob("*.html")):
        if artlib.get(f.stem):
            artlib.upsert(f.stem, page_html=f.read_text(errors="ignore"), count_publish=False)
            n += 1
    return n


def needs_meta() -> list[dict]:
    con = artlib.connect()
    rows = con.execute("select id, title, about, headings, substr(body, 1, 600) as body, area, project_dir"
                       " from artifacts where tags = '' or about = '' or area = ''").fetchall()
    con.close()
    return [dict(r) for r in rows]


def render_missing() -> dict:
    out = {"render": 0, "placeholder": 0}
    con = artlib.connect()
    ids = [r["id"] for r in con.execute("select id from artifacts order by updated_at desc")]
    con.close()
    for aid in ids:
        if not artlib.files(aid)["thumb"].exists():
            out[artlib.render(aid)] += 1
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "scan":
        print(json.dumps(run()))
    elif cmd == "import":
        print(f"imported {import_dir(Path(sys.argv[2]))}")
    elif cmd == "render":
        print(json.dumps(render_missing()))
    elif cmd == "needs-meta":
        print(json.dumps(needs_meta(), ensure_ascii=False))
    else:
        print(__doc__)
        sys.exit(1)
