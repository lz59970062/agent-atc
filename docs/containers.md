# Linux containers and AutoDL

ATC 0.7.0 uses GNU screen when a systemd user manager is unavailable. A container
does not need systemd, privileged mode, a host service socket or the Docker socket
to run this backend. Keep ATC, the selected Agent CLI, its session files and the
workload in the **same running container**, under the same user account.

There are two independent choices:

| Setting | What it controls | Container choice |
| --- | --- | --- |
| `--backend` on `run`, `agent` or `setup` | The owner of each workload | `screen` |
| `--service` on `setup` | How the callback coordinator starts | `standalone`, an explicitly selected existing `supervisor`, or run `atc daemon` in the foreground yourself |

`setup --service auto` uses an available systemd user manager, otherwise standalone
mode. It does not automatically modify a platform-managed Supervisor installation.
The selected workload backend is saved in each task; recovery keeps that owner.

For unattended use, configure both startup and recovery rather than relying on a
detached shell. Choose the route that owns your container:

| Environment | Start ATC when the container starts | Restart an exited coordinator |
| --- | --- | --- |
| Existing AutoDL/development instance | Its verified boot hook runs standalone setup | Use an existing Supervisor if this is required |
| Container with an administered Supervisor | An ATC program with `autostart=true` | `autorestart=true`, while Supervisor remains running |
| Docker host you control | Foreground `atc daemon` with `--restart unless-stopped` | Docker restarts the whole container |

These restore the coordinator, not an interrupted training process. Keep the
existing Agent profile, queue, installation and workspace at stable paths.

## Docker image

From the `v0.7.0` release checkout:

```bash
docker build -f examples/docker/Dockerfile -t atc:0.7.0 .
docker run --rm atc:0.7.0 atc --version
docker run --rm atc:0.7.0 screen --version
```

The [example Dockerfile](../examples/docker/Dockerfile) installs Python-based ATC
and screen. It contains **no Agent CLI, model runtime, GPU stack or workload
dependencies**. Extend this image with the Codex or Claude Code CLI and the
dependencies required by your commands before expecting a real callback. The
following example uses `atc-agent:0.7.0` as the name of that prepared image.
Its selected Agent executable must be on the `atc` user's `PATH`.

An existing Debian/Ubuntu Python image can supply the base without changing the
Dockerfile:

```bash
docker build -f examples/docker/Dockerfile \
  --build-arg BASE_IMAGE=your-local-python-image:tag \
  -t atc:0.7.0 .
```

The override requires Python 3.9+ and `apt-get`; Alpine images use different
package tooling and cannot be substituted into this Dockerfile unchanged.
The build context allowlist excludes local profiles, task records, Git history
and committee discussions. Do not add credentials through `COPY`, build arguments
or image environment values.

### Start the coordinator and agent

Choose an existing absolute workspace path on the Docker host. The image uses UID
1000; that user must be able to write the mounted workspace. Adjust the image user
or workspace permissions for your deployment if the host uses a different UID.

```bash
export ATC_WORKSPACE=/absolute/path/to/your/project
docker volume create atc-state
docker run -d --init --name atc-service \
  --mount type=volume,src=atc-state,dst=/state \
  --mount "type=bind,src=$ATC_WORKSPACE,dst=/workspace" \
  atc-agent:0.7.0
docker exec atc-service atc install-skill --target both
docker logs atc-service
```

`--init` adds a small init process to handle child-process reaping. A full systemd
installation inside the container is unnecessary for this arrangement.
[Docker process guidance](https://docs.docker.com/engine/containers/multi-service_container/)

### Enable Docker startup and restart

On the **Docker host**, add `--restart unless-stopped` to the `docker run` above,
or update that existing container without recreating it:

```bash
docker update --restart unless-stopped atc-service
docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' atc-service
```

For Compose, set these keys on the service using the prepared image and the same
state/workspace mounts:

```yaml
services:
  atc:
    # Keep the image, volumes and runtime environment from your deployment.
    init: true
    restart: unless-stopped
    command: ["atc", "daemon"]
```

The host must start Docker itself on boot. `unless-stopped` leaves a manually
stopped container stopped; use `docker start atc-service` to start it again.
To remove automatic restart, use `docker update --restart no atc-service`
(Compose: `restart: "no"`). This does not stop the current container.
[Docker restart policy reference](https://docs.docker.com/engine/containers/start-containers-automatically/)

This route restarts the entire container if its main coordinator exits, ending
any screen workloads. Use the Supervisor route below when only the coordinator
should restart inside a still-running container. A rental instance without access
to its Docker host should use its platform startup mechanism instead.

### Use the Agent inside the container

Authenticate your selected Agent interactively **inside this container**, using
its supported login method. Its configuration and session directories are runtime
volume paths: `/state/codex` for `CODEX_HOME`, `/state/claude` for
`CLAUDE_CONFIG_DIR`. Existing dedicated profiles can instead be mounted at those
locations when the Agent supports that arrangement. They need write access for
session updates. Supply API credentials at container startup through your runtime
credential mechanism if the Agent uses environment-based authentication; the
coordinator also needs access when it launches a callback. Credentials supplied
only to one `docker exec` shell do not become the coordinator's environment.

Open the installed CLI in the container, for example:

```bash
docker exec -it --workdir /workspace atc-service codex
# Or, when that is the CLI installed in your image:
# docker exec -it --workdir /workspace atc-service claude
```

From that Agent's shell/tool context, submit the actual workload:

```bash
atc run --backend screen --cwd /workspace \
  --task "train experiment" -- python /workspace/train.py
```

The launching Agent normally supplies its session identifier. An unrelated
`docker exec` shell does not inherit the Agent's session; explicitly pass
`--agent codex --session <actual-container-session-id>` when submitting there.
Do not substitute a host desktop session ID: the container CLI must be able to
resume the bound session itself. The example disables Codex Desktop socket
delivery and uses the CLI callback path within the container.

### What persists

| Runtime path | Content |
| --- | --- |
| `/state/atc/` | Queue, task metadata, logs, compact callback details, ACKs, goals and reminder settings |
| `/state/codex/` and `/state/claude/` | Agent profiles, credentials and session history stored by the installed CLI |
| `/workspace/` | Your code, outputs and checkpoints |

The state volume and workspace mount must remain available at the same container
paths while tasks are active. Keep only one coordinator per queue. A named volume
outlives a container unless you explicitly remove it; a bind mount exposes the
chosen host directory to the container.
[Docker volumes](https://docs.docker.com/engine/storage/volumes/),
[bind mounts](https://docs.docker.com/engine/storage/bind-mounts/)

The image runs `atc daemon` in the foreground. **If that main service exits, the
container stops and its running tasks stop too.** Screen cannot outlive its
container. If coordinator restarts must leave workloads running, use your existing
Supervisor/process manager as the container's main service and let it manage
`atc daemon` as a child. Restart only that coordinator child; stopping the whole
container still ends all workloads. Do not use `atc setup --service standalone
--now` as a container's sole `CMD`: setup returns after starting its background
process, which ends the container's main command.

## AutoDL or an existing Linux development container

Use the environment already provided by the platform; a second Docker daemon is
not required. Install screen with the image's package manager and install ATC into
the Python environment you intend to keep available:

```bash
# Debian/Ubuntu-based image, as its administrator:
apt-get update
apt-get install -y screen

# In the selected Python environment:
python -m pip install "git+https://github.com/lz59970062/agent-atc.git@v0.7.0"
atc --version
screen --version
```

Select a directory on storage that your platform retains. Replace the example
path below with that actual path; ATC does not determine the retention policy of
an AutoDL disk or rental instance. Choose the Agent profile locations **before
opening the session**. If you already have persistent profiles, keep their current
paths instead; exporting a new path does not copy old sessions or authentication.

```bash
export ATC_PERSIST_ROOT=/absolute/path/to/persistent-disk/atc
mkdir -p "$ATC_PERSIST_ROOT"
export CODEX_HOME="$ATC_PERSIST_ROOT/codex"
export CLAUDE_CONFIG_DIR="$ATC_PERSIST_ROOT/claude"
export ATC_EXECUTION_BACKEND=screen
export ATC_DESKTOP_APP_SERVER=0

# Install the selected Agent CLI and make its authentication available first.
atc setup --backend screen --service standalone --now
atc status
```

Keep these environment settings consistent in the Agent and coordinator startup
environments. A detached standalone daemon continues after the launching shell
closes while the platform keeps the container alive. It is **not** a boot service
or a crash supervisor. Register one of the startup routes below to restore it
after the platform/container starts. Until then, restore the same environment and
run `atc setup --backend screen --service standalone --keep-skill --now` manually.
Its log is
`$ATC_HOME/daemon.log`.

Standalone coordinator PID/runtime files belong to the selected `ATC_HOME`.
Use a separate `ATC_HOME` for an isolated installation or another standalone
coordinator, and start its Agent with that same `ATC_HOME`. Merely changing
`--queue-dir` does not isolate those coordinator files.

### Give startup the same environment as the Agent

Startup hooks and Supervisor do not necessarily load `.bashrc`, activate Conda,
or load NVM. In the working Agent environment, locate the installed executables:

```bash
command -v atc
command -v codex
command -v node
command -v screen
```

For a root-owned AutoDL instance, save the following wrapper as
`/root/.local/bin/atc-container` (create its parent directory first). **Edit its
paths before using it**: the Python environment, Agent runtime directory and
workspace are examples. For another user, use that user's directories and account.
Keep the profile paths of your existing Agent sessions; do not migrate them just
to match the example. The Agent runtime directory must contain the selected CLI
and its runtime, such as Node for an npm-installed Codex.

```sh
#!/bin/sh
set -eu
export PATH="/root/miniconda3/bin:/absolute/path/to/agent-runtime/bin:/usr/local/bin:/usr/bin:/bin"
export CODEX_HOME="/root/.codex"
export CLAUDE_CONFIG_DIR="/root/.claude"
export ATC_HOME="/root/.atc"
export ATC_QUEUE_DIR="/root/.atc/queue"
export ATC_EXECUTION_BACKEND=screen
export ATC_DESKTOP_APP_SERVER=0
cd /root/agent-atc
exec /root/miniconda3/bin/atc "$@"
```

Set `ATC_QUEUE_DIR` to your actual existing queue, including
when it differs from the example. Use the same setting in the Agent environment;
the hook and Supervisor below both inherit it from this wrapper. Keep one queue
per profile.

Make it executable, check its syntax, and prepare ATC once without starting a
second coordinator:

```bash
chmod 700 /root/.local/bin/atc-container
sh -n /root/.local/bin/atc-container
/root/.local/bin/atc-container --version
/root/.local/bin/atc-container setup --backend screen --service standalone \
  --skill-target codex --keep-skill
```

For Claude, use its existing profile and `--skill-target claude` (or `both`).
If authentication needs environment variables, provide them through a private
runtime environment available to the startup process; shell-only exports will
not reach an already-running Supervisor. For ATC proxy settings, add
`--inherit-proxy` or `--proxy-env-file /absolute/path/to/private-proxy.env` to the
one-time setup. ATC loads its saved proxy file when the daemon starts. Keep
credentials out of images, shared startup scripts and committed configuration.

### AutoDL: register a verified boot hook

First identify what your instance actually executes on each boot: an image startup
script, a platform boot-hook setting, or an existing process manager. Some images
use `/root/autodl.sh`; inspect the image's startup configuration before using that
path. Creating the file alone does not register a boot hook, and `.bashrc` only
runs for certain shells rather than on container startup.

AutoDL private-cloud administrators can configure per-boot scripts in their
[instance customization settings](https://private.autodl.com/docs/practice_6/).
That is a private-cloud facility, not a promised setting for every public rental
instance. If the platform provides no editable boot hook, use the existing
Supervisor route with its administrator or retain manual startup.

In a confirmed **one-shot boot hook**, preserve its existing commands and add:

```bash
mkdir -p /root/.atc
/root/.local/bin/atc-container setup --backend screen --service standalone \
  --skill-target codex --keep-skill --now \
  >> /root/.atc/startup.log 2>&1
```

Use the actual profile path for the log directory. Place this where the hook runs
on every boot, before any `exit` or `exec` that would skip it. Back up the existing
hook before editing. The hook may return after setup because the platform keeps
this development container running. Do not register it simultaneously with a
Supervisor program for the same queue, or run it on every terminal login.

An AutoDL **elastic deployment startup command** instead controls the container's
lifetime. For that route, after the one-time setup use the foreground command:

```bash
/root/.local/bin/atc-container daemon
```

Use the platform's restart controls if available; a detached setup command is not
a suitable main command. This distinction follows
[AutoDL's deployment startup guidance](https://www.autodl.com/docs/elastic_deploy_practice/).

The one-shot hook provides startup only, not crash recovery. `--enable` does not
change that in standalone mode. Repeating setup preserves an existing skill with
`--keep-skill` and checks queue ownership before requesting a safe reload. A reload
keeps the existing process environment and arguments: changed PATH, profiles or
service options need a controlled coordinator restart after deliveries drain.
If setup reports a deferred reload during active delivery, run it again after
that delivery drains; the setup call does not queue a later reload request.

To disable this boot integration, remove only the ATC invocation from the hook;
preserve the platform's other startup commands. Removing it does not stop the
currently running daemon or delete its records.

### Existing Supervisor: startup and coordinator crash recovery

Use this route only for a Supervisor you administer which the platform already
starts on boot. Locate its actual configuration, include directory and control
socket. Use `supervisorctl -c /actual/path/supervisord.conf status` to confirm you
are talking to that instance; an installed `supervisorctl` alone proves nothing.

After the one-time setup above, add the following program to that instance's
include directory. This example uses the same root-owned wrapper; other users
must set their matching `user`, paths and directory. The log directory must exist
and be writable by that user.

```ini
[program:atc]
command=/root/.local/bin/atc-container daemon
directory=/root/agent-atc
user=root
autostart=true
autorestart=true
startsecs=2
startretries=3
stopsignal=TERM
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=/root/.atc/supervisor.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
```

The wrapper pins profile and PATH values for the daemon; Supervisor's `user=`
changes the execution identity without loading that user's login environment.
Keep ATC in the foreground under Supervisor.
[Supervisor environment and process rules](https://supervisord.org/subprocess.html)

Configure one coordinator per queue. Before switching an existing standalone
installation, drain active callback deliveries and stop only its verified
coordinator, or schedule the switch for the next planned container start. Disable
the old boot invocation as part of the switch. Then load only the ATC program,
replacing the configuration path below with the one you inspected:

```bash
supervisorctl -c /actual/path/supervisord.conf reread
supervisorctl -c /actual/path/supervisord.conf update atc
supervisorctl -c /actual/path/supervisord.conf status atc
```

`update atc` applies changes to that program; `restart atc` alone does not reread
configuration. Avoid restarting all platform services.
[Supervisor control commands](https://supervisord.org/running.html)

If status becomes `BACKOFF` or `FATAL`, inspect the program log and correct the
path, environment or permissions before starting it again. `startretries` is
bounded; automatic restart is not a substitute for fixing startup errors.

ATC also provides `setup --service supervisor --now` for an administered default
Supervisor layout. In 0.7.0 it uses bare `supervisorctl`, can start `supervisord`
if connection fails, and runs an unscoped `update`. It does not expose a
`supervisorctl -c` option. `ATC_SUPERVISOR_CONF_DIR` changes only
where the program file is written. For a platform-managed/custom layout, use the
explicit configuration above instead. The generated program also does not pin
custom Agent profile variables; the wrapper supplies them explicitly.

To remove this integration after deliveries drain, stop only `atc` with the same
`supervisorctl -c ... stop atc`, remove its program file, and run `reread` and
`update atc`. Keep the queue and Agent profiles. Stopping the whole container
still terminates all screen workloads; Supervisor restart settings cannot prevent
that.

### Verify startup before relying on it

Check the selected hook/program and its logs. For the readiness check, use the
actual Agent session in that container. An independent shell or `docker exec`
does not inherit its session ID; set `ATC_SESSION_ID` to that real ID first:

```bash
# Replace this value with your actual container Codex session ID.
ATC_SESSION_ID='replace-with-your-actual-container-session-id'
/root/.local/bin/atc-container doctor --backend screen \
  --agent codex --session "$ATC_SESSION_ID"
/root/.local/bin/atc-container status
```

For Docker, pass the actual container session ID from the host shell:

```bash
docker exec atc-service atc doctor --backend screen \
  --agent codex --session "$ATC_SESSION_ID"
docker logs --tail 50 atc-service
```

For Claude, select `--agent claude` and its actual session ID. From an Agent's own
tool environment, the session can be detected automatically. A `session_unbound`
issue means the check lacks that binding, not that the daemon failed to start.
An idle daemon may have no screen session.
`doctor` checks local readiness; it does not prove model authentication or delivery.

During a planned idle restart, check that the coordinator starts **before** any
interactive login runs setup. Do not reboot a busy instance just to test this.
Then, from an actual Agent session in that container, submit a short smoke task:

```bash
atc run --backend screen --task "startup callback check" -- \
  python -c 'import time; time.sleep(5); print("startup callback check passed")'
```

Confirm one execution, the original session's receipt and inspection, its ACK, and
the callback's `done` record. Container recreation may change identity even with
the same mounts; inspect old records rather than treating autostart as permission
to replay interrupted work or redirect its callback.

## Stop, restart and callback boundaries

- Closing an interactive shell does not stop screen-owned tasks while their
  container and process manager stay alive.
- Stopping, replacing or deleting the container interrupts its processes. Saved
  files do not preserve a running process. Inspect the recorded result and
  checkpoints before submitting new work; ATC does not automatically rerun an
  interrupted business command.
- Recreating a container can change its host/container identity and the available
  Agent sessions, even with the same volume. ATC can classify its old task records
  as belonging to another host. Persistent files enable inspection, but do not
  establish cross-container or cross-host callback recovery.
- Keep the original bound Agent session and its files accessible. A new login or
  a new conversation is not permission to redirect an old callback.
- The Linux container path does not establish native macOS or Windows support.
  GPU access and checkpointing belong to your workload/container configuration.

The Dockerfile and these commands are deployment examples. A working image build
or CLI version check does not verify real Agent authentication and original-session
callback delivery; validate that complete path in the prepared container before
relying on it for unattended work.
