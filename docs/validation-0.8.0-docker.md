# 0.8.0.dev0 Linux validation in Docker — 2026-10-15

These checks ran on one Linux host (x86_64, kernel 5.15) in containers, so they
could install services, run the old LTC and migrate without touching the host.
They cover Linux only; macOS and Windows still need native validation.

## Unit and integration suites

| Environment | Gates | Result |
| --- | --- | --- |
| Debian 12, Python 3.12, GNU screen, non-root | `ATC_TEST_SCREEN=1` | OK, 94 skipped; both screen integration tests ran |
| Debian 12 with systemd as PID 1 and a lingering user manager (privileged container) | `ATC_TEST_SCREEN=1 ATC_TEST_SYSTEMD=1` | OK, 93 skipped |
| Same, without `XDG_RUNTIME_DIR` in the shell | `ATC_TEST_SYSTEMD=1` | OK after the test's own `systemctl` calls used `user_manager_environment()` (fixed in 39a7c4c) |

The systemd integration test stops the coordinator service while a task runs and
confirms the task finishes and produces exactly one callback. The screen
integration test kills the standalone coordinator and confirms the screen task
survives.

## End to end: LTC, `atc migrate`, ATC

In a fresh systemd container, as a non-root user, with a fake `codex` that records
the prompt it is resumed with and runs the ACK command in it:

1. The released LTC (`long-task-wakeup` 8e29b99) installed its systemd user
   coordinator, ran `ltc run -- sleep 2`, delivered the callback to the fake
   agent and received its ACK.
2. `atc migrate` printed the plan and changed nothing. `atc migrate --apply`
   renamed `~/.ltc` to `~/.ltc.migrated-20261015` with its history intact, stopped
   and removed `codex-long-task-wakeup.service`, and removed the LTC skills.
3. `atc setup --force --enable --now` wrote `~/.atc/bin/atc`, and
   `agent-atc.service` runs that launcher.
4. `atc run -- bash -c 'echo hello; exit 3'` resumed the bound session
   (`codex exec resume … atc-session -`) with:

   ```text
   [atc] 0c7105ed-c1
   Task: atc job
   Result: exited 3 · 0.0s
   From: run on systemd-user
   Files: /home/tester/.atc/tasks/0c7105ed/ (attempt-1.log, result.json)
   Details: /home/tester/.atc/queue/details/0c7105ed-c1.md (as needed)
   Session: atc-session (only)
   ACK after inspection: /home/tester/.atc/bin/atc ack 0c7105ed-c1
   ```

   The fake agent's ACK landed, `result.json` recorded `observed.state: exited`
   with exit code 3, and `atc doctor` reported `ready` with no issues.

## The frozen binary

The PyInstaller build from `packaging/build-linux.sh` (what the npm package
ships) reports `atc 0.8.0.dev0 (build b8e7dd9f1eeb-dirty)`.

- Under real systemd: `atc setup` copied it to `~/.atc/versions/`, the delivered
  copy was then deleted, and the coordinator, task worker and delivery worker
  still ran a task to an acknowledged callback.
- `atc --version` and `atc status` ran on Debian 11 (glibc 2.31, the build
  baseline), Debian 12 (2.36), Debian 13 (2.41) and Ubuntu 22.04 (2.35).
- On Alpine (musl) the binary cannot load (`/atc/atc: not found`). The npm
  launcher now detects musl and points to a source install instead; that message
  was checked only against glibc detection, because no Alpine image with Node
  was available offline.

Earlier on the host, `npm install -g` of the two packages, `atc setup` in a
temporary home, and a pip installation with a different build forwarding to
the installed one were also verified.

## Not covered

- macOS (launchd) and Windows (Task Scheduler) native backends.
- Ubuntu 20.04, Fedora and RHEL-compatible images: the registry mirrors on this
  host could not pull them.
- Real Codex, Claude Code or Pi sessions: delivery used a fake `codex`.
