---
name: agent-atc
description: Run long jobs (training, benchmarks, builds, test suites, deployments, data jobs, child agents) under Agent Traffic Control (atc) so they outlive this run, and get the result steered back into this same Pi session when they finish. Use instead of sleeping, polling logs, or blocking a tool call whenever a command may take about a minute or longer. Also covers handling an `[atc-status]` repair block and acknowledging `[atc]` results.
---

# Agent Traffic Control for Pi

ATC hands a long command to an independent OS task owner (systemd on Linux, launchd on macOS,
Task Scheduler on Windows; `screen` as fallback) and queues a callback when it exits. ATC's Pi
extension delivers that callback **into this live Pi process** through Pi's native steering:
while you are busy it arrives after the current tool batch, before your next model call; while
idle it starts a normal turn. You do not need to wait, poll, or end your run to receive it.

The full reference (all flags, platforms, recovery and goals) is in [REFERENCE.md](REFERENCE.md).
Read it only when you need details beyond this page. It is shared with other agents; where it
differs from this page about callback delivery, this page applies to Pi.

## When to use it

- Reliably done within 60 seconds → run it in the foreground once. No ATC.
- About a minute or longer, uncertain duration, or might need a second check → `atc run` from the start.
- Never keep a job alive with `sleep`/`tail` polling or a blocking tool call. Steering cannot
  interrupt a running tool, so a long foreground command also delays your callbacks.

## The workflow

1. **Submit** (returns in a second or two):

   ```bash
   atc run --cwd "$PWD" --task "train model" -- python train.py --config configs/exp.yaml
   ```

   Inside Pi's shell, ATC binds the callback to this session's absolute `$PI_SESSION_FILE`
   automatically. stderr prints the task id, backend and log path.

2. **Keep working** on independent work, or tell the user what is running and stop. There is
   nothing to wait on: the callback is pushed to you.

3. **Handle the callback** — it arrives as a user message:

   ```text
   [atc] <callback-id>
   Task: train model
   Result: finished; exit=0
   Files: <task dir>/ (attempt-1.log)
   Details: <path> (as needed | read before acting)
   Session: <this session file> (only)
   ```

   Inspect the log and artifacts (read Details first when it says "read before acting"), decide
   `continue | stop_success | stop_blocked | ask_user`, then run the `atc ack ...` command from
   the callback. ACK means "received and inspected", not "goal done". An exit code is process
   status, not proof that tests passed. Delivery is at-least-once: check existing processes and
   outputs before relaunching anything.

## Ordinary and managed Pi sessions

- **Ordinary Pi** (started as `pi`, with the extension installed) receives callbacks only while
  it is running. If this process exits before ACKing a delivered callback, ATC marks it for manual
  recovery instead of replaying it; when Pi was closed before delivery, reopen the session and run
  `atc retry --id <callback-id>`.
- **Managed Pi** (`atc pi --cwd "$PWD"` or `atc pi --session /absolute/session.jsonl`) locks the
  session file for its lifetime. After it fully exits, ATC may deliver a pending callback by
  resuming the same file in print mode. Managed sessions are pinned: restart `atc pi` to switch,
  fork or reload.

## Other entry points

- `atc agent codex|claude|pi --cwd "$PWD" --task "..." -- "<prompt>"` — a fresh child agent; its
  result comes back as a callback here.
- `atc done --cwd "$PWD" --task "..." --exit-code 0` — report work you are already supervising.
- `atc status --state pending --state running` — inspect tasks and callbacks.

## `[atc-status]` blocks

ATC prints an `[atc-status]` JSON block when something needs attention. You own the repair: follow
`repair_command`, then `recheck_command`. `callback_queued` or `task_persisted` means the work
already exists — repair delivery around that id and **do not submit it again**. For Pi, a blocked
route usually means the extension is not loaded: `atc install-pi-extension`, then `/reload` (or
restart Pi), then recheck with `atc doctor --agent pi --session "$PI_SESSION_FILE"`.
