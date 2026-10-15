"""atc migrate: one explicit, refusable step from Long Task Callback to ATC."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_atc.harness.pi import channel as pi_channel
from agent_atc.install import migrate


class MigrateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.environment = {
            "HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex"), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
            "ATC_HOME": str(self.home / ".atc"), pi_channel.PROFILE_ENV: str(self.home / ".pi" / "agent"),
            "XDG_CONFIG_HOME": str(self.home / ".config"), "PATH": "",
        }
        patcher = mock.patch.dict(os.environ, self.environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ltc = self.home / ".ltc"
        for state in ("pending", "running", "done", "acks"):
            (self.ltc / "queue" / state).mkdir(parents=True)
        (self.ltc / "queue" / "done" / "old.json").write_text("{}", encoding="utf-8")
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "long-task-wakeup").symlink_to(self.ltc, target_is_directory=True)
        for skills in (self.home / ".codex" / "skills", self.home / ".claude" / "skills", self.home / ".pi" / "agent" / "skills"):
            (skills / "long-task-callback").mkdir(parents=True)
            (skills / "long-task-callback" / "SKILL.md").write_text("ltc", encoding="utf-8")
        (self.home / ".pi" / "agent" / "extensions").mkdir()
        (self.home / ".pi" / "agent" / "extensions" / "ltc-callback.js").write_text("//", encoding="utf-8")
        (self.home / ".pi" / "agent" / "extensions" / "mine.js").write_text("//", encoding="utf-8")
        self.unit = self.home / ".config" / "systemd" / "user" / "codex-long-task-wakeup.service"
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text("[Unit]\n", encoding="utf-8")
        self.rc = self.home / ".bashrc"
        self.rc.write_text(f"export A=1\n{migrate.OLD_HOOK_BEGIN}\nltc status\n{migrate.OLD_HOOK_END}\nexport B=2\n",
                           encoding="utf-8")

    def run_command(self, **options) -> tuple[int, str]:
        args = argparse.Namespace(**{"apply": False, "wait": False, **options})
        output = io.StringIO()
        with mock.patch.object(migrate.subprocess, "run") as run, mock.patch.object(Path, "home", return_value=self.home), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            run.return_value = mock.Mock(returncode=0, stderr="")
            status = migrate.command(args)
        self.commands = [call.args[0] for call in run.call_args_list]
        return status, output.getvalue()

    def test_without_apply_it_only_describes_the_plan(self):
        status, output = self.run_command()
        self.assertEqual(status, 0)
        for expected in ("archive LTC state", "remove legacy link", "systemd user service", "long-task-callback",
                         "ltc-callback.js", "status hook", "--apply"):
            self.assertIn(expected, output)
        self.assertTrue(self.ltc.is_dir())
        self.assertTrue(self.unit.exists())
        self.assertEqual(self.commands, [])

    def test_live_ltc_work_blocks_migration_even_with_apply(self):
        (self.ltc / "queue" / "pending" / "cb.json").write_text("{}", encoding="utf-8")
        (self.ltc / "tasks" / "t1").mkdir(parents=True)
        (self.ltc / "tasks" / "t1" / "task.json").write_text(json.dumps({"state": "running"}), encoding="utf-8")
        status, output = self.run_command(apply=True)
        self.assertEqual(status, 1)
        self.assertIn("callback cb (pending)", output)
        self.assertIn("task t1 (running)", output)
        self.assertTrue(self.ltc.is_dir())
        self.assertEqual(self.commands, [])

    def test_apply_archives_state_and_removes_the_old_installation(self):
        status, output = self.run_command(apply=True)
        self.assertEqual(status, 0, output)
        archives = list(self.home.glob(".ltc.migrated-*"))
        self.assertEqual(len(archives), 1)
        self.assertTrue((archives[0] / "queue" / "done" / "old.json").exists(), "history is kept unchanged")
        self.assertFalse(self.ltc.exists())
        self.assertFalse((self.home / ".codex" / "long-task-wakeup").is_symlink())
        self.assertFalse(self.unit.exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", "codex-long-task-wakeup.service"], self.commands)
        for skills in (".codex/skills", ".claude/skills", ".pi/agent/skills"):
            self.assertFalse((self.home / skills / "long-task-callback").exists())
        self.assertFalse((self.home / ".pi" / "agent" / "extensions" / "ltc-callback.js").exists())
        self.assertTrue((self.home / ".pi" / "agent" / "extensions" / "mine.js").exists())
        self.assertEqual(self.rc.read_text(encoding="utf-8"), "export A=1\nexport B=2\n")
        self.assertIn("atc setup", output)

    def test_the_atc_home_is_never_treated_as_ltc_state(self):
        os.environ["LTC_HOME"] = os.environ["ATC_HOME"]
        (self.home / ".atc").mkdir()
        plan = migrate.build_plan(home=self.home)
        self.assertNotIn((self.home / ".atc").resolve(), plan.state_dirs)

    def test_nothing_to_do_on_a_clean_machine(self):
        for path in (self.home / ".codex" / "long-task-wakeup",):
            path.unlink()
        import shutil
        for path in (self.ltc, self.home / ".codex" / "skills", self.home / ".claude", self.home / ".pi", self.unit.parent):
            shutil.rmtree(path)
        self.rc.write_text("export A=1\n", encoding="utf-8")
        status, output = self.run_command(apply=True)
        self.assertEqual(status, 0)
        self.assertIn("nothing to do", output)


if __name__ == "__main__":
    unittest.main()
