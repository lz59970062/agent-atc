"""reins expert: manage the portable experts file (~/.reins/experts.yaml).

``export`` writes a self-contained file (instructions inline) that ``import``
reads on another machine. ``reins agent expert <name>`` runs one.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

from ..core import experts as expert_rules
from ..harness import CHILD_AGENT_NAMES, get_child_agent
from ..store import layout
from ..store import records as queue_store


def default_path() -> Path:
    return layout.reins_home() / "experts.yaml"


def _harnesses() -> dict[str, tuple[str, ...]]:
    return {name: tuple(get_child_agent(name).reasoning_efforts) if get_child_agent(name).supports_reasoning_effort else ()
            for name in CHILD_AGENT_NAMES}


def load(path: Path | None = None) -> dict[str, dict[str, object]]:
    path = (path or default_path()).expanduser()
    if not path.is_file():
        return {}
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read experts file {path}: {exc}") from exc
    return expert_rules.validate(document, base=path.parent, harnesses=_harnesses())


def get(name: str, path: Path | None = None) -> dict[str, object]:
    experts = load(path)
    if name not in experts:
        known = ", ".join(sorted(experts)) or "none configured"
        raise ValueError(f"no expert named {name!r} (known: {known}); add one with reins expert import <file>")
    return experts[name]


class _Dumper(yaml.SafeDumper):
    pass


def _text(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _text)


def dump(experts: dict[str, dict[str, object]]) -> str:
    return yaml.dump(expert_rules.document(experts), Dumper=_Dumper, allow_unicode=True, sort_keys=False)


def _path(args: argparse.Namespace) -> Path | None:
    return Path(args.experts).expanduser() if getattr(args, "experts", None) else None


def list_experts(args: argparse.Namespace) -> int:
    experts = load(_path(args))
    if not experts:
        print(f"No experts configured in {_path(args) or default_path()}; add some with reins expert import <file>")
        return 0
    width = max(len(name) for name in experts)
    for name, expert in sorted(experts.items()):
        model = f" ({expert['model']})" if expert.get("model") else ""
        print(f"{name:<{width}}  {expert['harness']}{model}  {expert.get('description') or ''}".rstrip())
    return 0


def show_expert(args: argparse.Namespace) -> int:
    print(dump({args.name: get(args.name, _path(args))}), end="")
    return 0


def export_experts(args: argparse.Namespace) -> int:
    experts = load(_path(args))
    if args.names:
        missing = sorted(set(args.names) - set(experts))
        if missing:
            raise ValueError(f"no experts named {', '.join(missing)}")
        experts = {name: experts[name] for name in args.names}
    print(dump(experts), end="")
    return 0


def import_experts(args: argparse.Namespace) -> int:
    source = Path(args.file).expanduser()
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {source}: {exc}") from exc
    incoming = expert_rules.validate(document, base=source.parent, harnesses=_harnesses())
    target = _path(args) or default_path()
    current = {} if args.replace else load(target)
    replaced = sorted(set(incoming) & set(current))
    current.update(incoming)
    queue_store.private_directory(target.parent)
    queue_store.write_private_text(target, dump(current))
    print(f"Imported {len(incoming)} expert(s) into {target}" + (f"; replaced {', '.join(replaced)}" if replaced else ""))
    return 0


def command(args: argparse.Namespace) -> int:
    try:
        return {"list": list_experts, "show": show_expert, "export": export_experts,
                "import": import_experts}[args.expert_action](args)
    except ValueError as exc:
        print(f"reins: {exc}", file=sys.stderr)
        return 2
