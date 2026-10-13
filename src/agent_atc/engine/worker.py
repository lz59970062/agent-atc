"""The task process: run the workload under its owner, observe the outcome and record it."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from ..harness import get_child_agent
from ..system import windows_io, windows_process
from ..store import layout
from ..owners import host
from ..store import records as queue_store
from ..system import shell
from . import tasks as managed_tasks


def run_task_worker(args: argparse.Namespace) -> int:
    """Native service entry; admission is rechecked under the task owner lock."""
    task_path = Path(args.task_file).expanduser().absolute()
    try:
        task = managed_tasks.load_managed_task(task_path)
        root = Path(str(task["queue_dir"])).expanduser().absolute()
        if task_path != layout.managed_task_path(root, str(task["id"])).absolute():
            raise ValueError("task outside its recorded queue")
        lock = queue_store.acquire_owner_lock(root, f"managed-task-{task['id']}", blocking=True)
        if lock is None:
            raise ValueError("task already owned")
        try:
            task = managed_tasks.load_managed_task(task_path)
            if (task.get("execution_backend") not in ("systemd-user", "launchd", "windows-task") or task.get("state") != "launching"
                    or task.get("launch_attempt_count") != args.attempt):
                raise ValueError("stale or unauthorized native worker")
            if task["execution_backend"] in ("launchd", "windows-task"):
                backend = managed_tasks.native_backend(str(task["execution_backend"]))
                owner = backend.owner_name(str(task["id"]), args.attempt, root)
                boot = host.current_boot_id()
                if (task.get("execution_owner") != owner or not backend.admits_worker(owner)
                        or task.get("machine_id") != host.current_machine_id()
                        or not boot or task.get("launch_boot_id") != boot):
                    raise ValueError("worker does not match its native owner, host or boot")
                task["owner_invocation_id"] = owner
            else:
                if not os.environ.get("INVOCATION_ID"):
                    raise ValueError("native worker requires a systemd service invocation")
                task["owner_invocation_id"] = os.environ["INVOCATION_ID"]
            if task["execution_backend"] == "windows-task":
                windows_process.enter_worker_job()
                with Path(str(task["log_path"])).open("a", encoding="utf-8") as log:
                    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                        return run_managed_worker_locked(root, task)
            return run_managed_worker_locked(root, task)
        finally:
            queue_store.release_owner_lock(lock, remove=False)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"atc: native worker refused: {exc}", file=sys.stderr)
        if os.name == "nt" and isinstance(locals().get("task"), dict):
            # Scheduled tasks have no interactive stderr; retain admission
            # failures next to their private record for diagnosis.
            try:
                with (task_path.parent / "worker-admission.log").open("a", encoding="utf-8") as log:
                    log.write(f"native worker refused: {exc}\n")
            except OSError:
                pass
        return 125


def run_screen_worker(args: argparse.Namespace) -> int:
    if not os.environ.get("STY"):
        print("atc: refusing to run screen worker outside GNU screen", file=sys.stderr)
        return 125
    task_path = Path(args.task_file).expanduser().absolute()
    try:
        task = managed_tasks.load_managed_task(task_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"atc: invalid managed task record {task_path}: {exc}", file=sys.stderr)
        return 125
    if task.get("token") != args.token:
        print("atc: refusing unauthorized screen worker", file=sys.stderr)
        return 125
    root = Path(str(task["queue_dir"])).expanduser().absolute()
    if task_path != layout.managed_task_path(root, str(task["id"])).absolute():
        print("atc: refusing managed task outside its recorded queue", file=sys.stderr)
        return 125
    lock = queue_store.acquire_owner_lock(root, f"managed-task-{task['id']}", blocking=True)
    if lock is None:
        print("atc: could not acquire managed task ownership", file=sys.stderr)
        return 125
    try:
        try:
            task = managed_tasks.load_managed_task(task_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"atc: invalid managed task record after worker handoff {task_path}: {exc}", file=sys.stderr)
            return 125
        if task.get("token") != args.token or task.get("state") != "launching":
            print("atc: refusing stale or unauthorized screen worker", file=sys.stderr)
            return 125
        return run_screen_worker_locked(root, task)
    finally:
        queue_store.release_owner_lock(lock, remove=False)


def run_screen_worker_locked(root: Path, task: dict[str, object]) -> int:
    """Compatibility entry point for the screen backend and older integrations."""
    return run_managed_worker_locked(root, task)


def run_managed_worker_locked(root: Path, task: dict[str, object]) -> int:
    environment_path = Path(str(task["environment_path"]))
    try:
        environment_record = json.loads(environment_path.read_text(encoding="utf-8"))
        environment = environment_record["environment"]
        if not isinstance(environment, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in environment.items()
        ):
            raise ValueError("invalid environment payload")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        task["state"] = "interrupted"
        task["outcome"] = "unknown"
        task["recovery_reason"] = f"launch_environment_invalid: {exc}"
        managed_tasks.write_managed_task(root, task)
        managed_tasks.queue_managed_task_callback(root, task)
        return 125
    os.environ.update(environment)
    if os.name == "nt":
        environment["PYTHONIOENCODING"] = "utf-8"
    try:
        environment_path.unlink()
        queue_store.fsync_directory(environment_path.parent)
    except FileNotFoundError:
        pass
    task.pop("token", None)
    task["state"] = "running"
    task["outcome"] = "running"
    task["boot_id"] = host.current_boot_id()
    task["started_at"] = time.time()
    task["worker_ready_at"] = task["started_at"]
    task["wrapper_pid"] = os.getpid()
    managed_tasks.write_managed_task(root, task)
    exit_code = 127
    try:
        command = [str(part) for part in task["wrapped_command"]]
        run_kwargs: dict[str, object] = {
            "cwd": str(task["cwd"]),
            "shell": False,
            "check": False,
            "close_fds": True,
        }
        if os.name == "nt":
            run_kwargs.update(stdout=sys.stdout, stderr=sys.stderr, env=environment,
                              **windows_process.background_popen_kwargs())
            command = shell.process_command(command, environment)
        if task.get("task_kind") == "agent":
            prompt_path = Path(str(task["agent_prompt_path"]))
            result_path = Path(str(task["agent_result_path"]))
            prompt = prompt_path.read_text(encoding="utf-8")
            system_prompt = task.get("agent_system_prompt_path")
            if system_prompt is not None:
                expected = prompt_path.parent / managed_tasks.AGENT_SYSTEM_PROMPT_FILE_NAME
                if not isinstance(system_prompt, str) or Path(system_prompt) != expected:
                    raise ValueError("system prompt snapshot must be in its managed task directory")
                # Pi otherwise treats a missing/unreadable filename as literal
                # system text or silently falls back to its default prompt.
                managed_tasks.read_agent_system_prompt(expected)
            run_kwargs.update(
                {
                    "input": prompt,
                    "text": True,
                    "encoding": "utf-8",
                    "env": managed_tasks.child_agent_environment(environment),
                }
            )
            captures_stdout = get_child_agent(str(task["agent_worker"])).child_result_mode == "stdout"
            if captures_stdout:
                run_kwargs["stdout"] = subprocess.PIPE
            completed = subprocess.run(command, **run_kwargs)
            if captures_stdout:
                output = completed.stdout if isinstance(completed.stdout, str) else ""
                queue_store.write_private_text(result_path, output)
                if output:
                    sys.stdout.write(output)
                    if not output.endswith("\n"):
                        sys.stdout.write("\n")
                    sys.stdout.flush()
            elif result_path.exists():
                if os.name == "nt":
                    windows_io.secure_file(result_path)
                else:
                    os.chmod(result_path, 0o600)
            else:
                queue_store.write_private_text(result_path, "")
        else:
            completed = subprocess.run(command, **run_kwargs)
        exit_code = completed.returncode
        completed_at = time.time()
        result = {
            "version": 1,
            "id": task["id"],
            "exit_code": exit_code,
            "completed_at": completed_at,
            "outcome": "completed",
        }
        queue_store.write_request(layout.managed_result_path(root, str(task["id"])), result)
    except (OSError, UnicodeError, ValueError) as exc:
        task["state"] = "interrupted"
        task["outcome"] = "unknown"
        task["recovery_reason"] = f"wrapped_command_launch_failed: {exc}"
        task["interrupted_at"] = time.time()
        managed_tasks.write_managed_task(root, task)
        managed_tasks.queue_managed_task_callback(root, task)
        return exit_code

    # The durable result is authoritative. Failure to publish metadata or a
    # callback must not reclassify successful execution as a launch failure.
    task.update({key: result[key] for key in ("exit_code", "completed_at", "outcome")})
    task["state"] = "completed"
    try:
        managed_tasks.write_managed_task(root, task)
        managed_tasks.queue_managed_task_callback(root, task)
    except (OSError, ValueError) as exc:
        print(f"atc: result saved for {task['id']}; callback recovery deferred: {exc}", file=sys.stderr)
    return exit_code
