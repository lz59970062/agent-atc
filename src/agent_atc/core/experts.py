"""Expert agents: named, portable presets for who does the work.

An expert fixes the harness, model, reasoning effort and instructions for a
kind of task, such as writing or code review. Instructions are prepended to
the task prompt on every harness, so one configuration behaves the same on
Codex, Claude Code and Pi and moves between machines unchanged. A continued
or forked node already carries them in its session and does not repeat them.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping

EXPERTS_SCHEMA = "atc.experts/1"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
FIELDS = ("description", "harness", "model", "reasoning", "instructions", "instructions_file", "report")


def validate(document: object, *, base: Path | None, harnesses: Mapping[str, tuple[str, ...]]) -> dict[str, dict[str, object]]:
    """Return experts with instructions resolved; ``harnesses`` maps a harness to its reasoning efforts."""
    if not isinstance(document, Mapping) or document.get("schema") != EXPERTS_SCHEMA:
        raise ValueError(f"an experts file must declare schema: {EXPERTS_SCHEMA}")
    entries = document.get("experts") or {}
    if not isinstance(entries, Mapping):
        raise ValueError("experts must be a mapping of expert names to settings")
    experts: dict[str, dict[str, object]] = {}
    for name, entry in entries.items():
        where = f"expert {name!r}"
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"{where}: names use letters, digits, '-' or '_' (at most 32)")
        if not isinstance(entry, Mapping):
            raise ValueError(f"{where}: settings must be a mapping")
        unknown = set(entry) - set(FIELDS)
        if unknown:
            raise ValueError(f"{where}: unknown fields {sorted(unknown)}")
        harness = entry.get("harness")
        if harness not in harnesses:
            raise ValueError(f"{where}: harness must be one of {', '.join(harnesses)}")
        reasoning = entry.get("reasoning")
        if reasoning is not None and reasoning not in harnesses[harness]:
            raise ValueError(f"{where}: {harness} does not accept reasoning {reasoning!r}")
        if "instructions" in entry and "instructions_file" in entry:
            raise ValueError(f"{where}: give instructions or instructions_file, not both")
        instructions = entry.get("instructions")
        if "instructions_file" in entry:
            if base is None:
                raise ValueError(f"{where}: instructions_file needs a file location")
            source = (base / str(entry["instructions_file"])).expanduser()
            try:
                instructions = source.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"{where}: cannot read {source}: {exc}") from exc
        if instructions is not None and not isinstance(instructions, str):
            raise ValueError(f"{where}: instructions must be text")
        for field in ("description", "model"):
            if entry.get(field) is not None and not isinstance(entry[field], str):
                raise ValueError(f"{where}: {field} must be text")
        report = entry.get("report", True)
        if not isinstance(report, bool):
            raise ValueError(f"{where}: report must be true or false")
        experts[name] = {"description": entry.get("description"), "harness": harness, "model": entry.get("model"),
                         "reasoning": reasoning, "instructions": (instructions or "").strip() or None, "report": report}
    return experts


def document(experts: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    """A self-contained experts document: instructions inline, empty fields left out."""
    return {"schema": EXPERTS_SCHEMA, "experts": {
        name: {key: value for key, value in expert.items() if value is not None and not (key == "report" and value is True)}
        for name, expert in sorted(experts.items())}}


def prompt(expert: Mapping[str, object], task: str) -> str:
    instructions = expert.get("instructions")
    return task if not instructions else f"{instructions}\n\n## Task\n\n{task}"
