from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import atc_records
from agent_atc.core import capabilities, records
from agent_atc.engine import delivery as delivery_engine
from agent_atc.engine import dispatch
from agent_atc.store import layout
from agent_atc.store import records as queue_store


class CallbackRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "queue"
        queue_store.ensure_daemon_dirs(self.root)

    def write(self, record: dict[str, object], state: str = "pending") -> Path:
        path = layout.request_path(self.root, state, str(record["id"]))
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        return path

    def record(self, ident: str = "1a2b3c4d-c1", **fields: object) -> dict[str, object]:
        return atc_records.callback_record(ident, str(self.root), target={"kind": "session", "session": "s"},
                                           prompt="wake up", **fields)

    def test_routes_declare_what_delivery_depends_on(self):
        self.assertEqual(capabilities.required_for({"callback_mode": "cli"}), [capabilities.CLI_RESUME])
        self.assertEqual(capabilities.required_for({"callback_mode": "desktop"}), [capabilities.CODEX_DESKTOP])
        self.assertEqual(capabilities.required_for({"callback_mode": "manual"}), [])
        self.assertEqual(capabilities.required_for({"callback_mode": "cli", "pi_callback_protocol": "live-v1",
                                                    "pi_delivery": "steer"}),
                         sorted([capabilities.CLI_RESUME, capabilities.PI_STEER]))
        self.assertEqual(capabilities.required_for({"callback_mode": "cli", "pi_callback_protocol": "live-v1"}),
                         sorted([capabilities.CLI_RESUME, capabilities.PI_FOLLOW_UP]))

    def test_records_without_the_schema_are_refused_with_a_migration_hint(self):
        legacy = self.record()
        legacy.pop("schema")
        legacy["version"] = 2
        with self.assertRaisesRegex(ValueError, "atc migrate"):
            dispatch.load_request(self.write(legacy))
        self.assertTrue(records.is_callback(self.record()))

    def test_unknown_capabilities_leave_the_callback_untouched(self):
        future = self.record(requires=[capabilities.CLI_RESUME, "deliver.future-transport"])
        path = self.write(future)
        before = path.read_bytes()
        self.assertIsNone(delivery_engine.select_pending(self.root, 1e12))
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "deliver.future-transport"):
            dispatch.load_request(path)
        self.assertEqual(capabilities.unsupported("not-a-list"), ["<malformed requires>"])

    def test_known_capabilities_are_selected(self):
        path = self.write(self.record())
        self.assertEqual(delivery_engine.select_pending(self.root, 1e12), path)

    def test_only_pi_callbacks_may_require_pi_delivery(self):
        codex = self.record(agent="codex", requires=[capabilities.CLI_RESUME, capabilities.PI_STEER])
        with self.assertRaisesRegex(ValueError, "only Pi callbacks"):
            dispatch.load_request(self.write(codex))


if __name__ == "__main__":
    unittest.main()


class AddressTests(unittest.TestCase):
    def test_a_session_target_names_only_the_bound_session(self):
        from agent_atc.core import targets

        self.assertEqual(targets.session("019a"), {"kind": "session", "session": "019a"})
        self.assertEqual(targets.session_of({"kind": "session", "session": "019a"}), "019a")
        for target in (None, {}, {"kind": "session", "session": ""}, {"kind": "profile", "session": "x"}):
            self.assertIsNone(targets.session_of(target))
        with self.assertRaises(ValueError):
            targets.session("")

    def test_callbacks_record_who_submitted_them(self):
        import argparse
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(queue_dir=tmp, cwd=tmp, agent="claude", session="claude-1", task="t",
                                      exit_code=0, message=None, callback_mode="manual")
            with mock.patch("sys.stderr"):
                request = dispatch.make_request(args, "prompt")
        self.assertEqual(request["target"], {"kind": "session", "session": "claude-1"})
        self.assertEqual(request["origin"], {"harness": "claude", "session": "claude-1", "cwd": tmp, "binding": "--session"})
        self.assertNotIn("target_source", request)
