"""reins migrate: one explicit, refusable step from ATC or Long Task Callback to Reins."""
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

from harness_reins.harness.pi import channel as pi_channel
from harness_reins.install import migrate


class MigrateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.environment = {
            "HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex"), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
            "REINS_HOME": str(self.home / ".reins"), pi_channel.PROFILE_ENV: str(self.home / ".pi" / "agent"),
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
        args = argparse.Namespace(**{"apply": False, "wait": False, "only": None, **options})
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
        self.assertIn("reins setup", output)

    def test_the_atc_home_is_never_treated_as_ltc_state(self):
        os.environ["LTC_HOME"] = os.environ["REINS_HOME"]
        (self.home / ".reins").mkdir()
        plan = migrate.build_plan(home=self.home)
        self.assertNotIn((self.home / ".reins").resolve(), plan.state_dirs)

    def add_atc(self) -> Path:
        atc = self.home / ".atc"
        for state in ("pending", "running", "done"):
            (atc / "queue" / state).mkdir(parents=True)
        (atc / "experts.yaml").write_text("schema: atc.experts/1\nexperts: {}\n", encoding="utf-8")
        for skills in (self.home / ".codex" / "skills", self.home / ".claude" / "skills"):
            (skills / "agent-atc").mkdir(parents=True)
        (self.home / ".pi" / "agent" / "extensions" / "atc-callback.js").write_text("//", encoding="utf-8")
        units = self.unit.parent
        (units / "agent-atc.service").write_text("[Unit]\n", encoding="utf-8")
        (units / "agent-atc.service.d").mkdir()
        (units / "agent-atc.service.d" / "allow.conf").write_text("[Service]\nNoNewPrivileges=no\n", encoding="utf-8")
        with self.rc.open("a", encoding="utf-8") as rc:
            rc.write(f"{migrate.ATC.hook_begin}\natc status\n{migrate.ATC.hook_end}\n")
        return atc

    def test_an_idle_atc_moves_while_a_busy_ltc_is_left_alone(self):
        atc = self.add_atc()
        (self.ltc / "queue" / "pending" / "cb.json").write_text("{}", encoding="utf-8")
        status, output = self.run_command(apply=True)
        self.assertEqual(status, 1, "LTC still owns work")
        self.assertIn("LTC still owns this work", output)
        self.assertIn("Migrated Agent Traffic Control (ATC)", output)
        self.assertTrue(self.ltc.is_dir())
        self.assertTrue(self.unit.exists())
        self.assertFalse(atc.exists())
        self.assertEqual(len(list(self.home.glob(".atc.migrated-*"))), 1)
        units = self.unit.parent
        self.assertFalse((units / "agent-atc.service").exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", "agent-atc.service"], self.commands)
        self.assertNotIn(["systemctl", "--user", "disable", "--now", "codex-long-task-wakeup.service"], self.commands)
        self.assertEqual((units / "harness-reins.service.d" / "allow.conf").read_text(encoding="utf-8"),
                         "[Service]\nNoNewPrivileges=no\n", "the user's drop-in keeps applying")
        self.assertFalse((units / "agent-atc.service.d").exists())
        experts = self.home / ".reins" / "experts.yaml"
        self.assertIn("schema: reins.experts/1", experts.read_text(encoding="utf-8"))
        self.assertEqual(experts.stat().st_mode & 0o777, 0o600)
        for skills in (".codex/skills", ".claude/skills"):
            self.assertFalse((self.home / skills / "agent-atc").exists())
            self.assertTrue((self.home / skills / "long-task-callback").exists(), "LTC is untouched")
        self.assertFalse((self.home / ".pi" / "agent" / "extensions" / "atc-callback.js").exists())
        rc = self.rc.read_text(encoding="utf-8")
        self.assertNotIn(migrate.ATC.hook_begin, rc)
        self.assertIn(migrate.LTC.hook_begin, rc, "LTC's hook stays until LTC moves")

    def test_only_limits_migration_to_one_generation(self):
        atc = self.add_atc()
        status, output = self.run_command(apply=True, only="ltc")
        self.assertEqual(status, 0, output)
        self.assertTrue(atc.is_dir())
        self.assertFalse(self.ltc.exists())
        self.assertNotIn("Agent Traffic Control", output)

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
