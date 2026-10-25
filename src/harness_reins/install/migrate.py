"""Move a machine from an older generation (ATC, or Long Task Callback) to Reins.

Reins reads no records of the generations before it: Agent Traffic Control
(``atc``, 0.8) and Long Task Callback (``ltc``, up to 0.7). ``reins migrate``
shows what it would change and does nothing else; ``--apply`` makes the change.
Each generation moves on its own. One that still has callbacks to deliver or
tasks running is left alone and reported (``--wait`` waits for its daemon to
finish them); the others are migrated: their state directory is archived
unchanged, their coordinator service, skills, Pi extension and shell hook are
removed, and the new installation is left to ``reins setup``. ATC's systemd
drop-ins and its experts file carry over, since they record the user's choices.
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

LIVE_QUEUE_STATES = ("active", "pending", "running")
LIVE_TASK_STATES = ("submitted", "launching", "running")
WAIT_INTERVAL = 30.0
SERVICE = "harness-reins"


@dataclass(frozen=True)
class Generation:
    key: str
    title: str
    home_env: str
    default_home: str
    service: str
    skill: str
    template_prefix: str
    pi_extension: str
    pi_state_dir: str
    hook_begin: str
    hook_end: str
    commands: tuple[str, ...]
    uninstall: str
    codex_state: str | None = None       # an older location inside CODEX_HOME
    codex_files: str | None = None       # per-generation files inside CODEX_HOME (desktop bridge)
    experts_schema: str | None = None    # schema of a portable experts file to carry over


LTC = Generation(
    key="ltc", title="Long Task Callback (LTC)", home_env="LTC_HOME", default_home="~/.ltc",
    service="codex-long-task-wakeup", skill="long-task-callback", template_prefix="ltc-",
    pi_extension="ltc-callback.js", pi_state_dir="long-task-callback",
    hook_begin="# >>> codex-long-task-wakeup pending status >>>",
    hook_end="# <<< codex-long-task-wakeup pending status <<<",
    commands=("ltc", "codex-long-task-wakeup"), uninstall="pip uninstall codex-long-task-callback",
    codex_state="long-task-wakeup",
)
ATC = Generation(
    key="atc", title="Agent Traffic Control (ATC)", home_env="ATC_HOME", default_home="~/.atc",
    service="agent-atc", skill="agent-atc", template_prefix="atc-",
    pi_extension="atc-callback.js", pi_state_dir="agent-atc",
    hook_begin="# >>> agent-atc pending status >>>", hook_end="# <<< agent-atc pending status <<<",
    commands=("atc",), uninstall="npm uninstall -g agent-atc (or pip uninstall agent-atc)",
    codex_files="agent-atc", experts_schema="atc.experts/1",
)
GENERATIONS = (ATC, LTC)
# Kept for callers and tests that address the LTC hook directly.
OLD_HOOK_BEGIN, OLD_HOOK_END = LTC.hook_begin, LTC.hook_end


@dataclass
class Plan:
    generation: Generation
    state_dirs: list[Path] = field(default_factory=list)       # archived, never deleted
    links: list[Path] = field(default_factory=list)            # legacy symlinks to an archived dir
    live: list[str] = field(default_factory=list)              # work the old daemon still owns
    files: list[Path] = field(default_factory=list)            # removed
    directories: list[Path] = field(default_factory=list)      # removed
    shell_hooks: list[Path] = field(default_factory=list)      # hook block removed
    services: list[tuple[str, list[list[str]], Path | None]] = field(default_factory=list)
    carried: list[tuple[Path, Path]] = field(default_factory=list)  # copied to the Reins location
    notes: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.state_dirs or self.links or self.files or self.directories or self.shell_hooks
                    or self.services)


def _codex_home(environment: dict[str, str]) -> Path:
    return Path(environment.get("CODEX_HOME") or "~/.codex").expanduser()


def _claude_home(environment: dict[str, str]) -> Path:
    return Path(environment.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def _reins_home(environment: dict[str, str]) -> Path:
    return Path(environment.get(layout.REINS_HOME_ENV) or "~/.reins").expanduser().resolve()


def live_work(state: Path) -> list[str]:
    """Callbacks and tasks the old daemon still owns in one state directory."""
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


def build_plan(environment: dict[str, str] | None = None, *, home: Path | None = None,
               generation: Generation = LTC) -> Plan:
    environment = dict(os.environ if environment is None else environment)
    home = home or Path.home()
    plan = Plan(generation)
    reins_state = _reins_home(environment)
    codex_home = _codex_home(environment)

    candidates = [Path(environment.get(generation.home_env) or generation.default_home).expanduser()]
    if generation.codex_state:
        candidates.append(codex_home / generation.codex_state)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate.is_symlink():
            plan.links.append(candidate)  # an old migration link; its target is archived below
            state = candidate.resolve()
        elif candidate.is_dir():
            state = candidate
        else:
            continue
        resolved = state.resolve()
        if state.is_dir() and resolved not in seen and resolved != reins_state:
            seen.add(resolved)
            plan.state_dirs.append(resolved)
    for state in plan.state_dirs:
        plan.live.extend(f"{item} in {state}" for item in live_work(state))
        experts = state / "experts.yaml"
        if generation.experts_schema and experts.is_file() and not (reins_state / "experts.yaml").exists():
            plan.carried.append((experts, reins_state / "experts.yaml"))

    for skills in (codex_home / "skills", _claude_home(environment) / "skills", pi_channel.profile_dir() / "skills"):
        skill = skills / generation.skill
        if skill.exists() or skill.is_symlink():
            plan.directories.append(skill)
        for template in sorted(skills.glob(f"{generation.template_prefix}*")):
            plan.notes.append(f"{generation.key.upper()} template skill {template} still calls {generation.key}; "
                              "re-register it with reins template register")
    pi_profile = pi_channel.profile_dir()
    extension = pi_profile / "extensions" / generation.pi_extension
    if extension.exists():
        plan.files.append(extension)
    pi_state = pi_profile / generation.pi_state_dir
    if pi_state.is_dir():
        plan.directories.append(pi_state)
    if generation.codex_files and (codex_home / generation.codex_files).is_dir():
        plan.directories.append(codex_home / generation.codex_files)

    for rc in (home / ".bashrc", home / ".bash_profile", home / ".profile", home / ".zshrc"):
        try:
            if generation.hook_begin in rc.read_text(encoding="utf-8"):
                plan.shell_hooks.append(rc)
        except (OSError, UnicodeError):
            continue

    units = Path(environment.get("XDG_CONFIG_HOME") or home / ".config") / "systemd" / "user"
    unit = units / f"{generation.service}.service"
    if unit.exists():
        plan.services.append(("systemd user service", [
            ["systemctl", "--user", "disable", "--now", f"{generation.service}.service"],
            ["systemctl", "--user", "daemon-reload"],
        ], unit))
    dropins = units / f"{generation.service}.service.d"
    if dropins.is_dir():
        plan.directories.append(dropins)
        carried = units / f"{SERVICE}.service.d"
        if any(dropins.glob("*.conf")) and not carried.exists():
            plan.carried.append((dropins, carried))
            plan.notes.append(f"systemd drop-ins in {dropins} are copied to {carried}; they keep applying to Reins")
    plist = home / "Library" / "LaunchAgents" / f"{generation.service}.plist"
    if plist.exists():
        plan.services.append(("launchd agent", [["launchctl", "bootout", f"gui/{os.getuid()}/{generation.service}"]]
                              if hasattr(os, "getuid") else [], plist))
    if sys.platform == "win32" and shutil.which("schtasks"):
        query = subprocess.run(["schtasks", "/Query", "/TN", generation.service], capture_output=True, text=True)
        if query.returncode == 0:
            plan.services.append(("Windows scheduled task",
                                  [["schtasks", "/Delete", "/TN", generation.service, "/F"]], None))
    supervisor = Path("/etc/supervisor/conf.d") / f"{generation.service}.conf"
    if supervisor.exists():
        plan.notes.append(f"supervisor program {supervisor} needs root to remove: "
                          f"sudo supervisorctl stop {generation.service} && sudo rm {supervisor} && sudo supervisorctl update")
    for name in generation.commands:
        found = shutil.which(name, path=environment.get("PATH"))
        if found:
            plan.notes.append(f"old command {found} remains; remove it with: {generation.uninstall}")
    return plan


def build_plans(environment: dict[str, str] | None = None, *, home: Path | None = None,
                only: str | None = None) -> list[Plan]:
    return [build_plan(environment, home=home, generation=generation) for generation in GENERATIONS
            if only in (None, generation.key)]


def describe(plan: Plan) -> list[str]:
    name = plan.generation.key.upper()
    lines = []
    for state in plan.state_dirs:
        lines.append(f"archive {name} state {state} -> {archive_path(state)} (kept, not read by Reins)")
    for link in plan.links:
        lines.append(f"remove legacy link {link}")
    for label, commands, path in plan.services:
        lines.append(f"remove old {label}" + (f" ({path})" if path else ""))
    for source, target in plan.carried:
        lines.append(f"carry over {source} -> {target}")
    for path in plan.directories:
        lines.append(f"remove {path}")
    for path in plan.files:
        lines.append(f"remove {path}")
    for rc in plan.shell_hooks:
        lines.append(f"remove the {name} status hook from {rc}")
    return lines


def archive_path(state: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    candidate = state.with_name(f"{state.name}.migrated-{stamp}")
    index = 2
    while candidate.exists():
        candidate = state.with_name(f"{state.name}.migrated-{stamp}-{index}")
        index += 1
    return candidate


def remove_hook(rc: Path, begin: str = LTC.hook_begin, end: str = LTC.hook_end) -> None:
    lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
    kept, inside = [], False
    for line in lines:
        if line.strip() == begin:
            inside = True
            continue
        if inside and line.strip() == end:
            inside = False
            continue
        if not inside:
            kept.append(line)
    rc.write_text("".join(kept), encoding="utf-8")


def _carry(source: Path, target: Path, generation: Generation) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
        return
    text = source.read_text(encoding="utf-8")
    if generation.experts_schema:
        text = text.replace(generation.experts_schema, "reins.experts/1")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    os.chmod(target, 0o600)


def apply(plan: Plan) -> list[str]:
    """Carry out a plan whose old work has drained. Returns problems that remain."""
    problems = []
    for source, target in plan.carried:
        try:
            _carry(source, target, plan.generation)
        except OSError as exc:
            problems.append(f"could not carry over {source}: {exc}")
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
        remove_hook(rc, plan.generation.hook_begin, plan.generation.hook_end)
    return problems


def command(args: argparse.Namespace) -> int:
    only = getattr(args, "only", None)
    plans = build_plans(only=only)
    while getattr(args, "wait", False) and any(plan.live for plan in plans if not plan.empty()):
        waiting = sum(len(plan.live) for plan in plans)
        print(f"reins migrate: waiting for {waiting} item(s) the old daemons still own; "
              f"next check in {WAIT_INTERVAL:.0f}s", file=sys.stderr)
        time.sleep(WAIT_INTERVAL)
        plans = build_plans(only=only)
    found = [plan for plan in plans if not plan.empty()]
    for plan in plans:
        if plan.empty():
            for note in plan.notes:
                print(f"note: {note}")
    if not found:
        print("reins migrate: no ATC or Long Task Callback installation found; nothing to do.")
        return 0
    status = 0
    applying = getattr(args, "apply", False)
    for plan in found:
        print(f"reins migrate plan for {plan.generation.title}:")
        for line in describe(plan):
            print(f"  - {line}")
        for note in plan.notes:
            print(f"note: {note}")
        if plan.live:
            print(f"reins migrate: {plan.generation.key.upper()} still owns this work, so it is left as it is; "
                  "let its daemon finish, or rerun with --wait:", file=sys.stderr)
            for item in plan.live:
                print(f"  - {item}", file=sys.stderr)
            status = 1
            continue
        if not applying:
            continue
        problems = apply(plan)
        for problem in problems:
            print(f"reins migrate: warning: {problem}", file=sys.stderr)
        print(f"Migrated {plan.generation.title}.")
        status = status or (1 if problems else 0)
    if not applying:
        print("Nothing changed. Rerun with --apply to migrate, then run: reins setup --force --enable --now")
    else:
        print("Install Reins's coordinator and skills with: reins setup --force --enable --now")
    return status
