"""Fault-oriented ownership tests; no daemon, screen, or systemd is started."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_atc import cli
from agent_atc.commands import work as work_commands
from agent_atc.engine import dispatch
from agent_atc.engine import tasks as managed_tasks
from agent_atc.engine import worker as task_worker
from agent_atc.owners import host
from agent_atc.store import records as queue_store
from agent_atc.store import layout
from agent_atc.owners import LaunchError, OwnerState, ScreenBackend, SystemdUserBackend
from agent_atc.core import ids
import atc_records


class NativeTaskLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        platform = mock.patch.object(sys, "platform", "linux")
        platform.start()
        self.addCleanup(platform.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "queue"
        available = mock.patch.object(SystemdUserBackend, "available", return_value=True)
        available.start()
        self.addCleanup(available.stop)

    def submit(self) -> Path:
        args = argparse.Namespace(
            backend="systemd", agent="codex", cwd=str(self.directory), task="native fixture workload",
            command=None, exit_code=None, message=None, session="bound-native-session", last=False,
            via_daemon=False, queue_dir=str(self.root), approvals_reviewer="auto_review",
            approval_policy="on-request", sandbox_mode="workspace-write", dry_run=False,
            strict=False, wrapped_command=[sys.executable, "-c", "raise SystemExit(7)"],
        )
        with mock.patch.object(managed_tasks, "screen_binary", return_value=None), mock.patch.object(
            SystemdUserBackend, "launch"
        ) as launch:
            self.assertEqual(work_commands.run(args), 0)
            launch.assert_not_called()
        return next(layout.managed_tasks_root(self.root).glob("*/task.json"))

    def mark_running(self, path: Path) -> dict[str, object]:
        with mock.patch.object(SystemdUserBackend, "launch"):
            managed_tasks.recover_managed_tasks(self.root)
        task = managed_tasks.load_managed_task(path)
        task.update(state="running", boot_id=host.current_boot_id(), started_at=time.time())
        managed_tasks.write_managed_task(self.root, task)
        return task

    def test_submission_and_recovery_launch_independent_owner_once_without_screen(self) -> None:
        path = self.submit()
        submitted = managed_tasks.load_managed_task(path)
        self.assertEqual(submitted["execution_backend"], "systemd-user")
        self.assertEqual(submitted["schema"], "atc.task/1")
        with mock.patch.object(SystemdUserBackend, "launch") as launch, mock.patch.object(
            SystemdUserBackend, "probe", return_value=OwnerState.ALIVE
        ), mock.patch.object(managed_tasks, "launch_managed_screen") as screen:
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
        self.assertEqual(launch.call_count, 1)
        screen.assert_not_called()
        owner, argv, cwd, log = launch.call_args.args
        task = managed_tasks.load_managed_task(path)
        self.assertEqual(owner, task["execution_owner"])
        self.assertEqual(str(cwd), str(self.directory))
        self.assertEqual(str(log), task["log_path"])
        self.assertIn("_task-worker", argv)
        self.assertIn(str(path), argv)
        self.assertNotIn(task["command"], argv)
        self.assertFalse(any(argument.startswith("--token") for argument in argv))
        self.assertEqual(task["launch_attempt_count"], 1)

    def test_explicit_native_selection_does_not_silently_fall_back_to_screen(self) -> None:
        with mock.patch.object(SystemdUserBackend, "available", return_value=False), mock.patch.object(
            managed_tasks, "screen_binary", return_value="/usr/bin/screen"
        ):
            with self.assertRaises(ValueError):
                managed_tasks.select_execution_backend(argparse.Namespace(backend="systemd"))
            self.assertEqual(managed_tasks.select_execution_backend(argparse.Namespace(backend="auto")), "screen")
        self.assertFalse(layout.managed_tasks_root(self.root).exists())

    def test_manager_query_failure_does_not_interrupt_or_duplicate_running_work(self) -> None:
        path = self.submit()
        task = self.mark_running(path)
        with mock.patch.object(SystemdUserBackend, "probe", return_value=OwnerState.UNKNOWN), mock.patch.object(
            SystemdUserBackend, "launch"
        ) as launch:
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
        launch.assert_not_called()
        self.assertEqual(managed_tasks.load_managed_task(path)["state"], "running")
        self.assertFalse(layout.request_path(self.root, "pending", ids.callback_id(str(task["id"]))).exists())

    def test_ambiguous_launch_is_never_reissued_even_after_owner_disappears(self) -> None:
        path = self.submit()
        with mock.patch.object(
            SystemdUserBackend, "launch", side_effect=LaunchError("reply lost", uncertain=True)
        ) as launch, mock.patch.object(SystemdUserBackend, "probe", return_value=OwnerState.UNKNOWN):
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(managed_tasks.load_managed_task(path)["state"], "launching")
        with mock.patch.object(SystemdUserBackend, "probe", return_value=OwnerState.ABSENT), mock.patch.object(
            SystemdUserBackend, "launch"
        ) as relaunch, mock.patch.object(managed_tasks, "MANAGED_WORKER_HANDSHAKE_SECONDS", 0):
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
        relaunch.assert_not_called()
        task = managed_tasks.load_managed_task(path)
        self.assertEqual(task["state"], "interrupted")
        self.assertEqual(task["outcome"], "unknown")
        self.assertEqual(len(list((self.root / "pending").glob("*.json"))), 1)

    def test_persisted_result_wins_over_lost_manager_and_callback_is_idempotent(self) -> None:
        path = self.submit()
        task = self.mark_running(path)
        result = atc_records.result_record(task, exit_code=7, ended_at=time.time())
        queue_store.write_request(layout.managed_result_path(self.root, str(task["id"])), result)
        with mock.patch.object(SystemdUserBackend, "probe", return_value=OwnerState.UNKNOWN), mock.patch.object(
            SystemdUserBackend, "launch"
        ) as launch:
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
        launch.assert_not_called()
        completed = managed_tasks.load_managed_task(path)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["schema"], "atc.task/1")
        self.assertEqual(completed["execution_backend"], "systemd-user")
        pending = list((self.root / "pending").glob("*.json"))
        self.assertEqual(len(pending), 1)
        callback = dispatch.load_request(pending[0])
        self.assertEqual(callback["id"], ids.callback_id(task["id"]))
        self.assertEqual(callback["exit_code"], 7)
        self.assertEqual(callback["target"], {"kind": "session", "session": "bound-native-session"})

    def test_callback_runtime_excludes_queue_and_delivery_delays(self) -> None:
        path = self.submit()
        task = self.mark_running(path)
        task["started_at"] = 100.0
        managed_tasks.write_managed_task(self.root, task)
        queue_store.write_request(layout.managed_result_path(self.root, str(task["id"])),
                                  atc_records.result_record(task, exit_code=0, ended_at=225.5))
        with mock.patch.object(cli.time, "time", return_value=10000.0):
            managed_tasks.recover_managed_tasks(self.root)
        callback = dispatch.load_request(layout.request_path(self.root, "pending", ids.callback_id(str(task["id"]))))
        self.assertEqual(callback["duration_seconds"], 125.5)
        self.assertIn("Result: exited 0 · 2m 5.5s", callback["prompt_compact"])
        self.assertIn("Duration: 125.5s", Path(callback["prompt_details_path"]).read_text())

    def test_foreign_result_identity_cannot_mark_task_complete(self) -> None:
        path = self.submit()
        task = self.mark_running(path)
        queue_store.write_request(layout.managed_result_path(self.root, str(task["id"])),
                                  atc_records.result_record({"id": "different-task"}, exit_code=0, ended_at=time.time()))
        with mock.patch.object(SystemdUserBackend, "probe", return_value=OwnerState.ABSENT), mock.patch.object(
            SystemdUserBackend, "launch"
        ) as launch:
            managed_tasks.recover_managed_tasks(self.root)
        launch.assert_not_called()
        self.assertEqual(managed_tasks.load_managed_task(path)["state"], "running")
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])

    def test_host_reboot_marks_unknown_outcome_without_relaunch(self) -> None:
        path = self.submit()
        task = self.mark_running(path)
        task["boot_id"] = "prior-host-boot"
        managed_tasks.write_managed_task(self.root, task)
        with mock.patch.object(host, "current_boot_id", return_value="new-host-boot"), mock.patch.object(
            SystemdUserBackend, "launch"
        ) as launch:
            managed_tasks.recover_managed_tasks(self.root)
        launch.assert_not_called()
        recovered = managed_tasks.load_managed_task(path)
        self.assertEqual(recovered["state"], "interrupted")
        self.assertEqual(recovered["recovery_reason"], "interrupted_by_host_reboot")

    def test_task_without_a_backend_is_refused(self) -> None:
        path = self.submit()
        task = managed_tasks.load_managed_task(path)
        task.pop("execution_backend")
        managed_tasks.write_managed_task(self.root, task)
        with self.assertRaisesRegex(ValueError, "execution backend"):
            managed_tasks.load_managed_task(path)

    def test_unknown_screen_owner_is_not_evidence_to_interrupt_or_relaunch(self) -> None:
        path = self.submit()
        task = managed_tasks.load_managed_task(path)
        task.update(execution_backend="screen", state="running", boot_id=host.current_boot_id())
        managed_tasks.write_managed_task(self.root, task)
        with mock.patch.object(managed_tasks, "screen_owner_state", return_value=OwnerState.UNKNOWN), mock.patch.object(
            managed_tasks, "launch_managed_screen"
        ) as relaunch:
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
        relaunch.assert_not_called()
        latest = managed_tasks.load_managed_task(path)
        self.assertEqual(latest["state"], "running")
        self.assertNotIn("recovery_reason", latest)
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])

    def test_unknown_screen_probe_cannot_authorize_submitted_work_to_launch(self) -> None:
        path = self.submit()
        task = managed_tasks.load_managed_task(path)
        task.update(execution_backend="screen")
        managed_tasks.write_managed_task(self.root, task)
        with mock.patch.object(managed_tasks, "screen_binary", return_value="/usr/bin/screen"), mock.patch.object(
            managed_tasks, "screen_owner_state", return_value=OwnerState.UNKNOWN
        ), mock.patch.object(ScreenBackend, "launch") as launch:
            managed_tasks.recover_managed_tasks(self.root)
        launch.assert_not_called()
        self.assertEqual(managed_tasks.load_managed_task(path)["state"], "submitted")

    def test_ambiguous_screen_launch_reply_never_retries_disappeared_owner(self) -> None:
        path = self.submit()
        task = managed_tasks.load_managed_task(path)
        task.update(execution_backend="screen")
        managed_tasks.write_managed_task(self.root, task)
        with mock.patch.object(managed_tasks, "screen_binary", return_value="/usr/bin/screen"), mock.patch.object(
            managed_tasks, "screen_owner_state", return_value=OwnerState.ABSENT
        ), mock.patch.object(ScreenBackend, "launch", side_effect=LaunchError("reply lost", uncertain=True)) as launch, mock.patch.object(
            managed_tasks, "MANAGED_WORKER_HANDSHAKE_SECONDS", 0
        ):
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
        self.assertEqual(launch.call_count, 1)
        latest = managed_tasks.load_managed_task(path)
        self.assertEqual(latest["state"], "interrupted")
        self.assertEqual(latest["outcome"], "unknown")
        self.assertEqual(len(list((self.root / "pending").glob("*.json"))), 1)

    def test_native_worker_rejects_missing_invocation_stale_attempt_and_replay(self) -> None:
        path = self.submit()
        with mock.patch.object(SystemdUserBackend, "launch"):
            managed_tasks.recover_managed_tasks(self.root)
        worker = argparse.Namespace(task_file=str(path), attempt=1)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(cli.subprocess, "run") as command:
            self.assertEqual(task_worker.run_task_worker(worker), 125)
            command.assert_not_called()
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "fixture-invocation"}, clear=True), mock.patch.object(
            cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)
        ) as command:
            worker.attempt = 2
            self.assertEqual(task_worker.run_task_worker(worker), 125)
            command.assert_not_called()
            worker.attempt = 1
            self.assertEqual(task_worker.run_task_worker(worker), 7)
            self.assertEqual(task_worker.run_task_worker(worker), 125)
            self.assertEqual(command.call_count, 1)
        task = managed_tasks.load_managed_task(path)
        self.assertEqual(task["state"], "completed")
        self.assertEqual(task["exit_code"], 7)
        self.assertEqual(task["schema"], "atc.task/1")
        self.assertEqual(task["execution_backend"], "systemd-user")
        self.assertEqual(len(list((self.root / "pending").glob("*.json"))), 1)

    def test_unknown_recorded_backend_is_not_silently_run_as_systemd_or_screen(self) -> None:
        path = self.submit()
        task = managed_tasks.load_managed_task(path)
        task["execution_backend"] = "future-unavailable-backend"
        path.write_text(json.dumps(task), encoding="utf-8")
        with mock.patch.object(SystemdUserBackend, "launch") as native, mock.patch.object(
            managed_tasks, "launch_managed_screen"
        ) as screen:
            managed_tasks.recover_managed_tasks(self.root)
        native.assert_not_called()
        screen.assert_not_called()
        self.assertEqual(json.loads(path.read_text())["state"], "submitted")

    def test_unsupported_stored_agent_does_not_abort_recovery_of_valid_tasks(self) -> None:
        path = self.submit()
        valid = managed_tasks.load_managed_task(path)
        for ident, changed in (
            ("unsupported-callback", {"agent": "future-unavailable-agent"}),
            ("unsupported-worker", {"task_kind": "agent", "agent_worker": "future-unavailable-agent"}),
        ):
            record = dict(valid, id=ident, **changed)
            managed_tasks.write_managed_task(self.root, record)
        with mock.patch.object(SystemdUserBackend, "launch") as launch:
            managed_tasks.recover_managed_tasks(self.root)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(managed_tasks.load_managed_task(path)["state"], "launching")
        for ident in ("unsupported-callback", "unsupported-worker"):
            recorded = json.loads(layout.managed_task_path(self.root, ident).read_text())
            self.assertEqual(recorded["state"], "submitted")

    def test_recovery_reloads_state_after_lock_before_launching_stale_snapshot(self) -> None:
        path = self.submit()
        task = managed_tasks.load_managed_task(path)
        real_acquire = queue_store.acquire_owner_lock
        changed = False

        def finish_before_lock(root, request_id, *, blocking):
            nonlocal changed
            if request_id == f"managed-task-{task['id']}" and not changed:
                changed = True
                completed = dict(task, state="completed", outcome="completed", exit_code=7,
                                 completed_at=time.time())
                managed_tasks.write_managed_task(root, completed)
            return real_acquire(root, request_id, blocking=blocking)

        with mock.patch.object(queue_store, "acquire_owner_lock", side_effect=finish_before_lock), mock.patch.object(
            SystemdUserBackend, "launch"
        ) as launch:
            managed_tasks.recover_managed_tasks(self.root)
        self.assertTrue(changed)
        launch.assert_not_called()
        latest = managed_tasks.load_managed_task(path)
        self.assertEqual(latest["state"], "completed")
        self.assertEqual(latest["exit_code"], 7)
        self.assertEqual(len(list((self.root / "pending").glob("*.json"))), 1)

    def test_callback_details_write_failure_cannot_reclassify_completed_workload(self) -> None:
        path = self.submit()
        with mock.patch.object(SystemdUserBackend, "launch"):
            managed_tasks.recover_managed_tasks(self.root)
        worker = argparse.Namespace(task_file=str(path), attempt=1)
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "fixture-invocation"}, clear=True), mock.patch.object(
            cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)
        ), mock.patch.object(queue_store, "write_private_text", side_effect=OSError("details directory unavailable")):
            self.assertEqual(task_worker.run_task_worker(worker), 7)
        task = managed_tasks.load_managed_task(path)
        self.assertEqual(task["state"], "completed")
        self.assertEqual(task["outcome"], "completed")
        self.assertEqual(task["exit_code"], 7)
        self.assertFalse(task.get("callback_queued", False))
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])
        result = json.loads(layout.managed_result_path(self.root, str(task["id"])).read_text())
        self.assertEqual(result["observed"]["exit_code"], 7)
        with mock.patch.object(SystemdUserBackend, "launch") as relaunch:
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
        relaunch.assert_not_called()
        self.assertEqual(managed_tasks.load_managed_task(path)["state"], "completed")
        self.assertEqual(len(list((self.root / "pending").glob("*.json"))), 1)

    def test_metadata_failure_after_result_commit_preserves_known_exit_for_recovery(self) -> None:
        path = self.submit()
        with mock.patch.object(SystemdUserBackend, "launch"):
            managed_tasks.recover_managed_tasks(self.root)
        real_write = managed_tasks.write_managed_task

        def fail_completed_metadata(root, task):
            if task.get("state") == "completed":
                raise OSError("task metadata filesystem temporarily unavailable")
            return real_write(root, task)

        worker = argparse.Namespace(task_file=str(path), attempt=1)
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "fixture-invocation"}, clear=True), mock.patch.object(
            cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)
        ) as command, mock.patch.object(managed_tasks, "write_managed_task", side_effect=fail_completed_metadata):
            self.assertEqual(task_worker.run_task_worker(worker), 7)
            self.assertEqual(command.call_count, 1)
        task = managed_tasks.load_managed_task(path)
        self.assertEqual(task["state"], "running")
        self.assertNotIn("recovery_reason", task)
        result = json.loads(layout.managed_result_path(self.root, str(task["id"])).read_text())
        self.assertEqual(result["observed"]["state"], "exited")
        self.assertEqual(result["observed"]["exit_code"], 7)
        with mock.patch.object(SystemdUserBackend, "launch") as relaunch, mock.patch.object(
            SystemdUserBackend, "probe", return_value=OwnerState.UNKNOWN
        ):
            managed_tasks.recover_managed_tasks(self.root)
            managed_tasks.recover_managed_tasks(self.root)
        relaunch.assert_not_called()
        completed = managed_tasks.load_managed_task(path)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["outcome"], "completed")
        self.assertEqual(completed["exit_code"], 7)
        self.assertEqual(completed["schema"], "atc.task/1")
        self.assertEqual(len(list((self.root / "pending").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
