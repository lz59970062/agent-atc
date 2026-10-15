"""atc context: see and tidy the chains of named agent nodes.

Views show summaries only: names, states, observed results and the child's
report summary. A raw harness session can be large enough to fill the reader's
own context, so ATC gives its location and never prints it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from ..engine import contexts as contexts_engine
from ..store import contexts, layout
from ..store import records as queue_store

SUMMARY_LIMIT = 300
PROMPT_LIMIT = 100


def _first_line(path: object, limit: int) -> str:
    try:
        line = Path(str(path)).read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, IndexError, UnicodeError):
        return ""
    return line if len(line) <= limit else line[: limit - 3] + "..."


def _node_view(root: Path, task: dict[str, object]) -> dict[str, object]:
    result = contexts_engine.node_result(root, task) or {}
    observed = result.get("observed") or {}
    report = result.get("report") or None
    if observed.get("state") == "exited":
        state = f"exited {observed.get('exit_code')}"
    else:
        state = str(observed.get("state") or task.get("state") or "unknown")
    summary = (report or {}).get("summary") or ""
    return {
        "id": task["id"],
        "name": task["node"]["name"],
        "mode": task["node"]["lineage"]["mode"],
        "harness": task.get("agent_worker"),
        "model": task.get("child_model"),
        "state": state,
        "task": _first_line(task.get("agent_prompt_path"), PROMPT_LIMIT),
        "report": (report or {}).get("status"),
        "report_summary": summary if len(summary) <= SUMMARY_LIMIT else summary[: SUMMARY_LIMIT - 3] + "...",
        "result": str(layout.managed_result_path(root, str(task["id"]))) if result else None,
        "session": task["node"].get("session"),
    }


def _views(root: Path, include_archived: bool) -> list[dict[str, object]]:
    by_id = {task["id"]: task for task in contexts_engine.node_tasks(root)}
    names = {task_id: task["node"]["name"] for task_id, task in by_id.items()}
    views = []
    for context in contexts.all_contexts():
        if context.get("archived") and not include_archived:
            continue
        parent = context.get("parent")
        views.append({
            "context": context["id"],
            "harness": context.get("harness"),
            "model": context.get("model"),
            "archived": bool(context.get("archived")),
            "forked_from": ({"node": parent["node"], "name": names.get(parent["node"])} if parent else None),
            "nodes": [_node_view(root, by_id[node]) for node in context["nodes"] if node in by_id],
        })
    return views


def list_contexts(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args).expanduser().absolute()
    views = _views(root, getattr(args, "all", False))
    if getattr(args, "json", False):
        print(json.dumps(views, ensure_ascii=False, indent=2))
        return 0
    if not views:
        print("No agent contexts yet. Start one with: atc agent codex|claude|pi --name <name> -- <task>")
        return 0
    for view in views:
        origin = f" · forked from {view['forked_from']['name']} ({view['forked_from']['node']})" if view["forked_from"] else ""
        model = f" · {view['model']}" if view["model"] else ""
        print(f"context {view['context']} · {view['harness']}{model}{origin}{' · archived' if view['archived'] else ''}")
        width = max(len(node["name"]) for node in view["nodes"]) if view["nodes"] else 0
        for node in view["nodes"]:
            report = f"  report: {node['report']} (claimed)" if node["report"] else ""
            print(f"  {node['id']}  {node['name']:<{width}}  {node['state']}{report}")
    return 0


def show_context(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args).expanduser().absolute()
    try:
        task = contexts_engine.resolve(root, args.node)
    except ValueError as exc:
        print(f"atc: {exc}", file=sys.stderr)
        return 2
    context_id = task["node"]["context"]
    view = next((v for v in _views(root, True) if v["context"] == context_id), None)
    if view is None:
        print(f"atc: context {context_id} has no record", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(view, ensure_ascii=False, indent=2))
        return 0
    origin = f", forked from {view['forked_from']['name']}" if view["forked_from"] else ""
    print(f"context {view['context']} ({view['harness']}{origin})")
    for node in view["nodes"]:
        print(f"\n{node['name']}  [{node['id']}, {node['mode']}]")
        print(f"  task: {node['task']}")
        print(f"  result: {node['state']}")
        if node["report"]:
            print(f"  report: {node['report']} (claimed, unverified): {node['report_summary']}")
        if node["result"]:
            print(f"  result file: {node['result']}")
        if node["session"]:
            print(f"  raw session: {node['session']} (not printed; read the report instead)")
    last = view["nodes"][-1]["name"] if view["nodes"] else args.node
    print(f"\nContinue: atc agent continue {last} --name <next> -- <task>")
    return 0


def archive_context(args: argparse.Namespace) -> int:
    root = layout.queue_dir(args).expanduser().absolute()
    queue_store.ensure_daemon_dirs(root)
    lock = queue_store.acquire_owner_lock(root, contexts.lock_id(), blocking=True)
    try:
        task = contexts_engine.resolve(root, args.node)
        record = contexts.load(str(task["node"]["context"]))
        record["archived"] = True
        contexts.save(record)
    except ValueError as exc:
        print(f"atc: {exc}", file=sys.stderr)
        return 2
    finally:
        queue_store.release_owner_lock(lock, remove=False)
    print(f"Archived context {record['id']}; its nodes can still be forked.")
    return 0
