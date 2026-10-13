"""Container-safe setup selection: no system service is installed by tests."""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_atc import cli
from agent_atc.install import services
from agent_atc.install import skills as skill_install
from agent_atc.tower import environment as tower_environment
from agent_atc.engine import tasks as managed_tasks
from agent_atc.engine import reminders as reminder_policy
from agent_atc.store import layout
from agent_atc.owners import SystemdUserBackend


class DaemonServiceSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        platform = mock.patch.object(sys, "platform", "linux")
        platform.start()
        self.addCleanup(platform.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def arguments(self, service="auto", *, now=True):
        return argparse.Namespace(
            backend="screen", service=service, skill_path=None, skill_target="both",
            force=False, name="atc-container-test", queue_dir=str(self.directory / "queue"),
            interval=2.0, retries=3, retry_delay=30.0, retry_backoff=2.0,
            resume_timeout=60.0, restart_sec=2.0, exec_start=None, codex_bin=None,
            claude_bin=None, path=None, proxy_env_file=None, inherit_proxy=False,
            clear_proxy=False, enable=False, now=now,
        )

    def setup_with_spies(self, args, *, manager_available, backend_error=None):
        # No installer, credential probe, startup process, or user service call
        # escapes this fixture. It exercises only setup's routing decisions.
        spies = {}
        with contextlib.ExitStack() as stack:
            spies["select_execution_backend"] = stack.enter_context(mock.patch.object(
                managed_tasks, "select_execution_backend", return_value="screen", side_effect=backend_error
            ))
            stack.enter_context(mock.patch.object(SystemdUserBackend, "available", return_value=manager_available))
            stack.enter_context(mock.patch.object(services, "running_in_container", return_value=True))
            stack.enter_context(mock.patch.object(cli.shutil, "which", side_effect=lambda name: "/usr/bin/" + name))
            stack.enter_context(mock.patch.object(services, "systemd_user_dir", return_value=self.directory / "systemd"))
            for owner, name in ((skill_install, "install_skill"), (services, "install_systemd"), (services, "install_supervisor"),
                                (services, "start_standalone_daemon"), (tower_environment, "configure_proxy_environment"),
                                (reminder_policy, "ensure_callback_hook_file"), (services, "report_claude_agent_readiness")):
                spies[name] = stack.enter_context(mock.patch.object(owner, name, return_value=0))
            spies["run_systemctl"] = stack.enter_context(mock.patch.object(services, "run_systemctl"))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = services.setup(args)
        self.assertEqual(status, 0)
        self.assertFalse((self.directory / "systemd").exists())
        return spies

    def test_auto_uses_standalone_without_manager_even_when_supervisor_is_present(self) -> None:
        spies = self.setup_with_spies(self.arguments(), manager_available=False)
        spies["start_standalone_daemon"].assert_called_once()
        spies["install_systemd"].assert_not_called()
        spies["run_systemctl"].assert_not_called()
        spies["install_supervisor"].assert_not_called()
        spies["configure_proxy_environment"].assert_called_once()

    def test_auto_prefers_reachable_user_manager(self) -> None:
        spies = self.setup_with_spies(self.arguments(), manager_available=True)
        spies["install_systemd"].assert_called_once()
        spies["start_standalone_daemon"].assert_not_called()
        spies["install_supervisor"].assert_not_called()

    def test_explicit_standalone_does_not_install_systemd_even_when_available(self) -> None:
        spies = self.setup_with_spies(self.arguments("standalone"), manager_available=True)
        spies["start_standalone_daemon"].assert_called_once()
        spies["install_systemd"].assert_not_called()
        spies["install_supervisor"].assert_not_called()

    def test_standalone_setup_without_now_does_not_start_background_process(self) -> None:
        spies = self.setup_with_spies(self.arguments("standalone", now=False), manager_available=False)
        spies["install_skill"].assert_called_once()
        spies["start_standalone_daemon"].assert_not_called()
        spies["install_systemd"].assert_not_called()
        spies["install_supervisor"].assert_not_called()

    def test_explicit_supervisor_is_routed_only_to_requested_installer(self) -> None:
        spies = self.setup_with_spies(self.arguments("supervisor"), manager_available=False)
        spies["install_supervisor"].assert_called_once()
        spies["start_standalone_daemon"].assert_not_called()
        spies["install_systemd"].assert_not_called()

    def test_explicit_systemd_is_routed_to_requested_installer(self) -> None:
        spies = self.setup_with_spies(self.arguments("systemd"), manager_available=True)
        spies["install_systemd"].assert_called_once()
        spies["start_standalone_daemon"].assert_not_called()
        spies["install_supervisor"].assert_not_called()

    def test_parser_exposes_all_service_choices_without_starting_anything(self) -> None:
        for service in ("auto", "systemd", "supervisor", "standalone"):
            with self.subTest(service=service), mock.patch.object(
                sys, "argv", ["atc", "setup", "--service", service]
            ), mock.patch.object(services, "setup", return_value=0) as setup:
                self.assertEqual(cli.main(), 0)
                self.assertEqual(setup.call_args.args[0].service, service)

    def test_selector_rejects_unknown_service_instead_of_installing_default(self) -> None:
        with self.assertRaises(ValueError):
            services.select_daemon_service(self.arguments("unknown-future-service"))

    def test_keep_skill_cli_option_is_forwarded_to_skill_installer(self) -> None:
        with mock.patch.object(sys, "argv", ["atc", "setup", "--keep-skill", "--force"]), mock.patch.object(
            services, "setup", return_value=0
        ) as setup:
            self.assertEqual(cli.main(), 0)
        self.assertTrue(setup.call_args.args[0].keep_skill)
        args = self.arguments("standalone", now=False)
        args.keep_skill = True
        args.force = True
        spies = self.setup_with_spies(args, manager_available=False)
        forwarded = spies["install_skill"].call_args.args[0]
        self.assertTrue(forwarded.keep_existing)
        self.assertTrue(forwarded.force)

    def test_callback_only_setup_can_start_standalone_without_any_task_backend(self) -> None:
        with mock.patch.object(sys, "argv", ["atc", "setup", "--callback-only"]), mock.patch.object(
            services, "setup", return_value=0
        ) as setup:
            self.assertEqual(cli.main(), 0)
        self.assertTrue(setup.call_args.args[0].callback_only)
        args = self.arguments("standalone")
        args.callback_only = True
        spies = self.setup_with_spies(args, manager_available=False, backend_error=ValueError("no task backend"))
        spies["select_execution_backend"].assert_not_called()
        spies["install_skill"].assert_called_once()
        spies["start_standalone_daemon"].assert_called_once()

    def test_ordinary_setup_still_refuses_missing_task_backend_before_installation(self) -> None:
        args = self.arguments("standalone")
        with mock.patch.object(managed_tasks, "select_execution_backend", side_effect=ValueError("no task backend")), mock.patch.object(
            skill_install, "install_skill"
        ) as install, mock.patch.object(services, "start_standalone_daemon") as start, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(services.setup(args), 2)
        install.assert_not_called()
        start.assert_not_called()

    def test_auto_manager_failure_falls_back_without_taking_over_container_supervisor(self) -> None:
        args = self.arguments("auto")
        with mock.patch.object(managed_tasks, "select_execution_backend", return_value="screen"), mock.patch.object(
            SystemdUserBackend, "available", return_value=True
        ), mock.patch.object(services, "running_in_container", return_value=True), mock.patch.object(
            cli.shutil, "which", side_effect=lambda name: "/usr/bin/" + name
        ), mock.patch.object(services, "systemd_user_dir", return_value=self.directory / "systemd"), mock.patch.object(
            layout, "daemon_state_dir", return_value=self.directory / "state"
        ), mock.patch.object(skill_install, "install_skill", return_value=0), mock.patch.object(
            reminder_policy, "ensure_callback_hook_file"
        ), mock.patch.object(services, "report_claude_agent_readiness"), mock.patch.object(
            tower_environment, "configure_proxy_environment"
        ), mock.patch.object(services, "systemd_service_text", return_value="[Service]\nExecStart=/fixture/atc daemon\n"), mock.patch.object(
            services, "run_systemctl", return_value=1
        ) as systemctl, mock.patch.object(services, "start_standalone_daemon", return_value=0) as standalone, mock.patch.object(
            services, "install_supervisor", return_value=0
        ) as supervisor, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(services.setup(args), 0)
        systemctl.assert_called_once_with(["daemon-reload"])
        standalone.assert_called_once()
        supervisor.assert_not_called()
        self.assertEqual(len(list((self.directory / "systemd").glob("*.service"))), 1)


if __name__ == "__main__":
    unittest.main()
