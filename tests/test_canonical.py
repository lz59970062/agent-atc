"""One canonical Reins per user: the launcher setup installs, and forwarding to it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_reins import __version__
from harness_reins import runtime as reins_runtime
from harness_reins.engine import dispatch
from harness_reins.install import canonical
from harness_reins.store import install_record

SRC = str(Path(__file__).resolve().parents[1] / "src")


class CanonicalInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "reins-home"
        patcher = mock.patch.dict(os.environ, {"REINS_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_build_ids_name_the_code_not_just_the_version(self):
        self.assertRegex(reins_runtime.build_id(), r"^[0-9a-f]{12}$")
        self.assertEqual(reins_runtime.build_id(), reins_runtime.build_id())

    def test_ensure_installs_a_working_launcher_and_record(self):
        record = canonical.ensure()
        self.assertEqual(record["version"], __version__)
        self.assertEqual(record["build"], reins_runtime.build_id())
        self.assertEqual(install_record.read(), json.loads(install_record.record_path().read_text(encoding="utf-8")))
        launcher = install_record.launcher_path()
        self.assertEqual(install_record.launcher(), str(launcher))
        if os.name != "nt":
            self.assertTrue(os.access(launcher, os.X_OK))
            completed = subprocess.run([str(launcher), "--version"], capture_output=True, text=True, timeout=60,
                                       env=dict(os.environ, PYTHONPATH=SRC))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(f"reins {__version__} (build {reins_runtime.build_id()})", completed.stdout)

    def test_the_same_build_runs_itself_and_a_different_build_forwards(self):
        self.assertTrue(canonical.is_current())  # nothing installed yet
        canonical.ensure()
        self.assertTrue(canonical.is_current())
        self.assertIsNone(canonical.forward(["status"]))
        record = install_record.read()
        record["build"] = "000000000000"
        install_record.record_path().write_text(json.dumps(record), encoding="utf-8")
        self.assertFalse(canonical.is_current())
        with mock.patch.object(canonical.subprocess, "run", return_value=mock.Mock(returncode=7)) as run, \
                mock.patch("sys.stderr"):
            self.assertEqual(canonical.forward(["status", "--json"]), 7)
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["status", "--json"])
        self.assertEqual(run.call_args.kwargs["env"][canonical.FORWARDED_ENV], "1")
        with mock.patch.dict(os.environ, {canonical.FORWARDED_ENV: "1"}):
            self.assertIsNone(canonical.forward(["status"]), "a forwarded command never forwards again")

    def test_ack_commands_use_the_installed_launcher(self):
        canonical.ensure()
        queue = self.home / "queue"
        with mock.patch.object(dispatch.layout, "queue_dir", return_value=queue):
            command = dispatch.ack_command(queue, "1a2b3c4d-c1")
        self.assertTrue(command.startswith(str(install_record.launcher_path()) if os.name != "nt" else "&"))
        self.assertTrue(command.endswith("ack 1a2b3c4d-c1"))


if __name__ == "__main__":
    unittest.main()
