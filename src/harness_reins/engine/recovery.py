"""Recover callbacks interrupted mid-submission and cancel queued ones; never replay work."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from ..store import layout
from ..owners import host
from ..store import records as queue_store
from . import delivery as delivery_engine
from . import dispatch


def active_prompt(args: argparse.Namespace) -> str:
    command = args.command or " ".join(args.wrapped_command)
    return "\n".join(
        [
            "[reins-active]",
            f"Task: {args.task}",
            f"Working directory: {args.cwd}",
            f"Command: {command}",
            "State: armed before task launch; this record is not dispatchable.",
        ]
    )


def make_active_request(args: argparse.Namespace, started_at: float) -> dict[str, object]:
    request = dispatch.make_request(args, active_prompt(args))
    request.update(
        {
            "queue_dir": str(layout.queue_dir(args)),
            "lifecycle_state": layout.ACTIVE_STATE,
            "task": args.task,
            "command": args.command or " ".join(args.wrapped_command),
            "started_at": started_at,
            "wrapper_pid": os.getpid(),
            "boot_id": host.current_boot_id(),
            "launch_phase": "prelaunch",
        }
    )
    return request


def discard_unlaunched_active(root: Path, request_id: str) -> None:
    path = layout.request_path(root, layout.ACTIVE_STATE, request_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        print(f"reins: warning: could not remove unlaunched callback {request_id}: {exc}", file=sys.stderr)
        return
    try:
        queue_store.fsync_directory(path.parent)
    except OSError as exc:
        print(
            f"reins: warning: removed unlaunched active callback {request_id} "
            f"but could not sync its directory: {exc}",
            file=sys.stderr,
        )


def best_effort_disarm(
    root: Path,
    request_id: str,
    lock: tuple[object, Path] | None,
) -> None:
    try:
        discard_unlaunched_active(root, request_id)
    except Exception as exc:
        print(f"reins: warning: pre-arm record cleanup failed: {exc}", file=sys.stderr)
    try:
        queue_store.release_owner_lock(lock, remove=True)
    except Exception as exc:
        print(f"reins: warning: pre-arm lock cleanup failed: {exc}", file=sys.stderr)


def transition_active_to_pending(root: Path, request: dict[str, object], prompt: str) -> int:
    request_id = str(request["id"])
    source = layout.request_path(root, layout.ACTIVE_STATE, request_id)
    if queue_store.is_canceled(root, request_id):
        queue_store.remove_live_request_copies(root, request_id)
        print(f"reins: callback {request_id} was canceled; suppressing finalization", file=sys.stderr)
        return 0
    if not source.exists():
        if queue_store.request_already_transitioned(root, request_id):
            return 0
        print(
            f"reins: warning: active callback {request_id} disappeared before finalization",
            file=sys.stderr,
        )
        return 1
    dispatch.prepare_request_for_queue(root, request, prompt)
    if queue_store.is_canceled(root, request_id):
        queue_store.remove_live_request_copies(root, request_id)
        return 0
    queue_store.write_request(source, request)
    if queue_store.is_canceled(root, request_id):
        queue_store.remove_live_request_copies(root, request_id)
        return 0
    try:
        queue_store.move_request(source, root / "pending")
    except FileNotFoundError:
        if queue_store.is_canceled(root, request_id) or queue_store.request_already_transitioned(root, request_id):
            if queue_store.is_canceled(root, request_id):
                queue_store.remove_live_request_copies(root, request_id)
            return 0
        raise
    if queue_store.is_canceled(root, request_id):
        queue_store.remove_live_request_copies(root, request_id)
        return 0
    print(f"reins: queued callback {request_id} in {root}", file=sys.stderr)
    return 0


def recovery_prompt(request: dict[str, object], now: float) -> str:
    started_at = request.get("started_at")
    duration = now - float(started_at) if isinstance(started_at, (int, float)) else None
    recovered_args = argparse.Namespace(
        task=str(request.get("task") or dispatch.request_task(request)),
        cwd=str(request["cwd"]),
        command=str(request.get("command") or ""),
        exit_code=None,
        agent=dispatch.request_agent(request),
        message=(
            "The long-task wrapper disappeared before recording task completion. "
            "The task outcome and exit status are unknown, and the wrapped child may still be running. "
            "Inspect process state and artifacts before retrying or launching the next stage."
        ),
    )
    return dispatch.build_prompt(recovered_args, duration)


def recover_active(root: Path) -> int:
    queue_store.ensure_daemon_dirs(root)
    recovered = 0
    for path in sorted((root / layout.ACTIVE_STATE).glob("*.json")):
        request_id = path.stem
        lock = queue_store.acquire_owner_lock(root, request_id, blocking=False)
        if lock is None:
            continue
        remove_lock = False
        try:
            if not path.exists():
                continue
            if queue_store.is_canceled(root, request_id):
                queue_store.remove_live_request_copies(root, request_id)
                remove_lock = True
                continue
            request = dispatch.load_request(path)
            if request.get("launch_phase") == "prelaunch":
                discard_unlaunched_active(root, request_id)
                remove_lock = True
                print(
                    f"reins: discarded callback {request_id}; wrapped task was not launch-committed",
                    file=sys.stderr,
                )
                continue
            if request.get("outcome") == "completed" and isinstance(request.get("exit_code"), int):
                queue_store.move_request(path, root / "pending")
                recovered += 1
                remove_lock = True
                print(
                    f"reins: recovered completed callback {request_id} with its recorded exit code",
                    file=sys.stderr,
                )
                continue
            request["lifecycle_state"] = "recovered_unknown"
            request["recovered_at"] = time.time()
            request["recovery_reason"] = "wrapper_owner_lock_released_before_completion"
            request["outcome"] = "unknown"
            prompt = recovery_prompt(request, float(request["recovered_at"]))
            if transition_active_to_pending(root, request, prompt) == 0:
                recovered += 1
                remove_lock = True
                print(
                    f"reins: recovered orphaned active callback {request_id} with unknown outcome",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(
                f"reins: warning: failed to recover active callback {request_id}: {exc}",
                file=sys.stderr,
            )
        finally:
            queue_store.release_owner_lock(lock, remove=remove_lock)
    return recovered


def cancel_one(root: Path, request_id: str, message: str | None = None) -> bool:
    queue_store.ensure_daemon_dirs(root)
    canceled = layout.request_path(root, "canceled", request_id)
    if canceled.exists():
        queue_store.remove_live_request_copies(root, request_id)
        print(f"reins: callback {request_id} is already canceled in {root}", file=sys.stderr)
        return True

    for state in (layout.ACTIVE_STATE, "pending", "running", "failed"):
        source = layout.request_path(root, state, request_id)
        if not source.exists():
            continue
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data.update(
            {
                "id": request_id,
                "canceled_at": time.time(),
                "canceled_from": state,
            }
        )
        if message:
            data["cancel_message"] = message
        # Publish the tombstone first. The wrapper finalizer and daemon both
        # honor it, so cancellation wins even if a state transition races us.
        queue_store.write_request(canceled, data)
        queue_store.remove_live_request_copies(root, request_id)
        try:
            delivery_engine.release_retained_target_lease(root, data)
        except (OSError, RuntimeError) as exc:
            print(
                f"reins: warning: could not release retained target lease for "
                f"{request_id}: {exc}",
                file=sys.stderr,
            )
        print(f"reins: canceled callback {request_id} from {state} in {root}", file=sys.stderr)
        return True

    print(f"reins: warning: active callback {request_id} not found in {root}", file=sys.stderr)
    return False


def cancel_all(root: Path, message: str | None = None) -> int:
    queue_store.ensure_daemon_dirs(root)
    request_ids: set[str] = set()
    for state in (layout.ACTIVE_STATE, "pending", "running", "failed"):
        request_ids.update(path.stem for path in (root / state).glob("*.json"))
    canceled = 0
    for request_id in sorted(request_ids):
        if cancel_one(root, request_id, message):
            canceled += 1
    print(f"reins: canceled {canceled} active callback(s) in {root}", file=sys.stderr)
    return canceled
