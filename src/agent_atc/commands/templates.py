"""atc template: register expert agent templates and advertise their skills."""
from __future__ import annotations

import argparse
import json
import sys
from .. import template_registration


def template_command(args: argparse.Namespace) -> int:
    try:
        if args.template_action == "register":
            result = template_registration.register(args)
        elif args.template_action == "unregister":
            result = template_registration.unregister(args)
        else:
            result = template_registration.list_templates()
            if not args.json:
                for entry in result:
                    print(f"{entry['name']} ({entry['worker']}, ${entry['skill']}): {entry['description']}")
                if not result:
                    print("No registered child-agent templates.")
                return 0
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.dry_run or args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.template_action == "register":
            print(f"Registered {result['name']} for {result['worker']}.")
            print(f"Skill: ${result['skill']}")
            print(f"Template: {result['template_file']}")
        else:
            print(f"{result['name']} is unregistered; template and system prompt files retained.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"atc: {exc}", file=sys.stderr)
        return 1
