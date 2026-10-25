"""The submitting session's environment, kept for resuming that session headlessly.

A task already runs with the environment of the session that submitted it. A
headless resume (``claude -p --resume``, ``codex exec resume``) used to run
with the coordinator's environment instead, which on a systemd or launchd
service lacks what the session had: model endpoints and keys set as
variables, proxies, PATH entries, launcher settings. Reins therefore keeps a
private copy of the session environment per callback and resumes with it,
on top of the coordinator's own environment. Markers that tie a process to
the running parent session or to a service are left out, so the resumed CLI
does not believe it is nested. The copy is removed once the callback is
acknowledged, done or canceled.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from ..harness import AGENTS
from ..store import layout
from ..store import records as queue_store

DIRECTORY_NAME = "environments"
# Service, screen and forwarding markers describe the process that captured the
# environment, not the session; Claude's entrypoint and Codex's originator would
# make a headless resume pose as the desktop app.
EXCLUDED = (
    "INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID", "STY", "WINDOW", "XPC_SERVICE_NAME", "XPC_FLAGS",
    "CLAUDE_CODE_ENTRYPOINT", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "REINS_FORWARDED",
    "CODEX_LONG_TASK_DELIVERY_LOCK_FDS", "CODEX_LONG_TASK_DELIVERY_LOCK_FD",
)
# The coordinator's own queue and lock locations always win over the session's.
COORDINATOR_OWNED = (layout.REINS_HOME_ENV, layout.TARGET_LOCK_DIR_ENV)


def path(root: Path, callback_id: str) -> Path:
    return root / DIRECTORY_NAME / f"{callback_id}.json"


def session_environment(environment: Mapping[str, str]) -> dict[str, str]:
    kept = AGENTS.child_environment(environment)
    for name in EXCLUDED:
        kept.pop(name, None)
    return kept


def capture(root: Path, callback_id: str, environment: Mapping[str, str] | None = None) -> None:
    """Keep the session environment for ``callback_id``; failure only loses the improvement."""
    try:
        directory = root / DIRECTORY_NAME
        queue_store.private_directory(directory)
        values = session_environment(os.environ if environment is None else environment)
        queue_store.write_private_text(path(root, callback_id), json.dumps(values, ensure_ascii=False))
    except OSError:
        pass


def load(root: Path, callback_id: str) -> dict[str, str] | None:
    try:
        data = json.loads(path(root, callback_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(name): str(value) for name, value in data.items() if isinstance(value, str)}


def merged(coordinator: Mapping[str, str], session: Mapping[str, str] | None) -> dict[str, str]:
    """The resume environment: the coordinator's, overlaid by the session's, without session markers.

    A coordinator started from inside an agent session (a standalone daemon in a
    container) carries that session's markers too; neither side may pass them on.
    """
    environment = dict(coordinator)
    if session:
        environment.update(session)
        for name in COORDINATOR_OWNED:
            if name in coordinator:
                environment[name] = coordinator[name]
            else:
                environment.pop(name, None)
    return session_environment(environment)


def discard(root: Path, callback_id: str) -> None:
    try:
        path(root, callback_id).unlink()
    except OSError:
        pass


def prune(root: Path) -> None:
    """Remove copies whose callback no longer needs a resume; failed callbacks keep theirs for ``reins retry``."""
    directory = root / DIRECTORY_NAME
    try:
        entries = list(directory.glob("*.json"))
    except OSError:
        return
    for entry in entries:
        callback_id = entry.stem
        if (layout.ack_path(root, callback_id).exists()
                or layout.request_path(root, "done", callback_id).exists()
                or layout.request_path(root, "canceled", callback_id).exists()):
            discard(root, callback_id)
