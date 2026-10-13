from __future__ import annotations

import argparse
import base64
import contextlib
from email.message import EmailMessage
import hashlib
import importlib.resources as resources
import json
import os
import re
import secrets
import signal
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

try:
    import fcntl
except ImportError:  # pragma: no cover - the durable run lifecycle is POSIX-only
    fcntl = None

from . import __version__
from .core import envelope as callbacks
from . import callback_transport
from .harness.claude import session as claude_code
from .harness.pi import channel as pi_callback
from .install import diagnostics
from .store import files as storage
from . import template_registration
from .harness.codex.desktop.connection import BridgeEndpoint, UnixBridgeEndpoint, load_bridge_endpoint, load_unix_bridge_endpoint
from .harness import AGENTS, AGENT_NAMES, CHILD_AGENT_NAMES, ChildOptions, get_agent, get_child_agent
from .harness.base import (
    DEFAULT_APPROVALS_REVIEWER,
    DEFAULT_APPROVAL_POLICY,
    DEFAULT_SANDBOX_MODE,
    DEFAULT_CLAUDE_PERMISSION_MODE,
)
from .harness.codex.adapter import CODEX_THREAD_ID_ENV
from .harness.claude.adapter import (
    CLAUDE_THREAD_ID_ENV,
    CLAUDE_MARKER_ENV,
    CLAUDE_BIN_ENV,
    CLAUDE_PERMISSION_MODE_ENV,
)
from .harness.pi.adapter import PI_BIN_ENV, PI_SESSION_FILE_ENV
from .runtime import worker_command
from .owners import LaunchError, OwnerState, ScreenBackend, SystemdUserBackend, LaunchdBackend
from .owners import linux as linux_platform
from .owners import macos as macos_platform
from .system import posix as posix_platform
from .owners import windows as windows_platform
from .system import windows_io, windows_process
from .owners.windows import WindowsBackend
from .templates import TEMPLATES, REASONING_EFFORTS, load_template
from .store import layout
from .owners import host
from . import runtime as atc_runtime
from .store import records as queue_store
from .system import commands
from .engine import delivery as delivery_engine
from .engine import reminders as reminder_policy
from .harness.codex import app_server
from .engine import dispatch
from .engine import goals
from .engine import recovery
from .engine import tasks as managed_tasks
from .engine import worker as task_worker
from .install import services
from .install import skills as skill_install
from .tower import main as tower


def done(args: argparse.Namespace) -> int:
    callback_code = dispatch.queue_callback(args, dispatch.build_prompt(args))
    return callback_code if args.strict else 0


def run(args: argparse.Namespace) -> int:
    if not args.wrapped_command:
        raise SystemExit("run mode requires a command after --")

    args.command = args.command or commands.format_command(args.wrapped_command)
    dispatch.bind_target(args)
    if args.dry_run:
        print(
            "\n".join(
                [
                    "[atc-submission-dry-run]",
                    f"Task: {args.task}",
                    f"Working directory: {os.path.abspath(args.cwd)}",
                    f"Command: {args.command}",
                    f"Execution: daemon -> {getattr(args, 'backend', 'auto')} backend -> independent worker -> task",
                    "No task record was written and no command was started.",
                ]
            )
        )
        return 0
    return dispatch.submit_managed_run(args)


def agent(args: argparse.Namespace) -> int:
    args.agent_prompt_text = managed_tasks.agent_prompt_text(list(args.agent_prompt))
    args.template = getattr(args, "template", None)
    args.template_file = getattr(args, "template_file", None)
    args.template_version = None
    args.template_source = None
    args.template_handoff = None
    args.child_model = getattr(args, "child_model", None)
    args.child_reasoning_effort = getattr(args, "child_reasoning_effort", None)
    system_prompt_file = getattr(args, "system_prompt_file", None)
    args.agent_system_prompt_source = None
    args.agent_system_prompt_text = None
    if system_prompt_file is not None and not str(system_prompt_file).strip():
        raise SystemExit("--system-prompt-file must not be empty")
    if not args.agent_prompt_text.strip():
        raise SystemExit("agent mode requires non-empty task requirements")
    if args.child_model is not None and not args.child_model.strip():
        raise SystemExit("--model must not be empty")
    child_adapter = get_child_agent(args.agent_worker)
    if args.child_reasoning_effort and not child_adapter.supports_reasoning_effort:
        raise SystemExit(f"{child_adapter.display_name} children do not support --reasoning-effort")
    if args.template is not None or args.template_file is not None:
        try:
            profile = load_template(args.template, template_file=args.template_file)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"atc: {exc}") from exc
        args.template = profile.name
        args.template_version = profile.version
        args.template_source = profile.source
        args.template_handoff = profile.handoff
        args.child_model = args.child_model or profile.model_for(args.agent_worker)
        args.child_reasoning_effort = args.child_reasoning_effort or profile.reasoning_effort_for(args.agent_worker)
        system_prompt_file = system_prompt_file or profile.system_prompt_file_for(args.agent_worker)
        args.agent_prompt_text = profile.render(args.agent_prompt_text)
    if args.child_reasoning_effort and args.child_reasoning_effort not in child_adapter.reasoning_efforts:
        raise SystemExit(f"unsupported {child_adapter.display_name} reasoning effort {args.child_reasoning_effort!r}")
    if system_prompt_file is not None:
        if not child_adapter.supports_system_prompt_file:
            raise SystemExit(f"{child_adapter.display_name} children do not support --system-prompt-file")
        try:
            source = Path(system_prompt_file).expanduser().resolve()
            args.agent_system_prompt_text = managed_tasks.read_agent_system_prompt(source)
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            raise SystemExit(f"atc: could not load system prompt file: {exc}") from exc
        args.agent_system_prompt_source = str(source)
    args.task_kind = "agent"
    args.command = args.command or f"atc agent {args.agent_worker}"
    args.wrapped_command = []
    dispatch.bind_target(args)
    if args.dry_run:
        print(
            "\n".join(
                [
                    "[atc-agent-submission-dry-run]",
                    f"Task: {args.task}",
                    f"Working directory: {os.path.abspath(args.cwd)}",
                    f"Child agent: {child_adapter.display_name}",
                    f"Template: {args.template or 'none'} (version {getattr(args, 'template_version', None)})",
                    f"Template source: {args.template_source or 'none'}",
                    f"Child model: {args.child_model or 'CLI default'}",
                    f"Child reasoning effort: {args.child_reasoning_effort or 'CLI default'}",
                    f"System prompt source: {args.agent_system_prompt_source or 'CLI default'}",
                    f"Expanded prompt:\n{args.agent_prompt_text}",
                    f"Template handoff: {args.template_handoff or 'none'}",
                    f"Execution: daemon -> {getattr(args, 'backend', 'auto')} backend -> independent worker -> child agent",
                    "No task record was written and no child agent was started.",
                ]
            )
        )
        return 0
    return dispatch.submit_managed_run(args)


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--callback-mode", choices=callback_transport.MODES, default="auto",
                        help="Callback route: auto detects Codex Desktop; cli uses existing CLI/shared service; desktop requires its bridge; manual saves for later receipt")
    parser.add_argument("--backend", choices=("auto", "systemd", "launchd", "windows-task", "screen"),
                        default=os.environ.get("ATC_EXECUTION_BACKEND", "auto"),
                        help="Task owner: systemd on Linux, launchd on macOS, windows-task on Windows; screen is the POSIX fallback")
    parser.add_argument("--callback-format", choices=("compact", "full"), default="compact",
                        help="Callback envelope (default: compact, verbose evidence saved locally)")
    parser.add_argument(
        "--agent",
        choices=AGENT_NAMES,
        help="Agent to wake (default: auto-detect from $PI_SESSION_FILE/$PI_SESSION_ID, $CLAUDECODE/$CLAUDE_CODE_SESSION_ID or $CODEX_THREAD_ID)",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--session",
        help=f"Agent session id to resume (default: ${PI_SESSION_FILE_ENV}, ${CLAUDE_THREAD_ID_ENV} or ${CODEX_THREAD_ID_ENV} from the launching agent thread)",
    )
    target.add_argument(
        "--last",
        action="store_true",
        help="Unsafely resume the most recent agent session instead of binding the launching thread",
    )
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for the task and resumed agent")
    parser.add_argument("--task", default="long task", help="Human-readable task name")
    parser.add_argument("--command", help="Original command text")
    parser.add_argument("--exit-code", type=int, help="Completed task exit code")
    parser.add_argument("--message", help="Extra callback message")
    parser.add_argument(
        "--via-daemon",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--queue-dir", help="Wakeup queue directory for daemon task submission and callback delivery")
    parser.add_argument("--goal-id", help="Explicit goal record to update when this callback is queued")
    parser.add_argument(
        "--approvals-reviewer",
        default=os.environ.get("ATC_APPROVALS_REVIEWER", DEFAULT_APPROVALS_REVIEWER),
        help="Codex-only: approvals_reviewer config value used when resuming (default: auto_review)",
    )
    parser.add_argument(
        "--approval-policy",
        default=os.environ.get("ATC_APPROVAL_POLICY", DEFAULT_APPROVAL_POLICY),
        help="Codex-only: approval_policy config value used when resuming (default: on-request)",
    )
    parser.add_argument(
        "--sandbox-mode",
        default=os.environ.get("ATC_SANDBOX_MODE", DEFAULT_SANDBOX_MODE),
        help="Codex-only: sandbox mode used by child agents and resumed callbacks (default: workspace-write)",
    )
    parser.add_argument(
        "--permission-mode",
        default=os.environ.get(CLAUDE_PERMISSION_MODE_ENV, DEFAULT_CLAUDE_PERMISSION_MODE),
        help="Claude Code-only: permission mode used by child agents and resumed callbacks (default: auto)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the run submission or done callback without writing queue state",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Propagate callback failure. By default callback failure never changes task success or exit code.",
    )


def template_command(args: argparse.Namespace) -> int:
    try:
        if args.template_action == "register":
            result = template_registration.register(args)
        elif args.template_action == "unregister":
            result = template_registration.unregister(args)
        else:
            result = template_registration.list_templates()
            if not args.json:
                for entry in result:
                    print(f"{entry['name']} ({entry['worker']}, ${entry['skill']}): {entry['description']}")
                if not result:
                    print("No registered child-agent templates.")
                return 0
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.dry_run or args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.template_action == "register":
            print(f"Registered {result['name']} for {result['worker']}.")
            print(f"Skill: ${result['skill']}")
            print(f"Template: {result['template_file']}")
        else:
            print(f"{result['name']} is unregistered; template and system prompt files retained.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"atc: {exc}", file=sys.stderr)
        return 1


def add_proxy_environment_flags(parser: argparse.ArgumentParser) -> None:
    proxy_source = parser.add_mutually_exclusive_group()
    proxy_source.add_argument(
        "--proxy-env-file",
        help="Read only proxy variables from this .env-style file and persist them for the daemon service",
    )
    proxy_source.add_argument(
        "--inherit-proxy",
        action="store_true",
        help="Persist proxy variables from this command environment for the daemon service",
    )
    proxy_source.add_argument(
        "--clear-proxy",
        action="store_true",
        help="Remove the persisted proxy environment file before updating the daemon service",
    )


def ack(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args)
    queue_store.ensure_daemon_dirs(root)
    marker = layout.ack_path(root, args.id)
    payload = {
        "id": args.id,
        "marked_at": time.time(),
    }
    if args.message:
        payload["message"] = args.message
    queue_store.write_request(marker, payload)
    for state in ("failed", "running", "pending"):
        source = layout.request_path(root, state, args.id)
        if not source.exists():
            continue
        try:
            request = dispatch.load_request(source)
            if state == "failed" or (state == "pending" and (request.get("callback_mode") == "manual"
                                                              or request.get("live_delivered_at"))):
                queue_store.move_request(source, root / "done")
            try:
                delivery_engine.release_retained_target_lease(root, request)
            except (OSError, RuntimeError) as exc:
                print(
                    f"atc: warning: callback {args.id} is durably acknowledged; "
                    f"daemon will reconcile its retained target lease: {exc}",
                    file=sys.stderr,
                )
        except FileNotFoundError:
            continue
        break
    print(f"atc: acknowledged callback {args.id} in {root}", file=sys.stderr)
    return 0


def retry_callback(args: argparse.Namespace) -> int:
    """Requeue a known callback; never resubmit its completed workload."""
    root = layout.queue_dir(args).expanduser().absolute()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.id):
        print("atc: invalid callback id", file=sys.stderr)
        return 2
    delivery_lock = target_lock = None
    try:
        queue_store.ensure_daemon_dirs(root)
        delivery_lock = queue_store.acquire_owner_lock(root, layout.delivery_lock_id(args.id), blocking=False)
        if delivery_lock is None:
            raise ValueError("callback delivery is still active")
        source = layout.request_path(root, "failed", args.id)
        if not source.exists():
            source = layout.request_path(root, "pending", args.id)
        request = dispatch.load_request(source)
        if source.parent.name == "pending" and request.get("callback_mode") != "manual":
            raise ValueError("callback is already pending")
        if layout.ack_path(root, args.id).exists() or queue_store.is_canceled(root, args.id):
            raise ValueError("acknowledged or canceled callbacks cannot be retried")
        if request.get("retain_target_lease") is True or delivery_engine.desktop_submission_pending(root, request):
            raise ValueError("previous submission outcome is unknown; inspect the original session before recovery")
        target_lock = delivery_engine.acquire_target_lock(request, blocking=False)
        if target_lock is None or delivery_engine.retained_target_lease_is_held(request):
            raise ValueError("the bound session has an active or unresolved delivery")
        if args.callback_mode:
            options = argparse.Namespace(callback_mode=args.callback_mode, agent=dispatch.request_agent(request))
            pi_route = {key: request[key] for key in pi_callback.ROUTE_FIELDS if key in request}
            for key in callback_transport.ROUTE_FIELDS:
                request.pop(key, None)
            request.update(callback_transport.selection(options))
            request.update(pi_route)
            request["version"] = 3 if dispatch.request_agent(request) == "pi" else 2
            if request.get("pi_delivery") == "steer":
                request["version"] = 4
        capability = callback_transport.inspect_route(request)
        if capability["status"] == "blocked":
            raise ValueError("callback route remains blocked: " + str(capability.get("reason")))
        history = request.setdefault("retry_history", [])
        if not isinstance(history, list):
            raise ValueError("callback retry history is invalid")
        history.append({"at": time.time(), "attempts": request.get("attempts", 0), "last_error": request.get("last_error")})
        request["retry_history"] = history[-10:]
        request["attempts"] = 0
        for key in ("last_error", "delivery_state", "next_attempt_at", "last_deferred_reason"):
            request.pop(key, None)
        if not source.exists() or layout.ack_path(root, args.id).exists() or queue_store.is_canceled(root, args.id):
            raise ValueError("callback changed during recovery; inspect its current state")
        queue_store.write_request(source, request)
        if source.parent.name != "pending":
            queue_store.move_request(source, root / "pending")
        print(f"atc: requeued callback {args.id}; business task was not relaunched", file=sys.stderr)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"atc: callback retry refused: {error}", file=sys.stderr)
        return 2
    finally:
        if target_lock is not None:
            queue_store.release_owner_lock(target_lock, remove=False)
        if delivery_lock is not None:
            queue_store.release_owner_lock(delivery_lock, remove=False)


def goal_start(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args)
    queue_store.ensure_daemon_dirs(root)
    target, source = dispatch.resolve_target(args)
    goal_id = args.id or uuid.uuid4().hex
    try:
        path = layout.goal_path(root, goal_id)
    except ValueError as exc:
        print(f"atc: error: {exc}", file=sys.stderr)
        return 2
    if args.idle_seconds <= 0:
        print("atc: error: --idle-seconds must be positive", file=sys.stderr)
        return 2
    try:
        plan_path, plan, plan_digest = goals.resolve_goal_plan_file(
            args.cwd, getattr(args, "plan_file", None)
        )
    except ValueError as exc:
        print(f"atc: error: {exc}", file=sys.stderr)
        return 2
    lock = queue_store.acquire_owner_lock(root, layout.goal_lock_id(goal_id), blocking=True)
    try:
        if path.exists():
            print(f"atc: goal {goal_id} already exists", file=sys.stderr)
            return 1
        now = time.time()
        goals.write_goal(root, {"version": 1, "id": goal_id, "task": args.task, "cwd": args.cwd, "target": target,
                          "target_source": source, "agent": dispatch.resolve_agent(args), "state": "active", "created_at": now,
                          "last_external_callback_at": now, "idle_seconds": args.idle_seconds,
                          "plan_file": str(plan_path), "plan_goal": plan["goal"],
                          "plan_sha256_at_start": plan_digest})
    finally:
        queue_store.release_owner_lock(lock, remove=False)
    print(goal_id)
    return 0


def goal_check(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args)
    try:
        layout.goal_path(root, args.id)
    except ValueError as exc:
        print(f"atc: error: {exc}", file=sys.stderr)
        return 2
    lock = queue_store.acquire_owner_lock(root, layout.goal_lock_id(args.id), blocking=True)
    try:
        try:
            goal = goals.load_goal(root, args.id)
            plan_path, plan, content, digest = goals.goal_plan_snapshot(goal)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"atc: error: {exc}", file=sys.stderr)
            return 1
        if goal.get("state") == "completed":
            print(f"atc: error: completed goal {args.id} is terminal", file=sys.stderr)
            return 1
        completed, total, current, remaining = goals.goal_plan_progress(plan)
        now = time.time()
        goal["last_plan_checked_at"] = now
        goal["last_plan_check_sha256"] = digest
        goal["plan_goal"] = plan["goal"]
        goal["plan_revision"] = plan["revision"]
        goal["plan_completed_items"] = completed
        goal["plan_total_items"] = total
        goals.write_goal(root, goal)
    finally:
        queue_store.release_owner_lock(lock, remove=False)

    print(f"Goal: {plan['goal']}")
    print(f"Plan revision: {plan['revision']}")
    print(f"Plan file: {plan_path}")
    print(f"Progress: {completed}/{total} path items completed")
    if current is None:
        print("Current item: none; the final path item is completed")
    else:
        print(
            f"Current item: {current['id']} [{current['status']}] - {current['title']}"
        )
        print("Remaining path:")
        for step in remaining:
            print(f"- {step['id']} [{step['status']}] - {step['title']}")
    print(f"Plan SHA-256: {digest}")
    print("\n--- YAML goal plan begin ---")
    print(content, end="" if content.endswith("\n") else "\n")
    print("--- YAML goal plan end ---")
    print(
        "Compare actual work and artifacts with every path item above. Do not report the goal "
        "complete unless the final item and every preceding item are confirmed completed."
    )
    return 0


def goal_set_plan(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args)
    try:
        layout.goal_path(root, args.id)
    except ValueError as exc:
        print(f"atc: error: {exc}", file=sys.stderr)
        return 2
    lock = queue_store.acquire_owner_lock(root, layout.goal_lock_id(args.id), blocking=True)
    try:
        try:
            goal = goals.load_goal(root, args.id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"atc: error: {exc}", file=sys.stderr)
            return 1
        if goal.get("state") == "completed":
            print(f"atc: error: completed goal {args.id} is terminal", file=sys.stderr)
            return 1
        try:
            plan_path, plan, plan_digest = goals.resolve_goal_plan_file(
                str(goal.get("cwd", os.getcwd())), args.plan_file
            )
        except ValueError as exc:
            print(f"atc: error: {exc}", file=sys.stderr)
            return 2
        goal.update(
            {
                "plan_file": str(plan_path),
                "plan_goal": plan["goal"],
                "plan_revision": plan["revision"],
                "plan_sha256_when_attached": plan_digest,
                "plan_attached_at": time.time(),
            }
        )
        for field in (
            "last_plan_checked_at",
            "last_plan_check_sha256",
            "plan_completed_items",
            "plan_total_items",
        ):
            goal.pop(field, None)
        goals.write_goal(root, goal)
    finally:
        queue_store.release_owner_lock(lock, remove=False)
    print(f"atc: goal {args.id} now follows YAML plan {plan_path}")
    print("atc: run goal check before acknowledging completion")
    return 0


def goal_ack(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args)
    try:
        layout.goal_path(root, args.id)
    except ValueError as exc:
        print(f"atc: error: {exc}", file=sys.stderr)
        return 2
    if args.state == "blocked_conditions":
        if not args.condition or not args.condition.strip():
            print("atc: error: --condition is required for blocked_conditions", file=sys.stderr)
            return 2
        if args.email_after <= 0:
            print("atc: error: --email-after must be positive", file=sys.stderr)
            return 2
        if args.email_to and not goals.is_single_email_recipient(args.email_to):
            print("atc: error: invalid email recipient", file=sys.stderr)
            return 2
        configured_recipient = os.environ.get("ATC_BLOCKED_EMAIL_TO")
        if args.email_to and args.email_to != configured_recipient:
            print("atc: error: --email-to must match ATC_BLOCKED_EMAIL_TO", file=sys.stderr)
            return 2
    lock = queue_store.acquire_owner_lock(root, layout.goal_lock_id(args.id), blocking=True)
    try:
        try:
            goal = goals.load_goal(root, args.id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"atc: error: {exc}", file=sys.stderr)
            return 1
        if goal.get("state") == "completed" and args.state != "completed":
            print(f"atc: error: completed goal {args.id} is terminal", file=sys.stderr)
            return 1
        if args.state == "completed":
            try:
                plan_path, plan, _, plan_digest = goals.goal_plan_snapshot(goal)
            except ValueError as exc:
                print(f"atc: error: {exc}", file=sys.stderr)
                return 1
            supplied_digest = getattr(args, "plan_sha256", None)
            if not supplied_digest:
                print(
                    f"atc: error: run 'atc goal check --id {args.id}' and pass its digest "
                    "with --plan-sha256 before completing the goal",
                    file=sys.stderr,
                )
                return 2
            checked_digest = goal.get("last_plan_check_sha256")
            if supplied_digest != plan_digest or checked_digest != plan_digest:
                print(
                    f"atc: error: YAML goal plan changed or was not checked: {plan_path}; "
                    f"rerun 'atc goal check --id {args.id}' and compare the work again",
                    file=sys.stderr,
                )
                return 1
            completed_items, total_items, current, _ = goals.goal_plan_progress(plan)
            if current is not None:
                print(
                    f"atc: error: goal path is only {completed_items}/{total_items} complete; "
                    f"current item is {current['id']} [{current['status']}] - {current['title']}",
                    file=sys.stderr,
                )
                return 1
        now = time.time()
        goal["state"] = args.state
        goal["state_changed_at"] = now
        if args.message:
            goal["message"] = args.message
        if args.state == "blocked_conditions":
            goals.clear_blocked_goal_email(goal)
            goal["condition"] = args.condition
            goal["blocked_email_to"] = args.email_to
            goal["blocked_email_after_seconds"] = args.email_after
        else:
            goal.pop("condition", None)
            goals.clear_blocked_goal_email(goal)
            goal["completion_plan_sha256"] = plan_digest
            goal["completion_plan_revision"] = plan["revision"]
            goal["completion_plan_checked_at"] = goal["last_plan_checked_at"]
        goals.write_goal(root, goal)
    finally:
        queue_store.release_owner_lock(lock, remove=False)
    print(f"atc: goal {args.id} acknowledged as {args.state}")
    return 0


def goal_resume(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args)
    try:
        layout.goal_path(root, args.id)
    except ValueError as exc:
        print(f"atc: error: {exc}", file=sys.stderr)
        return 2
    lock = queue_store.acquire_owner_lock(root, layout.goal_lock_id(args.id), blocking=True)
    try:
        try:
            goal = goals.load_goal(root, args.id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"atc: error: {exc}", file=sys.stderr)
            return 1
        if goal.get("state") == "completed":
            print("atc: error: completed goals are terminal", file=sys.stderr)
            return 1
        if goal.get("state") != "blocked_conditions":
            print("atc: error: only blocked goals can be resumed", file=sys.stderr)
            return 1
        now = time.time()
        goal.update({"state": "active", "state_changed_at": now, "last_external_callback_at": now})
        goal.pop("condition", None)
        goal.pop("last_reminder_at", None)
        goals.clear_blocked_goal_email(goal)
        goals.write_goal(root, goal)
    finally:
        queue_store.release_owner_lock(lock, remove=False)
    print(f"atc: goal {args.id} resumed")
    return 0


def cancel(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args)
    if args.all:
        recovery.cancel_all(root, args.message)
        return 0
    return 0 if recovery.cancel_one(root, args.id, args.message) else 1


def queued_items(root: Path, states: list[str]) -> list[tuple[str, Path, dict[str, object]]]:
    items: list[tuple[str, Path, dict[str, object]]] = []
    for state in states:
        for path in sorted((root / state).glob("*.json")):
            try:
                request = dispatch.load_request(path)
            except Exception as exc:
                request = {"id": path.stem, "prompt": "Task: unreadable callback", "last_error": str(exc)}
            items.append((state, path, request))
    items.sort(key=lambda item: float(item[2].get("created_at", 0.0)))
    return items


def status(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args)
    states = args.state or ["pending", "running"]
    items = queued_items(root, states)
    if not items:
        if not args.quiet_empty:
            print(f"No pending long-task callbacks in {root}")
        return 0

    counts = {state: sum(1 for item in items if item[0] == state) for state in states}
    summary = ", ".join(f"{state}={count}" for state, count in counts.items() if count)
    print(f"[atc] {len(items)} item(s) need attention ({summary})")
    limit = max(1, args.limit)
    for state, path, request in items[:limit]:
        request_id = str(request.get("id", path.stem))
        line = f"  - [{state}] {dispatch.request_task(request)} | {dispatch.request_target(request)} | id {request_id}"
        if request.get("callback_mode") == "manual":
            line += " | manual receipt; automatic callback disabled"
        if request.get("delivery_state") == "blocked":
            line += " | callback blocked; saved workload result retained"
        if state == "running" and layout.ack_path(root, request_id).exists():
            line += " | acknowledged; resumed Codex process still active"
        error = request.get("last_error")
        if isinstance(error, str) and error:
            line += f" | {error}"
        print(line)
    if len(items) > limit:
        print(f"  ... {len(items) - limit} more item(s)")
    print("  Inspect: atc status --state pending --state running")
    return 0


def invoke_with_diagnostics(args: argparse.Namespace, operation: Callable[[argparse.Namespace], int]) -> int:
    """Keep task admission/exit semantics; report configuration gaps afterward.

    Do not apply this wrapper to workers or acknowledgement/configuration paths:
    those must remain usable while an Agent repairs an incomplete installation.
    """
    try:
        return operation(args)
    finally:
        if not getattr(args, "dry_run", False):
            diagnostics.emit_if_needed(args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicit callback tool for waking Codex or Claude Code after a long task.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="mode", required=True)

    doctor_parser = sub.add_parser("doctor", help="Check local readiness and print an Agent-owned repair plan as JSON")
    doctor_parser.add_argument("--queue-dir", help="Queue to inspect and configure")
    doctor_parser.add_argument("--backend", choices=("auto", "systemd", "launchd", "windows-task", "screen"),
                               default=os.environ.get("ATC_EXECUTION_BACKEND", "auto"))
    doctor_parser.add_argument("--operation", choices=("run", "agent", "done"), default="run",
                               help="Workflow whose prerequisites are checked (default: run)")
    doctor_parser.add_argument("--agent", choices=AGENT_NAMES, help="Callback Agent (default: detect current Agent)")
    doctor_parser.add_argument("--agent-worker", choices=CHILD_AGENT_NAMES, help="Child Agent for --operation agent")
    doctor_parser.add_argument("--callback-mode", choices=callback_transport.MODES, default="auto")
    doctor_target = doctor_parser.add_mutually_exclusive_group()
    doctor_target.add_argument("--session", help="Original Agent session to preserve during repair")
    doctor_target.add_argument("--last", action="store_true", help="Inspect an already explicitly chosen unsafe last-session target")

    prompt_policy_parser = sub.add_parser("prompt-policy", help="Show or set callback reminder intervals")
    prompt_policy_parser.add_argument("--system-every", type=reminder_policy.positive_prompt_interval, help="Show standard reminders every N distinct callbacks (default: 4)")
    prompt_policy_parser.add_argument("--user-every", type=reminder_policy.positive_prompt_interval, help="Show callback-hook reminders every N distinct callbacks (default: 3)")

    retry_parser = sub.add_parser("retry", help="Requeue a failed/manual callback without rerunning its workload")
    retry_parser.add_argument("--id", required=True)
    retry_parser.add_argument("--queue-dir")
    retry_parser.add_argument("--callback-mode", choices=callback_transport.MODES)

    desktop_parser = sub.add_parser("desktop", help="Prepare, inspect or launch the opt-in Mac/Windows Desktop shared Core")
    desktop_actions = desktop_parser.add_subparsers(dest="desktop_action", required=True)
    prepare = desktop_actions.add_parser("prepare", help="Save configuration and a double-click launcher without restarting Desktop")
    prepare.add_argument("--app", help="Desktop .app on Mac or .exe on Windows (default: platform discovery)")
    prepare.add_argument("--wrapper", help="Custom Core wrapper (default: preserve selection, or use this ATC installation)")
    prepare.add_argument("--core", help="Windows Desktop Core .exe override (default: matching Desktop Core)")
    prepare.add_argument("--launch-mode", choices=("direct", "package-context"),
                         help="Windows launch route; package-context is experimental and explicitly selected")
    prepare.add_argument("--force", action="store_true")
    desktop_actions.add_parser("status", help="Inspect the configured Core identity")
    launch = desktop_actions.add_parser("launch", help="Launch Desktop only after its existing process has exited")
    launch.add_argument("--check-only", action="store_true")

    done_parser = sub.add_parser("done", help="Queue a callback after an externally managed task finishes")
    add_common_flags(done_parser)

    run_parser = sub.add_parser("run", help="Submit a command to an independent Linux, macOS or Windows task owner")
    add_common_flags(run_parser)
    run_parser.add_argument("wrapped_command", nargs=argparse.REMAINDER)

    agent_parser = sub.add_parser("agent", help="Run a durable fresh Codex, Claude Code or PI Agent child")
    agent_sub = agent_parser.add_subparsers(dest="agent_worker", required=True)
    for worker in CHILD_AGENT_NAMES:
        adapter = get_child_agent(worker)
        child_parser = agent_sub.add_parser(worker, help=f"Run a fresh {adapter.display_name} child agent")
        add_common_flags(child_parser)
        template_source = child_parser.add_mutually_exclusive_group()
        template_source.add_argument("--template", help="User template name in ~/.config/atc/templates, or built-in test")
        template_source.add_argument("--template-file", help="Custom YAML template file (relative to the submitting shell directory)")
        child_parser.add_argument("--model", dest="child_model", help="Child model only; overrides template default")
        if adapter.supports_system_prompt_file:
            child_parser.add_argument(
                "--system-prompt-file",
                help="UTF-8 system prompt file; overrides template default and freezes its text at submission",
            )
        child_parser.add_argument(
            "--reasoning-effort", dest="child_reasoning_effort",
            choices=adapter.reasoning_efforts or REASONING_EFFORTS,
            help="Child reasoning effort; PI Agent maps this to --thinking; model must support it",
        )
        child_parser.add_argument("agent_prompt", nargs=argparse.REMAINDER)

    screen_worker_parser = sub.add_parser("_screen-worker", help=argparse.SUPPRESS)
    screen_worker_parser.add_argument("--task-file", required=True)
    screen_worker_parser.add_argument("--token", required=True)

    task_worker_parser = sub.add_parser("_task-worker", help=argparse.SUPPRESS)
    task_worker_parser.add_argument("--task-file", required=True)
    task_worker_parser.add_argument("--attempt", type=int, required=True)
    sub.add_parser("_delivery-worker", help=argparse.SUPPRESS)
    windows_coordinator_parser = sub.add_parser("_windows-coordinator", help=argparse.SUPPRESS)
    windows_coordinator_parser.add_argument("--config-file", required=True)
    uninstall_windows_parser = sub.add_parser("uninstall-windows-task", help="Remove Windows coordinator login registration and drain deliveries")
    uninstall_windows_parser.add_argument("--name", default="agent-atc")

    daemon_parser = sub.add_parser("daemon", help="Process queued wakeup requests outside Codex tool sandboxes")
    daemon_parser.add_argument("--queue-dir", help="Wakeup queue directory")
    daemon_parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    daemon_parser.add_argument("--once", action="store_true", help="Exit after the queue is empty")
    daemon_parser.add_argument("--max-items", type=int, help="Exit after processing this many queued requests")
    daemon_parser.add_argument("--retries", type=int, default=delivery_engine.DEFAULT_RETRIES, help="Retries after a callback is not acknowledged")
    daemon_parser.add_argument("--retry-delay", type=float, default=delivery_engine.DEFAULT_RETRY_DELAY, help="Initial retry delay in seconds")
    daemon_parser.add_argument("--retry-backoff", type=float, default=delivery_engine.DEFAULT_RETRY_BACKOFF, help="Retry delay multiplier")
    daemon_parser.add_argument("--resume-timeout", type=float, default=delivery_engine.DEFAULT_RESUME_TIMEOUT, help="Maximum seconds for one Codex resume before retrying")

    for service in ("systemd", "launchd", "windows-task"):
        service_parser = sub.add_parser(f"install-{service}", help=f"Install a user-level {service} coordinator")
        service_parser.add_argument("--name", default="agent-atc", help="Service name / LaunchAgent label")
        service_parser.add_argument("--queue-dir", help="Wakeup queue directory")
        service_parser.add_argument("--interval", type=float, default=2.0, help="Daemon polling interval in seconds")
        service_parser.add_argument("--retries", type=int, default=delivery_engine.DEFAULT_RETRIES)
        service_parser.add_argument("--retry-delay", type=float, default=delivery_engine.DEFAULT_RETRY_DELAY)
        service_parser.add_argument("--retry-backoff", type=float, default=delivery_engine.DEFAULT_RETRY_BACKOFF)
        service_parser.add_argument("--resume-timeout", type=float, default=delivery_engine.DEFAULT_RESUME_TIMEOUT)
        service_parser.add_argument("--restart-sec", type=float, default=5.0, help="Restart delay in seconds")
        service_parser.add_argument("--exec-start", help="Path to ATC executable")
        service_parser.add_argument("--codex-bin", help="Path to codex executable used by the daemon")
        service_parser.add_argument("--claude-bin", help="Path to claude executable used by the daemon")
        service_parser.add_argument("--pi-bin", help="Path to the PI Agent executable used by the daemon")
        service_parser.add_argument("--path", help="PATH environment for the daemon service")
        add_proxy_environment_flags(service_parser)
        service_parser.add_argument("--force", action="store_true", help="Overwrite an existing service file")
        service_parser.add_argument("--enable", action="store_true", help="Enable the service for this user")
        service_parser.add_argument("--now", action="store_true", help="Start the service or request a safe reload")
        service_parser.add_argument("--print", action="store_true", help="Print configuration without installing it")

    install_parser = sub.add_parser("install-skill", help="Install the bundled skill for Codex, Claude Code and/or Pi")
    install_parser.add_argument("--path", help="Skills directory to install into (overrides --target)")
    install_parser.add_argument(
        "--target",
        choices=skill_install.SKILL_TARGETS,
        default="both",
        help="Agent home to install into (default: both — ${CODEX_HOME:-~/.codex}/skills and ${CLAUDE_CONFIG_DIR:-~/.claude}/skills; "
             "pi — ${PI_CODING_AGENT_DIR:-~/.pi/agent}/skills; all — the three)",
    )
    install_parser.add_argument("--force", action="store_true", help="Overwrite an existing agent-atc skill")

    pi_install = sub.add_parser("install-pi-extension", help="Install Pi's native live callback extension")
    pi_install.add_argument("--profile", help="Pi profile directory (default: PI_CODING_AGENT_DIR or ~/.pi/agent)")
    pi_install.add_argument("--force", action="store_true", help="Replace an edited ATC extension after review")
    pi_launch = sub.add_parser("pi", help="Launch Pi with a session writer lease acquired before startup")
    pi_launch.add_argument("--cwd", default=os.getcwd(), help="Working directory for Pi")
    pi_launch.add_argument("--session", help="Absolute existing JSONL file (default: a new managed session)")
    pi_launch.add_argument("pi_args", nargs=argparse.REMAINDER, help="Pi options after --")

    template_parser = sub.add_parser("template", help="Register named child templates and advertise their skills")
    template_actions = template_parser.add_subparsers(dest="template_action", required=True)
    register_parser = template_actions.add_parser("register", help="Install a YAML template and discoverable task skill")
    register_parser.add_argument("name", help="Template name: lowercase letters, digits and hyphens")
    register_parser.add_argument("--file", help="Source YAML (default: existing named user template); relative paths use the current shell directory")
    register_parser.add_argument("--worker", choices=CHILD_AGENT_NAMES, help="Child worker (default: YAML worker)")
    register_parser.add_argument("--description", help="Skill discovery description (default: YAML description)")
    register_parser.add_argument("--target", choices=("codex", "claude", "both"), default="both", help="Parent Agent skill profiles to update")
    register_parser.add_argument("--force", action="store_true", help="Replace existing template files or edited owned entrypoints")
    register_parser.add_argument("--dry-run", action="store_true", help="Validate and show registration changes without writing files")
    register_parser.add_argument("--json", action="store_true", help="Print structured registration results")
    list_parser = template_actions.add_parser("list", help="List registered task capabilities")
    list_parser.add_argument("--json", action="store_true", help="Print structured registration metadata")
    unregister_parser = template_actions.add_parser("unregister", help="Remove discovery entrypoints; preserve template and prompt files")
    unregister_parser.add_argument("name")
    unregister_parser.add_argument("--dry-run", action="store_true")
    unregister_parser.add_argument("--json", action="store_true")

    setup_parser = sub.add_parser("setup", help="Install the bundled skill and user-level wakeup daemon")
    setup_parser.add_argument("--backend", choices=("auto", "systemd", "launchd", "windows-task", "screen"), default=os.environ.get("ATC_EXECUTION_BACKEND", "auto"))
    setup_parser.add_argument("--callback-only", action="store_true",
                              help="Configure callbacks for externally owned work without requiring a local task backend")
    setup_parser.add_argument("--service", choices=("auto", "systemd", "launchd", "windows-task", "supervisor", "standalone"), default="auto",
                              help="Coordinator hosting: systemd on Linux, launchd on macOS, Task Scheduler on Windows")
    setup_parser.add_argument("--skill-path", help="Skills directory to install into (overrides --skill-target)")
    setup_parser.add_argument("--with-pi-extension", action="store_true", help="Also install Pi's native live callback extension")
    setup_parser.add_argument(
        "--skill-target",
        choices=skill_install.SKILL_TARGETS,
        help="Agent home to install the skill into (default: both; all with --with-pi-extension)",
    )
    setup_parser.add_argument("--name", default="agent-atc", help="Service name / LaunchAgent label")
    setup_parser.add_argument("--queue-dir", help="Wakeup queue directory")
    setup_parser.add_argument("--interval", type=float, default=2.0, help="Daemon polling interval in seconds")
    setup_parser.add_argument("--retries", type=int, default=delivery_engine.DEFAULT_RETRIES, help="Retries after a callback is not acknowledged")
    setup_parser.add_argument("--retry-delay", type=float, default=delivery_engine.DEFAULT_RETRY_DELAY, help="Initial retry delay in seconds")
    setup_parser.add_argument("--retry-backoff", type=float, default=delivery_engine.DEFAULT_RETRY_BACKOFF, help="Retry delay multiplier")
    setup_parser.add_argument("--resume-timeout", type=float, default=delivery_engine.DEFAULT_RESUME_TIMEOUT, help="Maximum seconds for one Codex resume before retrying")
    setup_parser.add_argument("--restart-sec", type=float, default=5.0, help="Service restart delay in seconds")
    setup_parser.add_argument("--exec-start", help="Path to agent-atc executable")
    setup_parser.add_argument("--codex-bin", help="Path to codex executable used by the daemon")
    setup_parser.add_argument("--claude-bin", help="Path to claude executable used by the daemon")
    setup_parser.add_argument("--pi-bin", help="Path to the PI Agent executable used by the daemon")
    setup_parser.add_argument("--path", help="PATH environment for the daemon service")
    add_proxy_environment_flags(setup_parser)
    setup_parser.add_argument("--force", action="store_true", help="Overwrite an existing skill and service file")
    setup_parser.add_argument("--keep-skill", action="store_true",
                              help="Keep existing skill files during coordinator setup, even with --force; install if missing")
    desktop_setup = setup_parser.add_mutually_exclusive_group()
    desktop_setup.add_argument("--desktop-app", help="Desktop .app on Mac or .exe on Windows for the generated launcher")
    desktop_setup.add_argument("--no-desktop-launcher", action="store_true",
                               help="Skip Mac/Windows Desktop launcher generation (default: prepare without launching)")
    setup_parser.add_argument("--desktop-launch-mode", choices=("direct", "package-context"),
                              help="Windows Desktop launch route (default: preserve selection, or direct)")
    setup_parser.add_argument("--enable", action="store_true", help="Enable the service for this user")
    setup_parser.add_argument("--now", action="store_true", help="Start the service or request a safe reload")

    wait_parser = sub.add_parser(
        "wait",
        help="Claude Code only: block until this session's callback is ready, then print it (run in the background)",
    )
    wait_parser.add_argument("--queue-dir", help="Wakeup queue directory")
    wait_parser.add_argument("--task", help="Only wait for this managed task's callback")
    wait_parser.add_argument("--session", help="Claude Code session id (default: $CLAUDE_CODE_SESSION_ID)")
    wait_parser.add_argument("--timeout", type=float, help="Give up after this many seconds (exit 3)")
    wait_parser.add_argument("--poll-interval", type=float, default=1.0, help=argparse.SUPPRESS)

    claude_parser = sub.add_parser(
        "claude",
        help="Start interactive Claude Code with the ATC channel (research preview): callbacks are pushed "
             "into the open session; pass Claude's own arguments after --",
    )
    claude_parser.add_argument("--queue-dir", help="Wakeup queue directory")
    claude_parser.add_argument("claude_args", nargs=argparse.REMAINDER, help="Arguments for claude")
    channel_parser = sub.add_parser("_claude-channel", help=argparse.SUPPRESS)
    channel_parser.add_argument("--queue-dir", help=argparse.SUPPRESS)
    channel_parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    channel_parser.add_argument("--poll-interval", type=float, default=1.0, help=argparse.SUPPRESS)

    ack_parser = sub.add_parser("ack", help="Mark a daemon callback as successfully received")
    ack_parser.add_argument("--queue-dir", help="Wakeup queue directory")
    ack_parser.add_argument("--id", required=True, help="Callback request id to acknowledge")
    ack_parser.add_argument("--message", help="Optional acknowledgement note")

    goal_parser = sub.add_parser("goal", help="Manage persistent long-running goal acknowledgements")
    goal_sub = goal_parser.add_subparsers(dest="goal_mode", required=True)
    goal_start_parser = goal_sub.add_parser("start", help="Create an active goal")
    goal_start_parser.add_argument("--id", help="Optional stable goal id")
    goal_start_parser.add_argument("--queue-dir", help="Queue root that owns the goal")
    goal_start_parser.add_argument("--session", required=True, help="Bound agent session")
    goal_start_parser.add_argument(
        "--agent",
        choices=AGENT_NAMES,
        help="Agent that owns the session (default: auto-detect from the environment)",
    )
    goal_start_parser.add_argument("--cwd", default=os.getcwd())
    goal_start_parser.add_argument("--task", required=True)
    goal_start_parser.add_argument(
        "--plan-file",
        required=True,
        help="Mutable UTF-8 YAML goal path that governs progress and completion",
    )
    goal_start_parser.add_argument("--idle-seconds", type=float, default=goals.DEFAULT_GOAL_IDLE_SECONDS)
    goal_check_parser = goal_sub.add_parser("check", help="Inspect and record the current YAML goal path")
    goal_check_parser.add_argument("--queue-dir", help="Queue root that owns the goal")
    goal_check_parser.add_argument("--id", required=True)
    goal_set_plan_parser = goal_sub.add_parser(
        "set-plan", help="Attach or replace the mutable YAML plan for an existing goal"
    )
    goal_set_plan_parser.add_argument("--queue-dir", help="Queue root that owns the goal")
    goal_set_plan_parser.add_argument("--id", required=True)
    goal_set_plan_parser.add_argument("--plan-file", required=True)
    goal_ack_parser = goal_sub.add_parser("ack", help="Acknowledge completion or blocked conditions")
    goal_ack_parser.add_argument("--queue-dir", help="Queue root that owns the goal")
    goal_ack_parser.add_argument("--id", required=True)
    goal_ack_parser.add_argument("--state", choices=["completed", "blocked_conditions"], required=True)
    goal_ack_parser.add_argument(
        "--plan-sha256",
        help="Digest printed by the latest goal check; required for completed",
    )
    goal_ack_parser.add_argument("--message")
    goal_ack_parser.add_argument("--condition")
    goal_ack_parser.add_argument("--email-to")
    goal_ack_parser.add_argument("--email-after", type=float, default=goals.DEFAULT_BLOCKED_EMAIL_SECONDS)
    goal_resume_parser = goal_sub.add_parser("resume", help="Explicitly resume a blocked goal")
    goal_resume_parser.add_argument("--queue-dir", help="Queue root that owns the goal")
    goal_resume_parser.add_argument("--id", required=True)

    cancel_parser = sub.add_parser("cancel", help="Cancel queued callbacks before they retry or complete")
    cancel_parser.add_argument("--queue-dir", help="Wakeup queue directory")
    cancel_target = cancel_parser.add_mutually_exclusive_group(required=True)
    cancel_target.add_argument("--id", help="Callback request id to cancel")
    cancel_target.add_argument("--all", action="store_true", help="Cancel all active, pending, and running callbacks")
    cancel_parser.add_argument("--message", help="Optional cancellation note")

    status_parser = sub.add_parser("status", help="List queued callbacks that still need handling")
    status_parser.add_argument("--queue-dir", help="Wakeup queue directory")
    status_parser.add_argument(
        "--state",
        action="append",
        choices=[layout.ACTIVE_STATE, "pending", "running", "failed"],
        help="Queue state to include; repeat as needed (default: pending and running)",
    )
    status_parser.add_argument("--limit", type=int, default=5, help="Maximum items to print")
    status_parser.add_argument("--quiet-empty", action="store_true", help="Print nothing when no items exist")
    status_parser.add_argument("--shell-hook", action="store_true", help="Use compact terminal-startup output")

    shell_hook_parser = sub.add_parser("install-shell-hook", help="Install an idempotent pending-status check in Bash startup")
    shell_hook_parser.add_argument("--rc-file", help="Bash rc file (default: ~/.bashrc)")
    shell_hook_parser.add_argument("--command", help="Executable path embedded in the hook")

    args = parser.parse_args()
    if args.mode == "desktop":
        from .harness.codex.desktop import launcher as desktop
        return desktop.command(args)
    if args.mode == "retry":
        return retry_callback(args)
    if args.mode == "doctor":
        return diagnostics.doctor(args)
    if args.mode == "done":
        return invoke_with_diagnostics(args, done)
    if args.mode == "run":
        if args.wrapped_command and args.wrapped_command[0] == "--":
            args.wrapped_command = args.wrapped_command[1:]
        return invoke_with_diagnostics(args, run)
    if args.mode == "agent":
        return invoke_with_diagnostics(args, agent)
    if args.mode == "_screen-worker":
        return task_worker.run_screen_worker(args)
    if args.mode == "_task-worker":
        return task_worker.run_task_worker(args)
    if args.mode == "_delivery-worker":
        return delivery_engine.delivery_worker_main()
    if args.mode == "_windows-coordinator":
        from .install import windows_service
        return windows_service.run_coordinator(Path(args.config_file))
    if args.mode == "daemon":
        return tower.daemon(args)
    if args.mode == "install-systemd":
        return services.install_systemd(args)
    if args.mode == "install-launchd":
        return services.install_launchd(args)
    if args.mode == "install-windows-task":
        return services.install_windows_task(args)
    if args.mode == "uninstall-windows-task":
        from .install import windows_service
        return windows_service.uninstall(args)
    if args.mode == "install-skill":
        return skill_install.install_skill(args)
    if args.mode in ("install-pi-extension", "pi"):
        try:
            return pi_callback.install_extension(args) if args.mode == "install-pi-extension" else pi_callback.managed_pi(args)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"atc: {exc}", file=sys.stderr)
            return 2
    if args.mode == "template":
        return template_command(args)
    if args.mode == "setup":
        return services.setup(args)
    if args.mode == "prompt-policy":
        return reminder_policy.callback_prompt_policy(args)
    if args.mode == "ack":
        return ack(args)
    if args.mode == "wait":
        return claude_code.wait(args)
    if args.mode == "claude":
        from .harness.claude import channel as claude_channel
        return claude_channel.launch(args)
    if args.mode == "_claude-channel":
        from .harness.claude import channel as claude_channel
        return claude_channel.serve(args)
    if args.mode == "goal":
        if args.goal_mode == "start":
            return goal_start(args)
        if args.goal_mode == "check":
            return goal_check(args)
        if args.goal_mode == "set-plan":
            return goal_set_plan(args)
        if args.goal_mode == "ack":
            return goal_ack(args)
        return goal_resume(args)
    if args.mode == "cancel":
        return cancel(args)
    if args.mode == "status":
        return status(args)
    if args.mode == "install-shell-hook":
        return skill_install.install_shell_hook(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
