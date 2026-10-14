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
        return atc_records.callback_record(ident, str(self.root), target={"kind": "session", "value": "s"},
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
