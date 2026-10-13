"""atc run, atc agent and atc done: say who does the work; the callback returns to the bound session."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from ..harness import get_child_agent
from ..templates import load_template
from ..system import shell
from ..engine import dispatch
from ..engine import tasks as managed_tasks


def done(args: argparse.Namespace) -> int:
    callback_code = dispatch.queue_callback(args, dispatch.build_prompt(args))
    return callback_code if args.strict else 0


def run(args: argparse.Namespace) -> int:
    if not args.wrapped_command:
        raise SystemExit("run mode requires a command after --")

    args.command = args.command or shell.format_command(args.wrapped_command)
    dispatch.bind_target(args)
    if args.dry_run:
        print(
            "\n".join(
                [
                    "[atc-submission-dry-run]",
                    f"Task: {args.task}",
                    f"Working directory: {os.path.abspath(args.cwd)}",
                    f"Command: {args.command}",
                    f"Execution: daemon -> {getattr(args, 'backend', 'auto')} backend -> independent worker -> task",
                    "No task record was written and no command was started.",
                ]
            )
        )
        return 0
    return dispatch.submit_managed_run(args)


def agent(args: argparse.Namespace) -> int:
    args.agent_prompt_text = managed_tasks.agent_prompt_text(list(args.agent_prompt))
    args.template = getattr(args, "template", None)
    args.template_file = getattr(args, "template_file", None)
    args.template_version = None
    args.template_source = None
    args.template_handoff = None
    args.child_model = getattr(args, "child_model", None)
    args.child_reasoning_effort = getattr(args, "child_reasoning_effort", None)
    system_prompt_file = getattr(args, "system_prompt_file", None)
    args.agent_system_prompt_source = None
    args.agent_system_prompt_text = None
    if system_prompt_file is not None and not str(system_prompt_file).strip():
        raise SystemExit("--system-prompt-file must not be empty")
    if not args.agent_prompt_text.strip():
        raise SystemExit("agent mode requires non-empty task requirements")
    if args.child_model is not None and not args.child_model.strip():
        raise SystemExit("--model must not be empty")
    child_adapter = get_child_agent(args.agent_worker)
    if args.child_reasoning_effort and not child_adapter.supports_reasoning_effort:
        raise SystemExit(f"{child_adapter.display_name} children do not support --reasoning-effort")
    if args.template is not None or args.template_file is not None:
        try:
            profile = load_template(args.template, template_file=args.template_file)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"atc: {exc}") from exc
        args.template = profile.name
        args.template_version = profile.version
        args.template_source = profile.source
        args.template_handoff = profile.handoff
        args.child_model = args.child_model or profile.model_for(args.agent_worker)
        args.child_reasoning_effort = args.child_reasoning_effort or profile.reasoning_effort_for(args.agent_worker)
        system_prompt_file = system_prompt_file or profile.system_prompt_file_for(args.agent_worker)
        args.agent_prompt_text = profile.render(args.agent_prompt_text)
    if args.child_reasoning_effort and args.child_reasoning_effort not in child_adapter.reasoning_efforts:
        raise SystemExit(f"unsupported {child_adapter.display_name} reasoning effort {args.child_reasoning_effort!r}")
    if system_prompt_file is not None:
        if not child_adapter.supports_system_prompt_file:
            raise SystemExit(f"{child_adapter.display_name} children do not support --system-prompt-file")
        try:
            source = Path(system_prompt_file).expanduser().resolve()
            args.agent_system_prompt_text = managed_tasks.read_agent_system_prompt(source)
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            raise SystemExit(f"atc: could not load system prompt file: {exc}") from exc
        args.agent_system_prompt_source = str(source)
    args.task_kind = "agent"
    args.command = args.command or f"atc agent {args.agent_worker}"
    args.wrapped_command = []
    dispatch.bind_target(args)
    if args.dry_run:
        print(
            "\n".join(
                [
                    "[atc-agent-submission-dry-run]",
                    f"Task: {args.task}",
                    f"Working directory: {os.path.abspath(args.cwd)}",
                    f"Child agent: {child_adapter.display_name}",
                    f"Template: {args.template or 'none'} (version {getattr(args, 'template_version', None)})",
                    f"Template source: {args.template_source or 'none'}",
                    f"Child model: {args.child_model or 'CLI default'}",
                    f"Child reasoning effort: {args.child_reasoning_effort or 'CLI default'}",
                    f"System prompt source: {args.agent_system_prompt_source or 'CLI default'}",
                    f"Expanded prompt:\n{args.agent_prompt_text}",
                    f"Template handoff: {args.template_handoff or 'none'}",
                    f"Execution: daemon -> {getattr(args, 'backend', 'auto')} backend -> independent worker -> child agent",
                    "No task record was written and no child agent was started.",
                ]
            )
        )
        return 0
    return dispatch.submit_managed_run(args)
