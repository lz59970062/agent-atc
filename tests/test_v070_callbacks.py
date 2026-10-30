"""Requirement tests: short callbacks retain actionable, private evidence.

No token counter is assumed. Character budgets catch unbounded command/message
regressions while the original evidence must remain available on disk.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_reins.core import envelope
from harness_reins import cli
from harness_reins.engine import dispatch
from harness_reins.engine import reminders as reminder_policy
from harness_reins import runtime as reins_runtime
from harness_reins.store import layout
from test_cli import assert_private_file


class CompactCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="reins callback ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "queue's data"
        self.root.mkdir()

    def make_callback(self, ident: str, **overrides: object) -> tuple[dict[str, object], str]:
        arguments = dict(
            agent="codex", session="bound-original-session", last=False,
            task="Compare candidate with frozen reference", cwd=str(self.directory),
            command="python compare.py --frozen reference.json", exit_code=0,
            message=None, queue_dir=str(self.root), _callback_id=ident,
        )
        arguments.update(overrides)
        args = argparse.Namespace(**arguments)
        prompt = dispatch.build_prompt(args, duration=125.5)
        request = dispatch.make_request(args, prompt, duration=125.5)
        return request, prompt

    def prepare(self, ident: str, **overrides: object) -> dict[str, object]:
        request, prompt = self.make_callback(ident, **overrides)
        return dispatch.prepare_request_for_queue(self.root, request, prompt)

    def test_new_default_sends_short_status_and_keeps_exact_full_evidence(self) -> None:
        command = "python train.py --configuration " + "very-long-argument=" * 700
        message = "HANDOFF: inspect held-out failures.\n" + "evidence detail " * 900
        request, full = self.make_callback("compact-result", command=command, message=message)
        prepared = dispatch.prepare_request_for_queue(self.root, request, full)
        self.assertEqual(prepared["prompt_format"], "reins-compact/1")
        compact = prepared["prompt_compact"]
        self.assertLess(len(compact), 1200)
        self.assertLess(len(prepared["prompt"]), 1500)
        self.assertNotIn(command, compact)
        self.assertNotIn(message, compact)
        self.assertIn("Result: exited 0 · 2m 5.5s", compact)
        for essential in ("compact-result", "bound-original-session", "ack", str(self.root)):
            self.assertIn(essential, compact)
        details = Path(prepared["prompt_details_path"])
        self.assertTrue(details.is_file())
        self.assertIn(full, details.read_text(encoding="utf-8"))
        assert_private_file(self, details)
        self.assertIn(str(details), compact)
        self.assertIn("read", compact.lower())
        self.assertIn("details", compact.lower())

    def test_duration_stays_fixed_across_delivery_cadence_and_retry(self) -> None:
        for ident in ("runtime-first", "runtime-next"):
            request = self.prepare(ident)
            restored = json.loads(json.dumps(request))
            payload = dict(prompt=restored["prompt"], request=restored,
                           queue_dir=str(self.root))
            with mock.patch.object(cli.time, "time", return_value=9999999999):
                selected = reminder_policy.select_delivery_prompt(payload)
                retried = reminder_policy.select_delivery_prompt(payload)
            self.assertEqual(restored["duration_seconds"], 125.5)
            self.assertIn("Result: exited 0 · 2m 5.5s", selected)
            self.assertEqual(selected, retried)

    def test_duration_handles_long_runs_and_rounding_without_fabricating_values(self) -> None:
        for seconds, expected in ((0, "0.0s"), (12.5, "12.5s"),
                                  (59.96, "1m 0.0s"), (3600, "1h 0.0s"),
                                  (90061.2, "1d 1h 1m 1.2s")):
            with self.subTest(seconds=seconds):
                self.assertEqual(envelope.format_duration(seconds), expected)
        for seconds in (None, True, -1, "12.5", float("nan"), float("inf")):
            with self.subTest(seconds=seconds):
                request, prompt = self.make_callback("runtime-unknown")
                request["duration_seconds"] = seconds
                prepared = dispatch.prepare_request_for_queue(self.root, request, prompt)
                self.assertIn("Result: exited 0\n", prepared["prompt_compact"])

    def test_ack_line_can_be_executed_with_spaces_and_quotes_without_wrong_queue(self) -> None:
        launcher = "/opt/Reins user's tools/reins"
        with mock.patch.object(reins_runtime, "console_script_path", return_value=launcher):
            prepared = self.prepare("ack-correct-queue")
        lines = prepared["prompt_compact"].splitlines()
        prefix = "ACK after inspection: "
        candidates = [line.removeprefix(prefix) for line in lines if line.startswith(prefix)]
        self.assertEqual(len(candidates), 1)
        if os.name == "nt":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            self.assertIsNotNone(shell, "Windows ACK contract requires PowerShell")
            result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", candidates[0]],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            ack = layout.request_path(self.root, "acks", "ack-correct-queue")
            self.assertEqual(json.loads(ack.read_text(encoding="utf-8"))["id"], "ack-correct-queue")
        else:
            self.assertEqual(
                shlex.split(candidates[0]),
                [launcher, "ack", "--queue-dir", str(self.root), "ack-correct-queue"],
            )

    def test_default_queue_ack_is_just_the_callback_id(self) -> None:
        launcher = "/usr/local/bin/reins"
        with mock.patch.object(reins_runtime, "console_script_path", return_value=launcher), \
                mock.patch.object(layout, "queue_dir", return_value=self.root):
            prepared = self.prepare("default-queue")
            expected = reins_runtime.control_command("ack", "default-queue")  # Windows ignores the script
        self.assertIn(f"ACK after inspection: {expected}", prepared["prompt_compact"])
        self.assertNotIn("--queue-dir", prepared["prompt_compact"])

    def test_ack_takes_the_id_positionally_or_by_flag(self) -> None:
        from harness_reins.commands import callbacks as callback_commands
        for name, positional, flag in (("positional", "cb-one", None), ("flag", None, "cb-one"), ("both", "cb-one", "cb-one")):
            with self.subTest(name=name), mock.patch("sys.stderr"):
                args = argparse.Namespace(queue_dir=str(self.root / name), callback_id=positional, id=flag, message=None)
                self.assertEqual(callback_commands.ack(args), 0)
                self.assertTrue(layout.ack_path(self.root / name, "cb-one").exists())
        for positional, flag in (("cb-one", "cb-two"), (None, None)):
            with self.subTest(positional=positional, flag=flag), mock.patch("sys.stderr"):
                args = argparse.Namespace(queue_dir=str(self.root), callback_id=positional, id=flag, message=None)
                self.assertEqual(callback_commands.ack(args), 2)

    def test_from_line_names_who_did_the_work(self) -> None:
        request, _ = self.make_callback("source")
        self.assertEqual(envelope.source_text(request), "external job (reins done)")
        request.update(managed_task_id="1a2b3c4d", execution_backend="systemd-user")
        self.assertEqual(envelope.source_text(request), "run on systemd-user")
        request.update(task_kind="agent", agent_worker="claude", child_model="opus", agent_template="writer")
        self.assertEqual(envelope.source_text(request), "agent claude, model opus, template writer")

    def test_unknown_outcome_is_visible_and_full_recovery_guidance_is_retained(self) -> None:
        request, full = self.make_callback(
            "unknown-result", exit_code=None,
            message="The task was not automatically restarted. Inspect checkpoints before recreating work.",
        )
        request.update(outcome="unknown", recovery_reason="worker_disappeared_before_completion")
        prepared = dispatch.prepare_request_for_queue(self.root, request, full)
        self.assertIn("unknown", prepared["prompt_compact"].lower())
        self.assertNotIn("exit=0", prepared["prompt_compact"].lower())
        self.assertIn("read", prepared["prompt_compact"].lower())
        details = Path(prepared["prompt_details_path"]).read_text(encoding="utf-8")
        self.assertIn("not automatically restarted", details)
        self.assertIn("checkpoints", details)

    def test_explicit_full_format_remains_available(self) -> None:
        message = "USER REQUESTED FULL EVIDENCE " * 100
        request, full = self.make_callback("full-request", message=message, callback_format="full")
        prepared = dispatch.prepare_request_for_queue(self.root, request, full)
        self.assertNotEqual(prepared.get("prompt_format"), "reins-compact/1")
        self.assertIn(message, prepared["prompt"])

    def test_legacy_request_is_not_silently_reinterpreted_or_loses_handoff(self) -> None:
        request, full = self.make_callback("legacy", message="LEGACY HANDOFF: examine output carefully")
        request.pop("prompt_format", None)
        prepared = dispatch.prepare_request_for_queue(self.root, request, full)
        self.assertIn("LEGACY HANDOFF: examine output carefully", prepared["prompt"])
        self.assertIn("LEGACY HANDOFF: examine output carefully", prepared["prompt_compact"])
        self.assertIn("bound-original-session", prepared["prompt_compact"])

    def test_new_callback_cadence_has_no_bookkeeping_prose_and_retry_stays_stable(self) -> None:
        hook = self.directory / "callback-prompt.md"
        hook.write_text("USER REMINDER: preserve negative evidence", encoding="utf-8")
        outputs = []
        requests = []
        for sequence in range(1, 6):
            request = self.prepare(f"cadence-{sequence}")
            requests.append(request)
            payload = dict(prompt=request["prompt"], request=request,
                           queue_dir=str(self.root), callback_hook_path=str(hook))
            selected = reminder_policy.select_delivery_prompt(payload)
            self.assertEqual(payload["prompt"], selected)
            self.assertNotIn("Reminder cadence:", selected)
            self.assertEqual("USER REMINDER" in selected, sequence in (1, 4))
            self.assertIn("bound-original-session", selected)
            self.assertIn("ack", selected)
            outputs.append(selected)
        request = json.loads(json.dumps(requests[1]))
        retry = dict(prompt=request["prompt"], request=request,
                     queue_dir=str(self.root), callback_hook_path=str(hook))
        self.assertEqual(reminder_policy.select_delivery_prompt(retry), outputs[1])
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
