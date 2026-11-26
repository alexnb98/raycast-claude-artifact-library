#!/usr/bin/env python3
"""Install the Claude Code side of the Artifact Library. Safe to run again.

  install.py            check Pillow (installs it for macOS's Python if missing), create the library
                        folder, add the PostToolUse hook to ~/.claude/settings.json (backup first)
  install.py --check    report what is missing, change nothing (exit 1 if anything is)
  install.py --remove   take the hook out of ~/.claude/settings.json again (library data stays)

Prints one JSON object with what it found and did.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "hook.py"
PYTHON = "/usr/bin/python3"  # macOS's own Python: always there, and Raycast calls the same one


def settings_path() -> Path:
    return Path(os.environ.get("ARTLIB_SETTINGS") or Path.home() / ".claude" / "settings.json")


def library_root() -> Path:
    return Path(os.environ.get("ARTLIB_ROOT") or Path.home() / "Documents" / "Artifacts")


def hook_command() -> str:
    return f'{PYTHON} "{HOOK}"'


def _is_ours(h: dict) -> bool:
    return isinstance(h, dict) and str(HOOK.name) in str(h.get("command", "")) and "artifact-library" in str(
        h.get("command", ""))


def has_hook(settings: dict) -> bool:
    for group in (settings.get("hooks") or {}).get("PostToolUse") or []:
        if isinstance(group, dict) and any(_is_ours(h) for h in group.get("hooks") or []):
            return True
    return False


def add_hook(settings: dict) -> dict:
    """The hook as its own PostToolUse group on the Artifact tool. Existing hooks are left alone."""
    if has_hook(settings):
        return settings
    groups = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    groups.append({"matcher": "Artifact", "hooks": [{"type": "command", "command": hook_command(), "timeout": 10}]})
    return settings


def remove_hook(settings: dict) -> dict:
    groups = (settings.get("hooks") or {}).get("PostToolUse") or []
    for g in groups:
        if isinstance(g, dict):
            g["hooks"] = [h for h in g.get("hooks") or [] if not _is_ours(h)]
    settings.get("hooks", {})["PostToolUse"] = [g for g in groups if not isinstance(g, dict) or g.get("hooks")]
    return settings


def read_settings() -> dict:
    p = settings_path()
    if not p.exists():
        return {}
    d = json.loads(p.read_text())  # a broken settings file stops the install: never overwrite it
    return d if isinstance(d, dict) else {}


def write_settings(d: dict) -> str:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    backup = ""
    if p.exists():
        backup = str(p.with_name(f"settings.json.bak-{datetime.now():%Y%m%d-%H%M%S}"))
        shutil.copy2(p, backup)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(p)
    return backup


def has_pillow() -> bool:
    if not Path(PYTHON).exists():
        return False
    return subprocess.run([PYTHON, "-c", "import PIL"], capture_output=True).returncode == 0


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    out: dict = {"python": Path(PYTHON).exists(), "pillow": has_pillow(), "library": str(library_root()),
                 "settings": str(settings_path())}
    try:
        settings = read_settings()
    except ValueError as e:
        out["error"] = f"{settings_path()} is not valid JSON ({e}); fix it first, nothing was changed"
        print(json.dumps(out))
        return 1
    out["hook"] = has_hook(settings)

    if mode == "--check":
        print(json.dumps(out))
        return 0 if out["python"] and out["pillow"] and out["hook"] else 1
    if mode == "--remove":
        if out["hook"]:
            out["backup"] = write_settings(remove_hook(settings))
        out["hook"] = False
        print(json.dumps(out))
        return 0

    if not out["python"]:
        out["error"] = f"{PYTHON} not found: install Apple's command line tools with `xcode-select --install`"
        print(json.dumps(out))
        return 1
    if not out["pillow"]:
        r = subprocess.run([PYTHON, "-m", "pip", "install", "--user", "--quiet", "Pillow"], capture_output=True,
                           text=True)
        out["pillow"] = has_pillow()
        if not out["pillow"]:
            out["error"] = f"could not install Pillow: {(r.stderr or r.stdout).strip()[-300:]}"
            print(json.dumps(out))
            return 1
        out["installed_pillow"] = True
    library_root().mkdir(parents=True, exist_ok=True)
    if not out["hook"]:
        out["backup"] = write_settings(add_hook(settings))
        out["hook"] = True
        out["added_hook"] = True
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
