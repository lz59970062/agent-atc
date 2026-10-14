"""ATC identifiers: one root per piece of work, kind suffixes for what belongs to it.

A task root is eight hex digits; a goal root is ``g-<goal id>``. The N-th
callback of a root is ``<root>-c<N>`` and its N-th launch attempt is
``<root>-a<N>``. A suffix names what something is and its ordinal, never its
state: a callback keeps its ID from queued to acknowledged, and retrying a
delivery never creates a new callback.
"""
from __future__ import annotations

import re
import secrets
from typing import Callable

SAFE = re.compile(r"[A-Za-z0-9_-]+")
GOAL_PREFIX = "g-"
_SUFFIX = re.compile(r"^(?P<root>.+)-(?P<kind>[ac])(?P<sequence>[1-9][0-9]*)$")


def new_root(taken: Callable[[str], bool] = lambda root: False) -> str:
    """A fresh task root; ``taken`` reports roots already in use."""
    while True:
        root = secrets.token_hex(4)
        if not taken(root):
            return root


def new_goal_id() -> str:
    return secrets.token_hex(4)


def goal_root(goal_id: str) -> str:
    return f"{GOAL_PREFIX}{goal_id}"


def _child(root: str, kind: str, sequence: int) -> str:
    if not SAFE.fullmatch(root):
        raise ValueError("root must contain only letters, numbers, '-' or '_'")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError("sequence must be a positive integer")
    return f"{root}-{kind}{sequence}"


def callback_id(root: str, sequence: int = 1) -> str:
    return _child(root, "c", sequence)


def attempt_id(root: str, sequence: int) -> str:
    return _child(root, "a", sequence)


def root_of(identifier: str) -> str:
    """The root an ID belongs to; a root is its own root."""
    match = _SUFFIX.match(identifier)
    return match["root"] if match else identifier
