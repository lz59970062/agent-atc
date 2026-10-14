# CLI reference

Inspect the complete options with:

```bash
atc --help
atc run --help
atc agent --help
```

## Install and inspect

| Command | Purpose |
| --- | --- |
| `atc --version` | Print the current version |
| `atc setup --enable --now` | Install skills and start the coordinator |
| `atc setup --keep-skill --force --enable --now` | Update the coordinator and retain skill content |
| `atc doctor --agent codex --session <id>` | Inspect a session's runtime configuration |

## Submit work

| Command | Purpose |
| --- | --- |
| `atc run --task <name> -- <command>` | Submit a command |
| `atc agent codex -- <prompt>` | Start a Codex child Agent |
| `atc agent claude -- <prompt>` | Start a Claude child Agent |
| `atc done --task <name> --exit-code <code>` | Report an external task result |

Put submission options before `--`:

| Option | Purpose |
| --- | --- |
| `--cwd <directory>` | Working directory |
| `--task <name>` | Task name shown in the callback |
| `--agent <type>` | Agent type receiving the callback |
| `--session <session>` | Session receiving the callback |
| `--queue-dir <directory>` | Callback queue location |
| `--backend auto` | Select the task execution backend automatically |
| `--goal-id <goal>` | Associate the task with a multi-stage goal |
| `--callback-format full` | Use the full callback text |

## Handle callbacks

| Command | Purpose |
| --- | --- |
| `atc status` | Inspect outstanding callbacks |
| `atc status --state failed` | Inspect failed deliveries |
| `atc ack <callback-id>` | Confirm that you have read the result |
| `atc cancel --id <callback-id>` | Cancel delivery of this callback |
| `atc prompt-policy` | Inspect reminder intervals |

## Preview commands

These commands use the `0.7.1` preview:

| Command | Purpose |
| --- | --- |
| `atc wait --task <task-id>` | Wait for and print a callback in Claude Code |
| `atc retry --id <callback-id>` | Redeliver a saved callback |
| `atc agent pi -- <prompt>` | Start a Pi child Agent |
| `atc pi` | Open an ATC-managed Pi session |
| `atc template list` | List registered templates |
| `atc desktop status` | Inspect Desktop connection settings |
| `atc desktop launch` | Start Desktop through the shared Core |

See [Multi-stage goals](../guides/goals.md) for the goal command sequence.
