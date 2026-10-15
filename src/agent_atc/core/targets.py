"""Addresses: who submitted the work (origin) and who receives the callback (target).

v1 implements one target kind, an explicitly bound session, which must be the
origin's own session. The record's ``agent`` and ``cwd`` fields give the
harness and directory, so a target names a harness only once it may differ
from the origin's (``--notify``). Reserved kinds: ``profile`` (an expert
agent), ``endpoint`` (an SDK application) and ``none`` (store only).
"""
from __future__ import annotations

from typing import Mapping


def session(session_id: str) -> dict[str, str]:
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("a session target needs a non-empty session id")
    return {"kind": "session", "session": session_id}


def session_of(target: object) -> str | None:
    """The bound session of a session target, or None."""
    if isinstance(target, Mapping) and target.get("kind") == "session":
        value = target.get("session")
        if isinstance(value, str) and value:
            return value
    return None


def origin(harness: str, session_id: str, cwd: str, binding: str) -> dict[str, str]:
    """Who submitted the work; recorded, never used to route."""
    return {"harness": harness, "session": session_id, "cwd": cwd, "binding": binding}


def binding_of(record: Mapping[str, object], default: str = "unknown") -> str:
    origin_value = record.get("origin")
    if isinstance(origin_value, Mapping) and isinstance(origin_value.get("binding"), str):
        return str(origin_value["binding"])
    return default
