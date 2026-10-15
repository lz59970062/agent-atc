"""Contexts and nodes: continue or fork a child agent by ATC's own names.

Every agent task is a node with its own harness session. ``continue`` appends
a node to the end of a chain; ``fork`` starts a new chain from any finished
node. Both fork the parent node's session, so a finished node never changes
and stays an exact checkpoint. People and agents address nodes by task ID or
lineage name; the harness session IDs stay inside ATC.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

from ..core import ids, nodes, results
from ..harness.claude import session as claude_session
from ..store import contexts, layout
from . import tasks as managed_tasks

SESSION_DIR_NAME = "session"
_CODEX_THREAD = re.compile(r'"thread_id"\s*:\s*"([^"]+)"')


def _tasks(root: Path) -> list[dict[str, object]]:
    found = []
    for path in sorted(layout.managed_tasks_root(root).glob("*/task.json")):
        try:
            found.append(managed_tasks.load_managed_task(path))
        except (OSError, ValueError):
            continue
    return found


def node_tasks(root: Path) -> list[dict[str, object]]:
    return [task for task in _tasks(root) if isinstance(task.get("node"), dict)]


def resolve(root: Path, ref: str) -> dict[str, object]:
    """The task record of the node named by task ID, a callback ID of that task, or full lineage name."""
    root_id = ids.root_of(ref)  # 1a2b3c4d-c1, as copied from a callback, names node 1a2b3c4d
    matches = [task for task in node_tasks(root) if task["id"] in (ref, root_id) or task["node"].get("name") == ref]
    if not matches:
        raise ValueError(f"no agent node named {ref!r}; see atc context list")
    return matches[0]


def node_result(root: Path, task: dict[str, object]) -> dict[str, object] | None:
    try:
        return results.validate(json.loads(layout.managed_result_path(root, str(task["id"])).read_text(encoding="utf-8")),
                                str(task["id"]))
    except (OSError, ValueError):
        return None


def session_of(root: Path, task: dict[str, object]) -> str | None:
    session = task["node"].get("session")
    if isinstance(session, str) and session:
        return session
    result = node_result(root, task) or {}
    worker = result.get("worker") or {}
    value = worker.get("session") if isinstance(worker, dict) else None
    return value if isinstance(value, str) and value else None


def plan(root: Path, *, mode: str, ref: str | None, name: str | None, task_title: str | None) -> dict[str, object]:
    """Check the rules for a new node and return what the submission needs."""
    own = nodes.segment(name, task_title)
    parent = None
    if mode in ("continue", "fork"):
        parent = resolve(root, str(ref))
        if parent.get("state") != "completed":
            raise ValueError(f"node {parent['node']['name']} has not finished; wait for its callback first")
        if session_of(root, parent) is None:
            raise ValueError(f"node {parent['node']['name']} kept no session to {mode} from")
        context = contexts.load(str(parent["node"]["context"]))
        if mode == "continue":
            if context.get("archived"):
                raise ValueError(f"context {context['id']} is archived")
            if context["nodes"][-1] != parent["id"]:
                raise ValueError(f"node {parent['node']['name']} is not the end of its chain; "
                                 f"use atc agent fork {parent['node']['name']} --name <new-name> to branch from it")
    full = nodes.full_name(parent["node"]["name"] if parent else None, own)
    if any(task["node"].get("name") == full for task in node_tasks(root)):
        raise ValueError(f"a node named {full} already exists; choose another --name")
    return {
        "mode": mode,
        "name": full,
        "parent": parent,
        "fork_from": session_of(root, parent) if parent else None,
        "context": parent["node"]["context"] if mode == "continue" else None,
    }


def node_record(plan_: dict[str, object], task_id: str, task_directory: Path, worker: str) -> dict[str, object]:
    """The node section of the new task record, once its task ID is reserved."""
    parent = plan_["parent"]
    return {
        "name": plan_["name"],
        "context": plan_["context"] or task_id,
        "lineage": {"from": parent["id"] if parent else None, "mode": plan_["mode"]},
        "fork_from": plan_["fork_from"],
        # Claude resumes a private copy of the parent's session, made just before the child runs.
        "fork_copy": str(uuid.uuid4()) if worker == "claude" and plan_["fork_from"] else None,
        "session": None,
        "planned_session": str(uuid.uuid4()) if worker == "claude" else None,
        "session_dir": str(task_directory / SESSION_DIR_NAME),
    }


def record(root: Path, task: dict[str, object]) -> None:
    """Add a submitted node to its context, creating the context for new and forked chains."""
    node = task["node"]
    lineage = node["lineage"]
    if lineage["mode"] == "continue":
        context = contexts.load(str(node["context"]))
        context["nodes"].append(task["id"])
    else:
        parent = None
        if lineage["mode"] == "fork":
            parent_task = resolve(root, str(lineage["from"]))
            parent = {"context": str(parent_task["node"]["context"]), "node": str(parent_task["id"])}
        context = contexts.new(str(task["id"]), harness=str(task["agent_worker"]), cwd=str(task["cwd"]),
                               model=task.get("child_model"), expert=task.get("expert"), parent=parent)
    contexts.save(context)


def prepare_fork(task: dict[str, object], environment: dict[str, str]) -> Path | None:
    """Make the session copy a Claude node forks from; the caller removes it once the child exits."""
    node = task["node"]
    if not node.get("fork_copy"):
        return None
    return claude_session.isolated_copy(str(node["fork_from"]), str(node["fork_copy"]), environment)


def captures_events(task: dict[str, object]) -> bool:
    """Whether the worker must read the child's stdout to learn its session (Codex --json)."""
    return isinstance(task.get("node"), dict) and task.get("agent_worker") == "codex"


def discover_session(task: dict[str, object], events: str | None = None) -> str | None:
    """The harness session this node's child left behind, after it ran."""
    node = task["node"]
    worker = task.get("agent_worker")
    if worker == "claude":
        return node.get("planned_session")
    if worker == "codex":
        for line in (events or "").splitlines():
            if '"thread.started"' in line:
                match = _CODEX_THREAD.search(line)
                if match:
                    return match.group(1)
        return None
    if worker == "pi":
        files = sorted(Path(str(node["session_dir"])).rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        return str(files[-1].resolve()) if files else None
    return None
