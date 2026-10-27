# Benchmark: working with and without Reins

What does an agent gain from handing its long work to Reins? We ran the same agent on the same tasks
twice, once as it ships and once with Reins installed, and measured scores, model requests, money,
where the wall time went, and what happened when the agent process died.

**Short answer.** Reins does not make a model solve tasks better, and on a wait of a few hours it costs
about the same. On a wait of days it is far cheaper, because a polling agent pays for every hour while
Reins pays per event, and the work survives the agent process dying.

## Setup

| | |
| --- | --- |
| Harnesses | Codex CLI 0.162.1 (`codex exec --json --enable unified_exec`, no sandbox inside the container); Pi 0.87.1 (`pi --print`) |
| Model | Kimi K3 through an OpenAI-compatible gateway, the same in both arms |
| Tasks | [Terminal-Bench 4.0](https://github.com/harbor-framework/terminal-bench) (commit 452bf30) long tasks run with Harbor: `ctr-optimization` (a 48-simulated-hour ad campaign on a real clock, 4.8 h), `lake-temp-glm` (CPU LSTM training), `wdm-design` (Meep photonic inverse design); and `ctr-3day`, the same campaign with each simulated hour lasting 1.5 real hours, so one run takes 72 hours |
| Arms | **without Reins**: the harness as it ships. **with Reins**: the same, plus Reins (screen backend), its skill for that harness, and one sentence in the system prompt saying Reins exists |
| Trial end | without Reins: when the agent exits. With Reins: when the agent has exited and Reins has no task, callback or resumed turn left |
| Fault (3-day task) | 60 hours after the agent first ran, every `codex exec` process in each arm's container was killed with SIGKILL, at the same moment in both arms |
| Money | every model request priced on its own at list price: input ¥20, cache-hit input ¥2, output ¥100 per million tokens (reasoning included in output) |

## The 3-day task

![Money and model requests over 72 hours](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-longrun-light.svg)

| First 60 hours | without Reins | with Reins |
| --- | ---: | ---: |
| Model requests | 911 | 90 |
| Uncached input tokens | 1.44 M | 0.97 M |
| Cache-hit input tokens | 121.4 M | 4.2 M |
| Output tokens | 153 k | 73 k |
| Money (CNY) | **286.78** | **35.17** |
| Wall time with the agent alive, waiting | 3,427 min (95%) | 2 min |
| Wall time with no agent process | 0 | 3,669 min (99%) |
| Callbacks / median time from task end to resumed turn | – | 14 / 2 s |
| At the 60 h fault | its only session was killed; the trial ended and the task failed | no agent process was running; nothing to kill |

Without Reins, the agent waited the way agents wait: it kept one Codex session alive for 60 hours and
checked the campaign by polling a running command or looping `sleep`. Every poll is a model request that
rereads a context that keeps growing, so the money grows faster than the hours. With Reins, each wait
was submitted as `reins run -- sleep N`; the agent ended its turn and Reins resumed the same thread when
the wait ended, so money grows with the number of decisions, not with time.

One cost goes the other way: after a long idle gap the provider's prompt cache is partly gone, so a
resumed turn pays more uncached input. In this run 80% of the Reins arm's input was a cache hit, against
99% for the polling agent; Reins still cost an eighth as much because it made a tenth of the requests.
On a provider whose cache expires within minutes, every resume would reread the context at full price.

## Regular Terminal-Bench tasks

![Where the wall time went](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-time-light.svg)

The cleanest pair is Codex on `ctr-optimization` (4.8 hours, real clock): same 290 minutes, same 42
minutes of model time.

| ctr-optimization, Codex | without Reins | with Reins |
| --- | ---: | ---: |
| Model requests | 164 | 134 |
| Polls (`write_stdin` on a running command, foreground `sleep`) | 67 + 31 | 13 + 0 |
| Wall time with the agent alive, waiting | 244 min (84%) | 2 min |
| Wall time with no agent process | 0 | 242 min (83%) |
| Callbacks / median resume | – | 18 / 3 s |
| Money (CNY) | 30.59 | 30.11 |
| Score | 0 | 0 |

At this length the money is equal: Reins saved 18% of the requests, and each resume rereads the
context, which takes the saving back.

Across all 18 Terminal-Bench 4 trials (three tasks, both arms, Pi twice and Codex once) two passed, one
in each arm, both on `lake-temp-glm` with Pi. The others failed on the model's own design choices
(a click farm counted as real traffic, an RMSE just over the limit, a photonic design below the required
transmission). Nothing here suggests that Reins changes the pass rate, and one run per cell could not
show it if it did.

## Did the agent use Reins?

Not always. With a focused skill and one line in the system prompt, the Reins arm used Reins in two of
three tasks per round. Failure modes we saw: the agent never opened the skill and managed its own
background jobs with `nohup`; it used Reins for the first hour and then went back to foreground sleeps
as the session grew; it used Reins to launch jobs but kept polling their logs. Adoption, not the
mechanism, is the weak point.

## What the runs fixed

The benchmark found five defects in Reins (then Agent Traffic Control); all are fixed in 0.9.0 with
regression tests:

- A Pi callback that reached the session but was never ACKed kept the session's lease, so every later
  callback waited for manual recovery (live delivery and the offline managed resume).
- Codex sessions started outside a git repository could not be resumed (`codex exec resume` applies its
  first-run trust check); resume now passes `--skip-git-repo-check`.
- A callback for a Codex thread whose original process was still running counted as a failed delivery
  and used up its retries; it now waits for the thread to be free.
- Codex got the 800-line reference as its skill and, in practice, blocked on `wait` commands; it now has
  a focused page: submit, then end the turn.
- Input piped into `reins run` (a heredoc script) left the task waiting on a terminal; it now becomes the
  task's stdin.

After these fixes, the Codex round lost or stranded no callback.

## Caveats

- One run per cell and one model; differences in score between arms are noise at this size.
- Money is at list price for the model; the gateway's actual billing may differ.
- The fault is one kind (the agent process killed). Container restarts, network loss and host reboots
  were not tested here.
- The without-Reins arm had no supervisor to restart a crashed session; with one, it would have lost
  the session's context instead of the task.
