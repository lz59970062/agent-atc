"""Install and start the tower as a user service: systemd, launchd, Task Scheduler, supervisor or standalone."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from ..harness.claude import session as claude_code
from ..harness.pi import channel as pi_callback
from ..harness.claude.adapter import CLAUDE_BIN_ENV
from ..harness.pi.adapter import PI_BIN_ENV
from ..owners import SystemdUserBackend, LaunchdBackend
from ..owners import linux as linux_platform
from ..owners import windows as windows_platform
from ..system import windows_process
from ..store import layout
from ..store import records as queue_store
from ..system import commands
from ..engine import delivery as delivery_engine
from ..engine import reminders as reminder_policy
from ..harness.codex import app_server
from ..engine import tasks as managed_tasks
from . import skills as skill_install
from ..tower import environment as tower_environment
from ..tower import main as tower


DEFAULT_SUPERVISOR_CONF_DIR = "/etc/supervisor/conf.d"


def systemd_user_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / "systemd" / "user"
    return Path("~/.config/systemd/user").expanduser()


def systemd_quote(value: str) -> str:
    if any(char in value for char in "\x00\n\r"):
        raise ValueError("systemd values must not contain NUL, carriage return, or newline characters")
    if value and all(char not in value for char in " \t\n\"'\\"):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def supervisor_quote(value: str) -> str:
    if any(char in value for char in "\x00\n\r"):
        raise ValueError("supervisor values must not contain NUL, carriage return, or newline characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_name(name: str) -> str:
    return name if name.endswith(".service") else f"{name}.service"


def program_name(name: str) -> str:
    return name.removesuffix(".service")


def report_claude_agent_readiness(args: argparse.Namespace) -> None:
    configured = tower_environment.claude_bin_path(args)
    command = shutil.which(configured)
    if command is None:
        print(
            "atc: warning: Claude Code was not found; setup will continue, but `atc agent claude` "
            "requires `claude` on PATH or an explicit --claude-bin path",
            file=sys.stderr,
        )
        return
    try:
        environment = managed_tasks.child_agent_environment(dict(os.environ))
        result = subprocess.run(
            commands.process_command([command, "auth", "status", "--json"], environment),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
            env=environment,
            **(windows_process.background_popen_kwargs() if os.name == "nt" else {}),
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        print(
            "Claude Code agent mode: CLI and local authentication detected; configuration values were not printed."
        )
        return
    if claude_code.is_desktop_bundle(command):
        print(
            "atc: warning: only the Claude desktop app's bundled CLI was found, and it is not signed in "
            "outside the app. Live Claude Code sessions still receive callbacks through `atc wait`; "
            "headless resume fallback and `atc agent claude` need a standalone CLI signed in with "
            "`claude auth login`. Setup will continue.",
            file=sys.stderr,
        )
        return
    print(
        "atc: warning: Claude Code was found, but `claude auth status` could not confirm authentication. "
        "Configure Claude in the shell that will launch `atc agent claude`; setup will continue.",
        file=sys.stderr,
    )


def systemd_service_text(args: argparse.Namespace) -> str:
    command = tower.daemon_command(args)
    exec_start = " ".join(systemd_quote(part) for part in command)
    codex_bin = tower_environment.codex_bin_path(args)
    claude_bin = tower_environment.claude_bin_path(args)
    pi_bin = tower_environment.pi_bin_path(args)
    screen_bin = managed_tasks.screen_binary() or "screen"
    path = args.path or os.environ.get("PATH", "")
    return "\n".join(
        [
            "[Unit]",
            "Description=Agent Traffic Control daemon (Codex and Claude Code)",
            "Documentation=https://github.com/lz59970062/agent-atc",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={exec_start}",
            "ExecReload=/bin/kill -HUP $MAINPID",
            "Restart=always",
            f"RestartSec={args.restart_sec}",
            "UMask=0077",
            "NoNewPrivileges=yes",
            "Environment=PYTHONUNBUFFERED=1",
            f"Environment={systemd_quote(f'PATH={path}')}",
            f"Environment={systemd_quote(f'ATC_CODEX_BIN={codex_bin}')}",
            f"Environment={systemd_quote(f'{CLAUDE_BIN_ENV}={claude_bin}')}",
            f"Environment={systemd_quote(f'{PI_BIN_ENV}={pi_bin}')}",
            f"Environment={systemd_quote(f'{managed_tasks.SCREEN_BIN_ENV}={screen_bin}')}",
            f"Environment={systemd_quote(f'{app_server.DESKTOP_APP_SERVER_ENV}=1')}",
            f"Environment={systemd_quote(f'{tower_environment.PROXY_ENV_FILE_ENV}={layout.service_proxy_env_path()}')}",
            f"Environment={systemd_quote(f'{layout.ATC_HOME_ENV}={layout.daemon_state_dir().resolve()}')}",
            f"EnvironmentFile=-{layout.service_proxy_env_path()}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def running_in_container() -> bool:
    if Path("/.dockerenv").exists():
        return True
    container = os.environ.get("container", "").strip().lower()
    return container in {"docker", "podman", "oci", "containerd"}


def supervisor_conf_dir() -> Path:
    path = os.environ.get("ATC_SUPERVISOR_CONF_DIR", DEFAULT_SUPERVISOR_CONF_DIR)
    return Path(path).expanduser()


def supervisor_config_path(args: argparse.Namespace) -> Path:
    return supervisor_conf_dir() / f"{program_name(args.name)}.conf"


def supervisor_config_text(args: argparse.Namespace) -> str:
    root = layout.daemon_state_dir()
    log_path = root / "supervisor.log"
    command = " ".join(shlex.quote(part) for part in tower.daemon_command(args))
    environment = ",".join(
        f"{name}={supervisor_quote(value)}" for name, value in tower_environment.daemon_environment(args, include_proxy_values=False).items()
    )
    return "\n".join(
        [
            f"[program:{program_name(args.name)}]",
            f"command={command}",
            "autostart=true",
            "autorestart=true",
            "startsecs=3",
            "startretries=10",
            "stopsignal=TERM",
            "stopasgroup=true",
            "killasgroup=true",
            "redirect_stderr=true",
            f"stdout_logfile={log_path}",
            "stdout_logfile_maxbytes=10MB",
            "stdout_logfile_backups=3",
            f"environment={environment}",
            "",
        ]
    )


def run_systemctl(args: list[str]) -> int:
    command = ["systemctl", "--user", *args]
    result = subprocess.run(command, check=False, env=linux_platform.user_manager_environment())
    if result.returncode != 0:
        print(f"atc: warning: {' '.join(shlex.quote(part) for part in command)} exited with {result.returncode}", file=sys.stderr)
    return result.returncode


def systemd_service_is_active(name: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", name],
        check=False,
        env=linux_platform.user_manager_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def run_supervisorctl(args: list[str]) -> int:
    command = ["supervisorctl", *args]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(f"atc: warning: {' '.join(shlex.quote(part) for part in command)} exited with {result.returncode}", file=sys.stderr)
    return result.returncode


def read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        return windows_platform.pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start_standalone_daemon(args: argparse.Namespace) -> int:
    if os.name == "nt":
        raise ValueError("Use --service windows-task on Windows for an independent coordinator")
    root = layout.daemon_state_dir()
    root.mkdir(parents=True, exist_ok=True)
    pid_path = root / "daemon.pid"
    log_path = root / "daemon.log"

    # PID files can outlive their container and refer to an unrelated process
    # after restart. The OS-held per-queue lock is the source of ownership;
    # the runtime fingerprint is only used to authorize a safe reload signal.
    queue_root = layout.queue_dir(args)
    admission = queue_store.acquire_owner_lock(queue_root, "daemon-singleton", blocking=False)
    if admission is None:
        existing_pid = read_pid(pid_path)
        if has_running_callbacks(args):
            print(
                "atc: deferred standalone daemon reload because callback delivery is running; "
                "the existing daemon was left untouched.",
                file=sys.stderr,
            )
            return 0
        if existing_pid is not None and tower.send_standalone_reload(existing_pid, expected_queue=queue_root):
            print(f"Reloading standalone wakeup daemon with pid {existing_pid}.")
            print(f"atc: log file: {log_path}")
            return 0
        print(
            "atc: an existing coordinator owns this queue; its identity or safe reload "
            "could not be verified, so it was left untouched.",
            file=sys.stderr,
        )
        return 0
    # Do not pass this setup process's lock into Popen. The daemon acquires its
    # own singleton at startup, which also arbitrates concurrent setup calls.
    queue_store.release_owner_lock(admission, remove=False)

    env = os.environ.copy()
    env.update(tower_environment.daemon_environment(args))

    log_file = log_path.open("ab")
    try:
        process = subprocess.Popen(
            tower.daemon_command(args),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
            env=env,
        )
    finally:
        log_file.close()

    queue_store.write_private_text(pid_path, f"{process.pid}\n")
    time.sleep(0.2)
    returncode = process.poll()
    if returncode is not None:
        print(
            f"atc: warning: standalone daemon exited immediately with {returncode}; see {log_path}",
            file=sys.stderr,
        )
        return returncode

    print(f"Started standalone wakeup daemon with pid {process.pid}")
    print(f"Standalone daemon pid file: {pid_path}")
    print(f"Standalone daemon log file: {log_path}")
    return 0


def start_supervisord_if_available() -> int:
    if shutil.which("supervisord") is None:
        return 1
    result = subprocess.run(["supervisord"], check=False)
    if result.returncode != 0:
        print("atc: warning: supervisord failed to start", file=sys.stderr)
    return result.returncode


def install_supervisor(args: argparse.Namespace) -> int:
    root = layout.daemon_state_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = supervisor_config_path(args)
    if target.exists() and not args.force:
        print(f"Supervisor program already exists at {target}. Re-run with --force to overwrite.", file=sys.stderr)
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(supervisor_config_text(args), encoding="utf-8")
    print(f"Installed supervisor program to {target}")

    if not args.now:
        return 0
    if has_running_callbacks(args):
        print(
            "atc: deferred supervisor update because callback delivery is running; "
            "the installed config will activate after those callbacks drain.",
            file=sys.stderr,
        )
        return 0
    if shutil.which("supervisorctl") is None:
        print("atc: warning: supervisorctl not found; supervisor config was written but not loaded", file=sys.stderr)
        return 1

    status = run_supervisorctl(["reread"])
    if status != 0 and start_supervisord_if_available() == 0:
        status = run_supervisorctl(["reread"])
    status = run_supervisorctl(["update"]) or status
    status = run_supervisorctl(["start", program_name(args.name)]) or status
    if status == 0:
        print(f"Started supervisor-managed wakeup daemon: {program_name(args.name)}")
    return status


def start_daemon_fallback(args: argparse.Namespace) -> int:
    # setup's service selection requires an explicit Supervisor opt-in even if
    # a previously reachable systemd manager fails during installation.
    allow_supervisor = getattr(args, "allow_supervisor_fallback", True)
    if allow_supervisor and running_in_container() and (shutil.which("supervisorctl") or shutil.which("supervisord")):
        print("atc: starting supervisor daemon fallback", file=sys.stderr)
        return install_supervisor(args)
    print("atc: starting standalone daemon fallback", file=sys.stderr)
    return start_standalone_daemon(args)


def has_running_callbacks(args: argparse.Namespace) -> bool:
    root = layout.queue_dir(args)
    return any((root / "running").glob("*.json"))


def install_systemd(args: argparse.Namespace) -> int:
    name = service_name(args.name)
    target = systemd_user_dir() / name
    if target.exists() and not args.force:
        print(f"Service already exists at {target}. Re-run with --force to overwrite.", file=sys.stderr)
        return 1
    try:
        if args.print:
            print(systemd_service_text(args), end="")
            return 0
        tower_environment.configure_proxy_environment(args)
        text = systemd_service_text(args)
    except ValueError as exc:
        print(f"atc: error: {exc}", file=sys.stderr)
        return 2

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"Installed systemd user service to {target}")

    status = 0
    if shutil.which("systemctl"):
        status = run_systemctl(["daemon-reload"])
        if status != 0 and args.now:
            print(
                "atc: warning: systemd user service could not be reloaded; using fallback daemon",
                file=sys.stderr,
            )
            return start_daemon_fallback(args)
        if args.enable:
            status = run_systemctl(["enable", name]) or status
        if args.now:
            if has_running_callbacks(args):
                if tower.daemon_supports_hot_reload():
                    print("Requesting safe wakeup daemon reload after live delivery workers drain.")
                    status = run_systemctl(["reload", name]) or status
                    if status != 0:
                        print(
                            "atc: reload failed; refusing to restart while callback delivery is running.",
                            file=sys.stderr,
                        )
                        return status
                else:
                    print(
                        "atc: deferred daemon activation because callback delivery is running; "
                        "the installed unit is updated, but the existing daemon was left untouched. "
                        "Drain running callbacks, then rerun setup --now to activate this version.",
                        file=sys.stderr,
                    )
                    return status
            elif tower.daemon_supports_hot_reload():
                print("Reloading the running wakeup daemon in place.")
                status = run_systemctl(["reload", name]) or status
                if status != 0:
                    print(
                        "atc: reload failed; refusing to fall back to restart automatically.",
                        file=sys.stderr,
                    )
                    return status
            elif systemd_service_is_active(name):
                print(
                    "atc: deferred activation because the running daemon does not advertise "
                    "safe hot reload; the unit is updated but the process was left untouched.",
                    file=sys.stderr,
                )
                return status
            else:
                action = "restart" if args.enable else "start"
                status = run_systemctl([action, name]) or status
            if status != 0:
                print(
                    "atc: warning: systemd user service could not be started; using fallback daemon",
                    file=sys.stderr,
                )
                fallback_status = start_daemon_fallback(args)
                if fallback_status == 0:
                    return 0
    else:
        print("atc: warning: systemctl not found; service file was written but not loaded", file=sys.stderr)
        if args.enable:
            print("atc: warning: --enable has no effect without systemd", file=sys.stderr)
        if args.now:
            status = start_daemon_fallback(args)

    return status


def install_launchd(args: argparse.Namespace) -> int:
    from . import launchd_service
    return launchd_service.install(args)


def install_windows_task(args: argparse.Namespace) -> int:
    from . import windows_service
    return windows_service.install(args)


def select_daemon_service(args: argparse.Namespace) -> str:
    """Choose coordinator hosting separately from task execution ownership.

    Containers commonly lack a user bus. Do not touch a provider-owned
    Supervisor configuration automatically; it remains an explicit option.
    """
    service = getattr(args, "service", "systemd")
    if service not in ("auto", "systemd", "launchd", "windows-task", "supervisor", "standalone"):
        raise ValueError(f"unsupported daemon service: {service!r}")
    if service == "auto":
        if sys.platform == "win32":
            return "windows-task"
        if sys.platform == "darwin":
            return "launchd" if LaunchdBackend().available() else "standalone"
        return "systemd" if SystemdUserBackend().available() else "standalone"
    if service == "launchd" and sys.platform != "darwin":
        raise ValueError("launchd coordinator requires macOS")
    if service == "windows-task" and sys.platform != "win32":
        raise ValueError("Windows Task Scheduler requires native Windows")
    if sys.platform == "win32" and service != "windows-task":
        raise ValueError("Use --service windows-task on native Windows")
    if service == "systemd" and sys.platform != "linux":
        raise ValueError("systemd coordinator requires Linux; use --service launchd on macOS")
    return service


def setup(args: argparse.Namespace) -> int:
    from ..harness.codex.desktop import launcher as desktop

    try:
        desktop.validate_setup(args)
        service = select_daemon_service(args)
        if not getattr(args, "callback_only", False):
            managed_tasks.select_execution_backend(args)
    except ValueError as exc:
        print(f"atc: setup cannot continue: {exc}", file=sys.stderr)
        return 2
    skill_args = argparse.Namespace(
        path=args.skill_path,
        force=args.force,
        keep_existing=bool(getattr(args, "keep_skill", False)),
        target=getattr(args, "skill_target", None) or ("all" if getattr(args, "with_pi_extension", False) else "both"),
    )
    skill_status = skill_install.install_skill(skill_args)
    if skill_status != 0:
        return skill_status
    if getattr(args, "with_pi_extension", False):
        try:
            pi_callback.install_extension(argparse.Namespace(profile=None, force=args.force))
        except (OSError, ValueError) as exc:
            print(f"atc: could not install the Pi extension: {exc}", file=sys.stderr)
            return 2
    reminder_policy.ensure_callback_hook_file()
    report_claude_agent_readiness(args)
    try:
        desktop.install_for_setup(layout.codex_home(), args)
    except (OSError, ValueError) as exc:
        print(f"atc: could not prepare the optional Desktop launcher: {exc}; "
              "CLI setup can continue. Retry with atc desktop prepare.", file=sys.stderr)

    systemd_args = argparse.Namespace(
        name=args.name,
        allow_supervisor_fallback=False,
        queue_dir=args.queue_dir,
        interval=args.interval,
        retries=args.retries,
        retry_delay=args.retry_delay,
        retry_backoff=args.retry_backoff,
        resume_timeout=getattr(args, "resume_timeout", delivery_engine.DEFAULT_RESUME_TIMEOUT),
        restart_sec=args.restart_sec,
        exec_start=args.exec_start,
        codex_bin=args.codex_bin,
        claude_bin=getattr(args, "claude_bin", None),
        pi_bin=getattr(args, "pi_bin", None),
        path=args.path,
        proxy_env_file=getattr(args, "proxy_env_file", None),
        inherit_proxy=bool(getattr(args, "inherit_proxy", False)),
        clear_proxy=bool(getattr(args, "clear_proxy", False)),
        force=args.force,
        enable=args.enable,
        now=args.now,
        print=False,
    )
    if service == "systemd":
        return install_systemd(systemd_args)
    if service == "launchd":
        return install_launchd(systemd_args)
    if service == "windows-task":
        return install_windows_task(systemd_args)
    try:
        tower_environment.configure_proxy_environment(systemd_args)
        if service == "supervisor":
            return install_supervisor(systemd_args)
        if args.enable:
            print("atc: standalone mode has no login/startup registration; use a container entrypoint or external supervisor", file=sys.stderr)
        if args.now:
            return start_standalone_daemon(systemd_args)
        print("Standalone mode selected. Use setup --service standalone --now, or run atc daemon in the foreground.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"atc: could not configure {service} coordinator: {exc}", file=sys.stderr)
        return 2
