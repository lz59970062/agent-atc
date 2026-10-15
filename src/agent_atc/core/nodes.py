"""Node names: every agent task is a node, named by its lineage path.

A node's own segment comes from ``--name`` (or an explicit ``--task``). Its
full name is the parent node's full name plus that segment, so the name alone
says what the agent did before and after: ``research``, ``research/review``,
``research/plan``. Full names are unique.
"""
from __future__ import annotations

import re

SEGMENT_LIMIT = 32
_SEGMENT = re.compile(r"[^\s/]+")
CONTEXT_SCHEMA = "atc.context/1"
LINEAGE_MODES = ("new", "continue", "fork")


def segment(name: str | None, task: str | None = None) -> str:
    """The node's own name; ``task`` is used only when no name is given."""
    raw = name if name is not None else task
    if raw is None or not raw.strip():
        raise ValueError("name this agent task: --name <short-name> (for example --name review)")
    if name is None:
        raw = "-".join(raw.split()[:4])  # a short handle derived from the task title
    raw = raw.strip()
    if "/" in raw:
        raise ValueError("a node name cannot contain '/'; ATC adds the lineage path itself")
    if not _SEGMENT.fullmatch(raw):
        raise ValueError("a node name cannot contain spaces")
    if len(raw) > SEGMENT_LIMIT:
        if name is not None:
            raise ValueError(f"a node name is at most {SEGMENT_LIMIT} characters")
        raw = raw[:SEGMENT_LIMIT]
    return raw


def full_name(parent: str | None, own: str) -> str:
    return own if not parent else f"{parent}/{own}"
