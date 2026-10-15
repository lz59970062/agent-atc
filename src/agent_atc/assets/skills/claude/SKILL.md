---
name: agent-atc
description: Run long jobs (training, benchmarks, builds, test suites, deployments, data jobs, child agents) under Agent Traffic Control (atc) so they outlive this turn, and get woken in this same Claude Code session when they finish via a background `atc wait`. Use instead of sleeping, polling logs, or leaving a job owned by the Bash tool whenever a command may take about a minute or longer. Also covers handling an `[atc-status]` repair block and acknowledging `[atc]` results.
---

# Agent Traffic Control for Claude Code

ATC hands a long command to an independent OS task owner (launchd on macOS, systemd on Linux,
Task Scheduler on Windows) and queues a callback when it exits. In Claude Code you receive that
callback **in this live session** by running `atc wait` as a background Bash command: Claude Code
wakes you when a background command exits, and `atc wait` exits as soon as the callback is ready.

The full reference (all flags, platforms, recovery and goals) is in [REFERENCE.md](REFERENCE.md).
Read it only when you need details beyond this page. It is shared with other agents; where it
differs from this page about callback delivery, this page applies to Claude Code.

## When to use it

- Reliably done within 60 seconds → run it in the foreground once. No ATC.
- About a minute or longer, uncertain duration, or might need a second check → `atc run` from the start.
- Never keep a job alive by polling it with `sleep`/`tail`, and never leave a long job owned by a
  `run_in_background` Bash shell: it dies with the session and is not recovered.

## The workflow

1. **Submit** (foreground; returns in a second or two):

   ```bash
   atc run --cwd "$PWD" --task "train model" -- python train.py --config configs/exp.yaml
   ```

   stderr prints the task id, backend, log path, and a ready-made line:
   `atc: Claude Code live callback: run in the background (run_in_background): .../atc wait --queue-dir ... --task <id>`

   If it instead says the callback `arrives through the atc channel`, this session was started with
   `atc claude`: skip step 2. The callback is pushed into this session as a
   `<channel source="atc">` event; handle it as in step 3.

2. **Wait** — run exactly that `atc wait ... --task <id>` command with the Bash tool's
   `run_in_background: true`. Do not run it in the foreground (it blocks until the job ends), and do
   not poll its output. Then tell the user what is running and end your turn, or carry on with other
   work.

   If the Bash tool has no `run_in_background` (background tasks are disabled; `atc run` then says
   so instead of printing a wait command), skip the wait: tell the user what is running and end your
   turn. When the job finishes, ATC resumes this session headlessly and the callback arrives as the
   next message.

3. **Wake up** — when the job finishes, the background command exits and prints a callback:

   ```text
   [atc] <task-id>-c1
   Task: train model
   Result: exited 0 · 2h 3m 32s
   From: run on systemd-user
   Files: <task dir>/ (attempt-1.log)
   Details: <path> (as needed | read before acting)
   Session: <this session id> (only)
   ACK after inspection: atc ack <task-id>-c1
   ```

   Read the background command's output, inspect the log and artifacts (read Details first when it
   says "read before acting"), decide `continue | stop_success | stop_blocked | ask_user`, then run
   the `atc ack ...` command from the callback. ACK means "received and inspected", not "goal done".
   An exit code is process status, not proof that tests passed.

4. **Next step** — if you launch follow-up work, submit it with `atc run` again and start a new
   background `atc wait --task <new-id>`. Inspect existing processes and outputs first: delivery is
   at-least-once.

## Other entry points

- **Fresh child agent** (independent review, test authoring, a parallel fix):

  ```bash
  atc agent claude --cwd "$PWD" --name parser-review -- "Inspect parser.py and report concrete defects."
  atc agent codex --template test --name parser-tests -- "Test the contract in docs/parser.md."
  atc agent continue <task-or-callback-id> --name recheck -- "I fixed those; review parser.py again."
  ```

  Every child needs `--name`. `atc agent continue <id>` reuses the same child with what it already
  learned; `atc agent fork <id>` branches from it. `<id>` is the task ID, the callback ID, or the
  node's full name; prefer IDs. See `atc context show <node>` for summaries;
  never read a child's raw session.

  Then background `atc wait --task <id>` exactly as for `atc run`. The child's result path is in the
  callback. A Claude child needs a standalone `claude` CLI that is signed in (`claude auth status`);
  the Claude desktop app's bundled CLI cannot authenticate on its own.

- **Work already owned elsewhere** (tmux, screen, Slurm, a script): add `atc done` at its end.

  ```bash
  set +e; python train.py; status=$?
  atc done --cwd "$PWD" --task "train model" --command "python train.py" --exit-code "$status"
  exit "$status"
  ```

  The callback id is not known in advance, so start a background `atc wait` with no `--task`: it
  delivers the next unacknowledged callback bound to this session.

## Rules

- **Session binding is automatic** from `CLAUDE_CODE_SESSION_ID`. Never invent a session id. `atc wait` refuses to run without a session.
- **One wait, one wake-up.** A wait exits after printing what it found; start a new one for the next
  task. Every unacknowledged callback of this session keeps coming back until you ACK it.
- **If the session closes**, nothing is lost: when no waiter is alive, the ATC daemon falls back to a
  headless `claude -p --resume <session>` from the session's original directory. That fallback needs
  a signed-in standalone CLI; otherwise the callback stays queued and the next `atc wait` from this
  session (or `atc status`) picks it up. After a live delivery, the daemon waits 30 minutes for the
  ACK before it falls back.
- `atc wait` exists only for Claude Code. It refuses to run without `CLAUDE_CODE_SESSION_ID` and
  never touches callbacks bound to other agents.
- **Do not** call `claude -p --resume` yourself, add `--via-daemon`, or invent launch flags. Callback
  problems never change the task's own exit code unless the user asks for `--strict`.
- **Long objectives with several stages**: use `atc goal start|check|ack` with a YAML plan file, see
  REFERENCE.md ("Goal acknowledgement"). Callback ACK never completes a goal.

## `[atc-status]` blocks

If `atc run`, `atc agent` or `atc done` prints JSON between `[atc-status]` and `[/atc-status]`, it is
addressed to you: fix the listed configuration issues yourself (do not hand routine setup back to the
user), run its `recheck_command`, then continue. Check `work.state` first — `task_persisted` or
`callback_queued` means the work already exists: repair around that id and **do not submit it again**.
For an explicit check: `atc doctor --agent claude --session "$CLAUDE_CODE_SESSION_ID"`.

## Install and verify

```bash
python3 -m pip install "git+https://github.com/lz59970062/agent-atc.git"
atc setup --force --enable --now
atc doctor --agent claude --session "$CLAUDE_CODE_SESSION_ID"
```

Useful commands: `atc status` (unacknowledged callbacks), `atc cancel --id <id>`,
`atc retry --id <id>` (re-deliver a result without rerunning the task),
`tail -f <task log>` when the user asks to watch progress.
