"""Persist Desktop submission intent before a parent deadline can kill its worker.

The real subprocess/lease handoff runs on Windows and POSIX; the RPC endpoint is
a local fake, so these tests never connect to a model or a user's Desktop task.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import time
import types
import unittest
from unittest import mock

from harness_reins import cli
from harness_reins.commands import callbacks as callback_commands
from harness_reins.engine import dispatch
from harness_reins.engine import delivery as delivery_engine
from harness_reins.harness.codex import app_server
from harness_reins.store import records as queue_store
from harness_reins.store import layout
import reins_records


# Real-time budget for a worker to reach turn/start and finish on its own. It
# is deliberately far above any plausible startup delay on a loaded host, so it
# never decides an outcome; tests that need the parent deadline to expire
# control the parent's clock instead (see parent_deadline_after_submission).
WORKER_TIMEOUT = 120.0


class DesktopDeliveryTimeoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="reins desktop intent ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "queue"
        self.events = self.directory / "rpc-events.jsonl"
        self.cli_calls = self.directory / "cli-calls"
        self.request = reins_records.callback_record("desktop-fixture", str(self.directory), target={"kind": "session", "session": "fixture-original-session"}, prompt="Model-free Desktop delivery fixture", created_at=time.time(), agent="codex", queue_dir=str(self.root), target_source="explicit", sandbox_mode="workspace-write", attempts=0)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.directory / "codex-profile"), "REINS_HOME": str(self.directory / "codex-profile" / "reins-home"),
            layout.TARGET_LOCK_DIR_ENV: str(self.directory / "target-locks"),
            "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1]),
            "PYTHONIOENCODING": "utf-8",
        }))
        self.errors = self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        queue_store.ensure_daemon_dirs(self.root)

    def worker_command(self, mode):
        program = textwrap.dedent("""
            import argparse,json,os,sys,time
            from pathlib import Path
            from harness_reins import cli
            from harness_reins.commands import callbacks as callback_commands
            from harness_reins.engine import dispatch
            from harness_reins.engine import delivery as delivery_engine
            from harness_reins.harness.codex import app_server
            from harness_reins.store import records as queue_store
            from harness_reins.store import layout
            root=Path(ROOT)
            request=json.loads(REQUEST)
            events=Path(EVENTS)
            mode=MODE
            class FakeConnection:
                def connect(self): pass
                def close(self): pass
                def notify(self, method, params): pass
                def request(self, method, params):
                    if method == 'thread/resume' and mode == 'before-submit-failure':
                        raise app_server.AppServerProtocolError('connection lost before turn submission')
                    if method != 'turn/start': return {}
                    with events.open('a',encoding='utf-8') as log:
                        log.write(json.dumps({'method':method,'intent_durable':delivery_engine.desktop_submission_pending(root,request)})+'\\n')
                    if mode == 'worker-crash': os._exit(23)
                    if mode == 'explicit-rejection':
                        raise app_server.AppServerRpcError('explicit rejection')
                    if mode in ('acknowledged','unknown-after-ack'):
                        callback_commands.ack(argparse.Namespace(queue_dir=str(root),id=request['id'],message='fixture ACK'))
                    if mode in ('unknown','unknown-after-ack'):
                        time.sleep(0.35)
                        raise app_server.AppServerProtocolError('turn/start response lost')
                    return {'turn':{'id':'fixture-turn'}}
                def wait_for_turn_completion(self, thread_id, turn_id, timeout): return True
            app_server.desktop_app_server_socket=lambda request:Path('unused-fixture.sock')
            app_server.AppServerConnection=lambda path,timeout:FakeConnection()
            raise SystemExit(delivery_engine.delivery_worker_main())
        """)
        program = program.replace("ROOT", repr(str(self.root))).replace("REQUEST", repr(json.dumps(self.request)))
        program = program.replace("EVENTS", repr(str(self.events))).replace("MODE", repr(mode))
        return [sys.executable, "-c", program]

    def fallback_command(self):
        code = (
            "from pathlib import Path; import sys; sys.stdin.read(); "
            f"Path({str(self.cli_calls)!r}).write_text('called',encoding='utf-8')"
        )
        return [sys.executable, "-c", code]

    def submission_recorded(self):
        try:
            return self.events.read_text(encoding="utf-8").endswith("\n")
        except FileNotFoundError:
            return False

    @contextlib.contextmanager
    def parent_deadline_after_submission(self):
        """Hold the parent's clock until the worker has sent turn/start, then pass its deadline.

        The worker's own timeout keeps running on real time and is far longer,
        so the parent deadline is the one that fires, whatever the host load.
        """
        start = time.monotonic()

        def monotonic():
            if self.submission_recorded() or time.monotonic() - start > WORKER_TIMEOUT:
                return start + 10 * WORKER_TIMEOUT
            return start

        clock = types.SimpleNamespace(monotonic=monotonic, time=time.time, sleep=time.sleep)
        stop = mock.patch.object(delivery_engine, "stop_resume_process", wraps=delivery_engine.stop_resume_process)
        with mock.patch.object(delivery_engine, "time", clock), stop as stopped:
            yield stopped

    def run_worker(self, mode, timeout=WORKER_TIMEOUT):
        with mock.patch.object(delivery_engine, "delivery_worker_command", return_value=self.worker_command(mode)), mock.patch.object(
            delivery_engine, "resume_command", return_value=self.fallback_command()
        ):
            return delivery_engine.run_resume_until_exit_or_ack(self.root, self.request["id"], self.request,
                                                    argparse.Namespace(resume_timeout=timeout))

    def assert_submission_recorded_once(self):
        events = [json.loads(line) for line in self.events.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events, [{"method": "turn/start", "intent_durable": True}])

    def tearDown(self):
        for delivery in list(delivery_engine._BACKGROUND_RESUMES.values()):
            delivery_engine.stop_resume_process(delivery.process)
        delivery_engine._BACKGROUND_RESUMES.clear()

    def test_parent_deadline_before_worker_timeout_preserves_unknown_outcome(self):
        with self.parent_deadline_after_submission() as stopped:
            result, acked, running = self.run_worker("unknown")
        stopped.assert_called_once()  # The parent deadline, not the worker, ended delivery.
        self.assertEqual(result.returncode, 125, self.errors.getvalue())
        self.assertFalse(acked)
        self.assertFalse(running)
        self.assert_submission_recorded_once()
        self.assertTrue(delivery_engine.desktop_submission_pending(self.root, self.request))
        self.assertTrue(delivery_engine.retained_target_lease_is_held(self.request))
        self.assertFalse(self.cli_calls.exists())

    def test_worker_crash_after_submission_preserves_unknown_outcome(self):
        result, acked, running = self.run_worker("worker-crash")
        self.assertEqual(result.returncode, 125, self.errors.getvalue())
        self.assertFalse(acked)
        self.assertFalse(running)
        self.assert_submission_recorded_once()
        self.assertTrue(delivery_engine.retained_target_lease_is_held(self.request))
        self.assertFalse(self.cli_calls.exists())

    def test_unknown_submission_enters_failed_without_retry_and_blocks_other_queue(self):
        queue_store.write_request(layout.request_path(self.root, "pending", self.request["id"]), self.request)
        args = argparse.Namespace(resume_timeout=WORKER_TIMEOUT, retries=3, retry_delay=0, retry_backoff=1)
        with mock.patch.object(delivery_engine, "delivery_worker_command", return_value=self.worker_command("unknown")), mock.patch.object(
            delivery_engine, "resume_command", return_value=self.fallback_command()
        ):
            with self.parent_deadline_after_submission() as stopped:
                self.assertTrue(delivery_engine.process_one(self.root, args))
            stopped.assert_called_once()
            self.assertFalse(delivery_engine.process_one(self.root, args))
        failed = dispatch.load_request(layout.request_path(self.root, "failed", self.request["id"]))
        self.assertTrue(failed["retain_target_lease"])
        self.assertEqual(failed["attempts"], 1)
        self.assertFalse(layout.request_path(self.root, "pending", self.request["id"]).exists())
        other = self.directory / "other-queue"
        queue_store.ensure_daemon_dirs(other)
        queue_store.write_request(layout.request_path(other, "pending", "contender"), dict(self.request, id="contender", queue_dir=str(other)))
        self.assertIsNone(delivery_engine.select_pending(other, time.time()))
        self.assert_submission_recorded_once()

    def test_explicit_rejection_clears_intent_and_allows_cli(self):
        result, acked, running = self.run_worker("explicit-rejection")
        self.assertEqual(result.returncode, 0, self.errors.getvalue())
        self.assertFalse(acked)
        self.assertFalse(running)
        self.assert_submission_recorded_once()
        self.assertFalse(delivery_engine.desktop_submission_pending(self.root, self.request))
        self.assertTrue(self.cli_calls.exists())

    def test_matching_completion_clears_intent_and_keeps_normal_ack_policy(self):
        result, acked, running = self.run_worker("completed")
        self.assertEqual(result.returncode, 1, self.errors.getvalue())  # Known completion without ACK.
        self.assertFalse(acked)
        self.assertFalse(running)
        self.assert_submission_recorded_once()
        self.assertFalse(delivery_engine.desktop_submission_pending(self.root, self.request))
        self.assertFalse(self.cli_calls.exists())

    def test_acknowledged_submission_releases_intent_even_if_rpc_reply_is_lost(self):
        for mode in ("acknowledged", "unknown-after-ack"):
            with self.subTest(mode=mode):
                if layout.ack_path(self.root, self.request["id"]).exists():
                    layout.ack_path(self.root, self.request["id"]).unlink()
                if self.events.exists():
                    self.events.unlink()
                result, acked, running = self.run_worker(mode)
                self.assertEqual(result.returncode, 0, self.errors.getvalue())
                self.assertTrue(acked)
                if running:
                    # The parent returns as soon as it sees the ACK; the worker
                    # releases the intent afterwards. Wait for it to finish.
                    (delivery,) = delivery_engine._BACKGROUND_RESUMES.values()
                    delivery.process.wait(timeout=WORKER_TIMEOUT)
                    self.assertEqual(delivery_engine.finish_delivery_worker(delivery.process), 0)
                    delivery_engine._BACKGROUND_RESUMES.clear()
                self.assertFalse(delivery_engine.retained_target_lease_path(self.request).exists())
                self.assertFalse(delivery_engine.retained_target_lease_is_held(self.request))
                self.assertFalse(self.cli_calls.exists())
                self.assert_submission_recorded_once()
                self.tearDown()  # Finish the acknowledged worker before the next fixture.

    def test_failure_before_turn_submission_falls_back_without_intent(self):
        result, acked, running = self.run_worker("before-submit-failure")
        self.assertEqual(result.returncode, 0, self.errors.getvalue())
        self.assertFalse(acked)
        self.assertFalse(running)
        self.assertFalse(self.events.exists())
        self.assertFalse(delivery_engine.desktop_submission_pending(self.root, self.request))
        self.assertTrue(self.cli_calls.exists())

    def test_foreign_callback_lease_does_not_classify_this_request_as_unknown(self):
        other = dict(self.request, id="another-callback")
        delivery_engine.retain_target_lease(self.root, other)
        self.assertFalse(delivery_engine.desktop_submission_pending(self.root, self.request))


if __name__ == "__main__":
    unittest.main()
