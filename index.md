A harness for your harnesses

# Harness Reins

Hand training runs, test suites, long experiments and child agents to an independent system process. The agent doesn't have to wait up: when the work finishes, the result comes back to the session that asked for it. Works with Codex, Claude Code and Pi.

[Get started](quickstart.md)
[See the benchmark](benchmark.md)


**8×**
*cheaper*
first 60 h of a 3-day task: ¥35 vs ¥287

**72 h**
*finished despite the kill*
the run without Reins failed on the spot

**2 s**
*back in the same session*
median time from task end to resumed turn


## One task, five steps

![Five steps: submit, own, run, queue, resume; for the next stage the agent submits again](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/workflow-light.svg)

From the agent's session, submit a training command:

```bash
reins run --cwd "$PWD" --task "Train the model" -- python train.py --config configs/exp.yaml
```

Reins returns a task ID and a log path at once, and the agent ends its turn. When the task finishes, the same
session receives a callback; the agent checks the output and runs the ACK command it contains. Start with the
ten-second example in [Your first task](quickstart.md).

## What do you want to do?


-   **Run long commands**

    Training, tests, builds and data jobs that report back when they finish.

    [Running commands](guides/run.md)

-   **Start a child agent**

    Give a self-contained piece of work to another agent and get its answer back.

    [Running child agents](guides/agents.md)

-   **Connect existing jobs**

    Jobs already running in Slurm or tmux call back the same way.

    [External jobs](guides/external-jobs.md)

-   **Reuse task templates**

    Turn review and analysis instructions into a template you dispatch in one command.

    [Task templates](guides/templates.md)

-   **Track multi-stage goals**

    Training, evaluation and reporting as one goal, checked at every stage.

    [Multi-stage goals](guides/goals.md)

-   **See the numbers**

    8× cheaper on a 3-day task, and the work survives the agent being killed.

    [Benchmark](benchmark.md)


## Set up your environment

Reins runs on Linux, macOS and Windows. Follow [Installation](installation.md), then choose your agent:
[Codex CLI](integrations/codex.md), [Claude Code](integrations/claude.md), [Pi](integrations/pi.md) or [Codex Desktop](integrations/desktop.md).
The [support matrix](integrations/support.md) lists versions and callback routes, and [Use cases](guides/use-cases.md)
walks through complete workflows.

## Keep reading

[Architecture](overview.md) follows a task from submission to callback. Use the [CLI reference](reference/cli.md)
for commands and [Troubleshooting](troubleshooting.md) for common problems. Implementation notes and validation
records are in [Engineering notes](reference/engineering.md).
