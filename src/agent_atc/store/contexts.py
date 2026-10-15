"""Context records: which nodes form one chain, and where each chain came from.

A context is a linear chain of nodes; its ID is its first node's task ID.
Records live in ${ATC_HOME}/contexts/<id>.json. Node details (name, lineage,
harness session) stay in each task record and result, so this record only
orders the chain and points at its parent node when it was forked.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from ..core import nodes
from . import layout
from . import records as queue_store


def directory() -> Path:
    return layout.atc_home() / "contexts"


def path(context_id: str) -> Path:
    if not context_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in context_id):
        raise ValueError("context id must contain only letters, numbers, '-' or '_'")
    return directory() / f"{context_id}.json"


def load(context_id: str) -> dict[str, object]:
    record = json.loads(path(context_id).read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema") != nodes.CONTEXT_SCHEMA or record.get("id") != context_id:
        raise ValueError(f"invalid context record {context_id}")
    return record


def save(record: dict[str, object]) -> None:
    queue_store.private_directory(directory())
    queue_store.write_request(path(str(record["id"])), record)


def all_contexts() -> list[dict[str, object]]:
    found = []
    for item in sorted(directory().glob("*.json")):
        try:
            found.append(load(item.stem))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda record: float(record.get("created_at") or 0))  # oldest first


def new(first_node: str, *, harness: str, cwd: str, model: str | None, expert: str | None,
        parent: dict[str, str] | None) -> dict[str, object]:
    return {"schema": nodes.CONTEXT_SCHEMA, "id": first_node, "harness": harness, "cwd": cwd, "model": model,
            "expert": expert, "nodes": [first_node], "parent": parent, "archived": False, "created_at": time.time()}


def lock_id() -> str:
    return "contexts"  # one lock serializes name allocation and chain changes
