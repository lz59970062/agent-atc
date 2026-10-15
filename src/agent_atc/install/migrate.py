"""Move a machine from Long Task Callback (LTC) to ATC in one explicit step.

ATC reads no LTC records. ``atc migrate`` shows what it would change and
does nothing else; ``--apply`` makes the change. It refuses while LTC still
has callbacks to deliver or tasks running (``--wait`` waits for the old daemon
to finish them), then archives the LTC state directory unchanged, removes the
old coordinator service, skills, Pi extension and shell hook, and leaves the
new installation to ``atc setup``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from ..harness.pi import channel as pi_channel
from ..store import layout

OLD_SERVICE = "codex-long-task-wakeup"
OLD_SKILL = "long-task-callback"
OLD_PI_EXTENSION = "ltc-callback.js"
OLD_HOOK_BEGIN = "# >>> codex-long-task-wakeup pending status >>>"
OLD_HOOK_END = "# <<< codex-long-task-wakeup pending status <<<"
LIVE_QUEUE_STATES = ("active", "pending", "running")
LIVE_TASK_STATES = ("submitted", "launching", "running")
WAIT_INTERVAL = 30.0


@dataclass
class Plan:
    state_dirs: list[Path] = field(default_factory=list)       # archived, never deleted
    links: list[Path] = field(default_factory=list)            # legacy symlinks to an archived dir
    live: list[str] = field(default_factory=list)              # work LTC still owns
    files: list[Path] = field(default_factory=list)            # removed
    directories: list[Path] = field(default_factory=list)      # removed
    shell_hooks: list[Path] = field(default_factory=list)      # hook block removed
    services: list[tuple[str, list[list[str]], Path | None]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.state_dirs or self.links or self.files or self.directories or self.shell_hooks or self.services)


def _ltc_home(environment: dict[str, str]) -> Path:
    return Path(environment.get("LTC_HOME") or "~/.ltc").expanduser()


def _codex_home(environment: dict[str, str]) -> Path:
    return Path(environment.get("CODEX_HOME") or "~/.codex").expanduser()


def _claude_home(environment: dict[str, str]) -> Path:
    return Path(environment.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def live_work(state: Path) -> list[str]:
    """Callbacks and tasks the old daemon still owns in one LTC state directory."""
    found = []
    queue = state / "queue"
    for name in LIVE_QUEUE_STATES:
        for path in sorted((queue / name).glob("*.json")):
            found.append(f"callback {path.stem} ({name})")
    for path in sorted((state / "tasks").glob("*/task.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found.append(f"task {path.parent.name} (unreadable record)")
            continue
        if isinstance(record, dict) and record.get("state") in LIVE_TASK_STATES:
            found.append(f"task {path.parent.name} ({record.get('state')})")
    return found


def build_plan(environment: dict[str, str] | None = None, *, home: Path | None = None) -> Plan:
    environment = dict(os.environ if environment is None else environment)
    home = home or Path.home()
    plan = Plan()
    atc_state = Path(environment.get(layout.ATC_HOME_ENV) or "~/.atc").expanduser().resolve()

    seen: set[Path] = set()
    for candidate in (_ltc_home(environment), _codex_home(environment) / "long-task-wakeup"):
        if candidate.is_symlink():
            plan.links.append(candidate)  # an old migration link; its target is archived below
            state = candidate.resolve()
        elif candidate.is_dir():
            state = candidate
        else:
            continue
        resolved = state.resolve()
        if state.is_dir() and resolved not in seen and resolved != atc_state:
            seen.add(resolved)
            plan.state_dirs.append(resolved)
    for state in plan.state_dirs:
        plan.live.extend(f"{item} in {state}" for item in live_work(state))

    for skills in (_codex_home(environment) / "skills", _claude_home(environment) / "skills",
                   pi_channel.profile_dir() / "skills"):
        skill = skills / OLD_SKILL
        if skill.exists() or skill.is_symlink():
            plan.directories.append(skill)
        for template in sorted(skills.glob("ltc-*")):
            plan.notes.append(f"LTC template skill {template} still calls ltc; re-register it with atc template register")
    pi_profile = pi_channel.profile_dir()
    extension = pi_profile / "extensions" / OLD_PI_EXTENSION
    if extension.exists():
        plan.files.append(extension)
    channels = pi_profile / OLD_SKILL
    if channels.is_dir():
        plan.directories.append(channels)

    for rc in (home / ".bashrc", home / ".bash_profile", home / ".profile"):
        try:
            if OLD_HOOK_BEGIN in rc.read_text(encoding="utf-8"):
                plan.shell_hooks.append(rc)
        except (OSError, UnicodeError):
            continue

    unit = Path(environment.get("XDG_CONFIG_HOME") or home / ".config") / "systemd" / "user" / f"{OLD_SERVICE}.service"
    if unit.exists():
        plan.services.append(("systemd user service", [
            ["systemctl", "--user", "disable", "--now", f"{OLD_SERVICE}.service"],
            ["systemctl", "--user", "daemon-reload"],
        ], unit))
    plist = home / "Library" / "LaunchAgents" / f"{OLD_SERVICE}.plist"
    if plist.exists():
        plan.services.append(("launchd agent", [["launchctl", "bootout", f"gui/{os.getuid()}/{OLD_SERVICE}"]]
                              if hasattr(os, "getuid") else [], plist))
    if sys.platform == "win32" and shutil.which("schtasks"):
        query = subprocess.run(["schtasks", "/Query", "/TN", OLD_SERVICE], capture_output=True, text=True)
        if query.returncode == 0:
            plan.services.append(("Windows scheduled task", [["schtasks", "/Delete", "/TN", OLD_SERVICE, "/F"]], None))
    supervisor = Path("/etc/supervisor/conf.d") / f"{OLD_SERVICE}.conf"
    if supervisor.exists():
        plan.notes.append(f"supervisor program {supervisor} needs root to remove: "
                          f"sudo supervisorctl stop {OLD_SERVICE} && sudo rm {supervisor} && sudo supervisorctl update")
    for name in ("ltc", OLD_SERVICE):
        found = shutil.which(name)
        if found:
            plan.notes.append(f"old command {found} remains; remove it with: pip uninstall codex-long-task-callback")
    return plan


def describe(plan: Plan) -> list[str]:
    lines = []
    for state in plan.state_dirs:
        lines.append(f"archive LTC state {state} -> {archive_path(state)} (kept, not read by ATC)")
    for link in plan.links:
        lines.append(f"remove legacy link {link}")
    for label, commands, path in plan.services:
        lines.append(f"remove old {label}" + (f" ({path})" if path else ""))
    for path in plan.directories:
        lines.append(f"remove {path}")
    for path in plan.files:
        lines.append(f"remove {path}")
    for rc in plan.shell_hooks:
        lines.append(f"remove the LTC status hook from {rc}")
    return lines


def archive_path(state: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    candidate = state.with_name(f"{state.name}.migrated-{stamp}")
    index = 2
    while candidate.exists():
        candidate = state.with_name(f"{state.name}.migrated-{stamp}-{index}")
        index += 1
    return candidate


def remove_hook(rc: Path) -> None:
    lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
    kept, inside = [], False
    for line in lines:
        if line.strip() == OLD_HOOK_BEGIN:
            inside = True
            continue
        if inside and line.strip() == OLD_HOOK_END:
            inside = False
            continue
        if not inside:
            kept.append(line)
    rc.write_text("".join(kept), encoding="utf-8")


def apply(plan: Plan) -> list[str]:
    """Carry out a plan whose LTC work has drained. Returns problems that remain."""
    problems = []
    for label, commands, path in plan.services:
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0 and "disable" not in command and "bootout" not in command:
                problems.append(f"{' '.join(command)} failed: {result.stderr.strip()}")
        if path is not None:
            path.unlink(missing_ok=True)
    for link in plan.links:
        link.unlink(missing_ok=True)
    for state in plan.state_dirs:
        os.replace(state, archive_path(state))
    for path in plan.directories:
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path, ignore_errors=True)
    for path in plan.files:
        path.unlink(missing_ok=True)
    for rc in plan.shell_hooks:
        remove_hook(rc)
    return problems


def command(args: argparse.Namespace) -> int:
    plan = build_plan()
    while plan.live and getattr(args, "wait", False):
        print(f"atc migrate: waiting for LTC to finish {len(plan.live)} item(s); next check in {WAIT_INTERVAL:.0f}s", file=sys.stderr)
        time.sleep(WAIT_INTERVAL)
        plan = build_plan()
    if plan.empty():
        print("atc migrate: no Long Task Callback installation found; nothing to do.")
        for note in plan.notes:
            print(f"note: {note}")
        return 0
    print("atc migrate plan:")
    for line in describe(plan):
        print(f"  - {line}")
    for note in plan.notes:
        print(f"note: {note}")
    if plan.live:
        print("atc migrate: LTC still owns this work; let its daemon finish it, or rerun with --wait:", file=sys.stderr)
        for item in plan.live:
            print(f"  - {item}", file=sys.stderr)
        return 1
    if not getattr(args, "apply", False):
        print("Nothing changed. Rerun with --apply to migrate, then run: atc setup --force --enable --now")
        return 0
    problems = apply(plan)
    for problem in problems:
        print(f"atc migrate: warning: {problem}", file=sys.stderr)
    print("Migrated. Install ATC's coordinator and skills with: atc setup --force --enable --now")
    return 1 if problems else 0
