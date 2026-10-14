"""Task results: what ATC observed, kept apart from what a child agent claims.

``observed`` is written from ATC's own evidence (the process and its owner)
and can be trusted. ``report`` is whatever a child agent wrote to the file
named by ``ATC_REPORT_FILE``; it is validated and bounded but never verified.
A missing or broken report never fails a task, and a report that says
"completed" never makes one succeed.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Mapping

RESULT_SCHEMA = "atc.result/1"
REPORT_ENV = "ATC_REPORT_FILE"
REPORT_FILE_NAME = "report.json"
REPORT_STATUSES = ("completed", "partial", "input_required", "failed")
SUMMARY_LIMIT = 2000
ITEM_LIMIT = 50
TEXT_LIMIT = 1000

REPORT_INSTRUCTIONS = """\
Before you finish, write a JSON report to the file named by the ATC_REPORT_FILE
environment variable ({path}). The agent that delegated this task reads it first:
{{"status": "completed" | "partial" | "input_required" | "failed",
 "summary": "what you did and what you found, in a few sentences",
 "artifacts": [{{"path": "file you created or changed", "description": "what it is"}}],
 "questions": ["anything you need answered"],
 "next_steps": ["what should happen next"]}}
Write it even if you could not finish. It does not replace your final answer."""


def timestamp(seconds: float | None) -> str | None:
    """RFC 3339 UTC with milliseconds, or None for missing evidence."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        return None
    moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def seconds(value: object) -> float | None:
    """Parse a timestamp written by ``timestamp`` back to epoch seconds."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).timestamp()


def _bounded(value: object, limit: int, field: str, problems: list[str]) -> str | None:
    if not isinstance(value, str):
        problems.append(f"{field} is not a string")
        return None
    if len(value) > limit:
        problems.append(f"{field} truncated to {limit} characters")
        return value[:limit]
    return value


def parse_report(text: str | None) -> tuple[dict[str, object] | None, str | None]:
    """Validate a child's report. Returns (report, problem); either may be None."""
    if text is None:
        return None, "no report was written"
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, f"report is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "report is not a JSON object"
    status = data.get("status")
    if status not in REPORT_STATUSES:
        return None, f"report status must be one of {', '.join(REPORT_STATUSES)}"
    problems: list[str] = []
    report: dict[str, object] = {"status": status}
    summary = _bounded(data.get("summary", ""), SUMMARY_LIMIT, "summary", problems)
    report["summary"] = summary if summary is not None else ""
    artifacts = data.get("artifacts", [])
    if not isinstance(artifacts, list):
        problems.append("artifacts is not a list")
        artifacts = []
    if len(artifacts) > ITEM_LIMIT:
        problems.append(f"artifacts truncated to {ITEM_LIMIT} items")
        artifacts = artifacts[:ITEM_LIMIT]
    kept = []
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            problems.append("an artifact without a path was dropped")
            continue
        description = _bounded(item.get("description", ""), TEXT_LIMIT, "artifact description", problems)
        kept.append({"path": item["path"][:TEXT_LIMIT], "description": description or ""})
    report["artifacts"] = kept
    for field in ("questions", "next_steps"):
        values = data.get(field, [])
        if not isinstance(values, list):
            problems.append(f"{field} is not a list")
            values = []
        if len(values) > ITEM_LIMIT:
            problems.append(f"{field} truncated to {ITEM_LIMIT} items")
            values = values[:ITEM_LIMIT]
        report[field] = [v for v in (_bounded(value, TEXT_LIMIT, field, problems) for value in values) if v is not None]
    return report, "; ".join(dict.fromkeys(problems)) or None


def exited(task: Mapping[str, object], *, exit_code: int, started_at: float | None, ended_at: float,
           attempt: str, log: str | None, text_result: str | None = None,
           report: dict[str, object] | None = None, report_error: str | None = None) -> dict[str, object]:
    """The result of a task whose process ATC saw exit."""
    duration = ended_at - started_at if isinstance(started_at, (int, float)) else None
    agent = task.get("task_kind") == "agent"
    return {
        "schema": RESULT_SCHEMA,
        "root": task["id"],
        "kind": "agent" if agent else "command",
        "observed": {
            "state": "exited",
            "exit_code": exit_code,
            "attempt": attempt,
            "started_at": timestamp(started_at),
            "ended_at": timestamp(ended_at),
            "duration_seconds": duration,
            "backend": task.get("execution_backend"),
            "recovery_reason": None,
            "log": log,
        },
        "worker": {
            "harness": task.get("agent_worker"),
            "model": task.get("child_model"),
            "profile": task.get("agent_template"),
            "session": None,
            "checkpoint": None,
        } if agent else None,
        "lineage": None,
        "text_result": text_result,
        "report": report,
        "report_source": "report-file" if report is not None else None,
        "report_error": report_error,
    }


def observed_exit(result: object, task_id: str) -> int:
    """The exit code from a result that belongs to ``task_id``, or ValueError."""
    if not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA or result.get("root") != task_id:
        raise ValueError("invalid managed result identity")
    observed = result.get("observed")
    if not isinstance(observed, dict) or observed.get("state") != "exited" or type(observed.get("exit_code")) is not int:
        raise ValueError("managed result does not record an observed exit")
    return observed["exit_code"]
