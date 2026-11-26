from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import install  # noqa: E402

OTHER = {"matcher": "Artifact", "hooks": [{"type": "command", "command": "python3 ~/my-own-hook.py"}]}


class TestInstall(unittest.TestCase):
    def test_add_is_idempotent_and_keeps_other_hooks(self):
        s = {"model": "x", "hooks": {"PostToolUse": [OTHER]}}
        s = install.add_hook(install.add_hook(s))
        groups = s["hooks"]["PostToolUse"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0], OTHER)
        self.assertEqual(groups[1]["matcher"], "Artifact")
        self.assertIn("hook.py", groups[1]["hooks"][0]["command"])
        self.assertTrue(install.has_hook(s))
        self.assertEqual(s["model"], "x")

    def test_remove_leaves_other_hooks(self):
        s = install.add_hook({"hooks": {"PostToolUse": [OTHER]}})
        s = install.remove_hook(s)
        self.assertEqual(s["hooks"]["PostToolUse"], [OTHER])
        self.assertFalse(install.has_hook(s))

    def test_cli_writes_backup_and_refuses_broken_json(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            settings.write_text(json.dumps({"hooks": {"PostToolUse": [OTHER]}}))
            env = {**os.environ, "ARTLIB_SETTINGS": str(settings), "ARTLIB_ROOT": str(Path(d) / "lib")}
            r = subprocess.run([sys.executable, str(SCRIPTS / "install.py")], capture_output=True, text=True, env=env)
            out = json.loads(r.stdout)
            if not out["pillow"]:
                self.skipTest("no Pillow for /usr/bin/python3 here")
            self.assertTrue(out["hook"])
            self.assertTrue(Path(out["backup"]).exists())
            self.assertTrue(install.has_hook(json.loads(settings.read_text())))
            settings.write_text("{broken")
            r = subprocess.run([sys.executable, str(SCRIPTS / "install.py")], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(settings.read_text(), "{broken")


if __name__ == "__main__":
    unittest.main()
