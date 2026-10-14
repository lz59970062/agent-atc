"""Queue record fixtures, built in one place.

Tests describe a callback by its id, working directory, target and prompt
plus whatever extra fields the case needs. When the record format changes,
only these builders change.
"""
from __future__ import annotations

from agent_atc.core import capabilities, records


def callback_record(request_id: str, cwd: str, *, target: dict[str, str], prompt: str, **fields: object) -> dict[str, object]:
    record: dict[str, object] = {"schema": records.CALLBACK_SCHEMA, "id": request_id, "cwd": cwd,
                                 "target": target, "prompt": prompt, "callback_mode": "cli"}
    record.update(fields)
    record.setdefault("requires", capabilities.required_for(record))
    return record


def agent_prompt(prompt: str, task: dict[str, object]) -> str:
    """What a child agent receives: the user's prompt verbatim, then the report request."""
    from agent_atc.core import results

    if not task.get("report_path"):
        return prompt
    return prompt + "\n\n" + results.REPORT_INSTRUCTIONS.format(path=task["report_path"])


def result_record(task: dict[str, object], *, exit_code: int, ended_at: float, started_at: float | None = None,
                  **fields: object) -> dict[str, object]:
    """An atc.result/1 record for a task whose process ATC saw exit."""
    from agent_atc.core import ids, results

    record = results.exited(task, exit_code=exit_code, started_at=started_at, ended_at=ended_at,
                            attempt=ids.attempt_id(str(task["id"]), 1), log="attempt-1.log")
    record.update(fields)
    return record
