#!/usr/bin/env python3
"""PostToolUse hook on the Artifact tool: register every publish in the Artifact Library.

Foreground (fast): copy the page, extract its text, upsert the row by artifact id.
Background: a detached `artlib.py thumb <id>` renders the thumbnail.
On a first publish (no tags yet) it asks the publishing Claude, via additionalContext,
to write search keywords with one `artlib.py set-meta` call.
Never fails the tool call: every error goes to ~/Documents/Artifacts/errors.log, exit 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import artlib  # noqa: E402

ARTLIB = HERE / "artlib.py"
ASK = ("Artifact Library: this artifact has no search keywords yet. Run once, without telling the user:\n"
       "{py} {cli} set-meta {aid} --tags \"<8-12 lowercase keywords someone might type months later when "
       "only vaguely remembering it: synonyms and broader terms, in the languages the user writes in, not the "
       "title's own words>\"{about}")


def spawn_render(aid: str) -> None:
    if os.environ.get("ARTLIB_NO_SPAWN"):
        return
    subprocess.Popen([sys.executable, str(ARTLIB), "thumb", aid], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _text(resp) -> str:
    """Plain text of a tool response (string, or a list of content blocks)."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in resp)
    if isinstance(resp, dict):
        return resp.get("text") or json.dumps(resp, ensure_ascii=False)
    return ""


def _type(known: dict, new: str) -> str:
    """A later publish of a stray file (e.g. a deck's CSS) must not downgrade a known type to 'other'."""
    return known.get("type") if new == "other" and known.get("type") else new


def handle(data: dict, env: dict, spawn=spawn_render) -> dict | None:
    inp = data.get("tool_input") or {}
    if not isinstance(inp, dict):
        return None
    if (inp.get("action") or "publish") != "publish":
        return None
    if inp.get("asset"):  # keep small copies of uploaded images for canvas/deck thumbnails
        artlib.cache_assets(_text(data.get("tool_response")))
        return None
    text = _text(data.get("tool_response"))
    aid = artlib.artifact_id(text) or artlib.artifact_id(inp.get("url"))
    if not aid:
        return None
    fp = inp.get("file_path") or ""
    typed = fp.endswith(".json") or bool(inp.get("type_url")) or (not fp and bool(inp.get("files")))
    page, tp = None, None
    if typed or "/project/" in fp:
        proj = artlib.project_dir(fp, inp.get("root"))
        tp = artlib.typed_page(proj) if proj else None
    if tp:
        page, typed = tp["html"], True
    elif fp.endswith((".html", ".htm")) and Path(fp).is_file() and artlib.type_for(fp, typed) == "page":
        page = Path(fp).read_text(errors="ignore")
    sid = data.get("session_id") or env.get("CLAUDE_CODE_SESSION_ID") or None
    try:
        host, title = artlib.recent_session(sid)
    except OSError:  # a session file vanished mid-scan: register without the conversation link
        host, title = "", ""
    known = artlib.get(aid) or {}
    artlib.upsert(aid, url=f"https://claude.ai/artifact/{aid}", title=inp.get("title") or (tp or {}).get("title", ""),
                  about=inp.get("description") or "", source_file=fp or None, page_html=page,
                  kind="typed" if typed else "html",
                  type=tp["type"] if tp else _type(known, artlib.type_for(fp, typed, inp.get("type_url"))),
                  headings=tp["headings"] if tp else "", body=tp["body"] if tp else "", area=known.get("area") or artlib.area_for(data.get("cwd"), f"{inp.get('title') or ''} {inp.get('description') or ''} {fp}"),
                  project_dir=data.get("cwd") or None, session_id=sid,
                  host_session_id=env.get("CLAUDE_CODE_HOST_SESSION_ID") or host or None, session_title=title)
    spawn(aid)
    a = artlib.get(aid) or {}
    if a.get("tags"):
        return None
    about = "" if a.get("about") else ' --about "<one sentence on what the page says>"'
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": ASK.format(py="/usr/bin/python3", cli=ARTLIB, aid=aid, about=about)}}


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        out = handle(data if isinstance(data, dict) else {}, dict(os.environ))
        if out:
            print(json.dumps(out))
    except Exception:
        artlib.log(f"hook: {traceback.format_exc()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
