# Configuration

Reins saves task commands, logs, execution results, and callback records. These files let you inspect earlier runs when troubleshooting or continuing work.

## Default locations

| Location | Contents |
| --- | --- |
| `~/.reins/tasks/<task-id>/task.json` | Command, working directory, backend, and session binding |
| `~/.reins/tasks/<task-id>/attempt-1.log` | Output from the first execution attempt |
| `~/.reins/tasks/<task-id>/result.json` | Exit code and execution result |
| `~/.reins/queue/` | Callbacks and their delivery states |
| `~/.reins/callback-hook.md` | User-supplied callback instructions |
| `~/.reins/callback-prompts.yaml` | Callback reminder intervals |
| `~/.config/reins/templates/` | Reusable task templates |

The child Agent's answer path and callback details path appear in the submission output or callback. Open the files at those paths.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `REINS_HOME` | Reins state directory; defaults to `~/.reins` |
| `CODEX_HOME` | Codex configuration and sessions; defaults to `~/.codex` |
| `CLAUDE_CONFIG_DIR` | Claude Code configuration; defaults to `~/.claude` |
| `PI_CODING_AGENT_DIR` | Pi configuration; defaults to `~/.pi/agent` |
| `REINS_TEMPLATE_DIR` | Custom template directory |

`~` means the current user's home directory, or the user profile directory on Windows.

## Use a custom queue

Choose an absolute path and use the same directory for setup, submission, and acknowledgement:

```bash
reins setup --queue-dir /absolute/path/to/reins-queue --enable --now
reins run --queue-dir /absolute/path/to/reins-queue --task "Tests" -- python -m pytest
reins status --queue-dir /absolute/path/to/reins-queue
```

Finish the work in the old queue before changing an existing coordinator's queue. To run multiple Reins installations, give each one a separate Agent profile and queue.

## Edit callback instructions

Write your preferred result-handling instructions in `callback-hook.md`. Reins reads the current file content when a reminder is due. Set the intervals with:

```bash
reins prompt-policy --system-every 4 --user-every 3
```

The two numbers control the system and user prompt intervals. Set an interval to `1` to attach that prompt to every callback.
