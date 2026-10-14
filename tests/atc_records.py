"""Queue record fixtures, built in one place.

Tests describe a callback by its id, working directory, target and prompt
plus whatever extra fields the case needs. When the record format changes,
only these builders change.
"""
from __future__ import annotations


def callback_record(request_id: str, cwd: str, *, target: dict[str, str], prompt: str, **fields: object) -> dict[str, object]:
    record: dict[str, object] = {"version": fields.pop("version", 1), "id": request_id, "cwd": cwd,
                                 "target": target, "prompt": prompt}
    record.update(fields)
    return record
