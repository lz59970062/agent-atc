---
hide:
  - toc
---

# Benchmark: long work, handed to Reins

<p class="reins-lead">Same agent, same model, same task. The only difference: while the work runs, does the agent stay up and watch it, or does Reins?</p>

<div class="reins-stats" markdown>
<div class="reins-stat" markdown>
**8×**
*cheaper*
<span>first 60 h of a 3-day task: ¥35 vs ¥287</span>
</div>
<div class="reins-stat" markdown>
**10×**
*fewer model requests*
<span>90 vs 911</span>
</div>
<div class="reins-stat" markdown>
**2 s**
*back in the same session*
<span>median time from task end to resumed turn</span>
</div>
<div class="reins-stat" markdown>
**72 h**
*finished despite the kill*
<span>the run without Reins failed on the spot</span>
</div>
</div>

## A 3-day job, and the agent can sleep through it

![Over 60 hours the agent without Reins made 911 requests and spent 287 yuan before it was killed; with Reins it made 90 requests and spent 35 yuan](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-longrun-light.svg#only-light)
![Over 60 hours the agent without Reins made 911 requests and spent 287 yuan before it was killed; with Reins it made 90 requests and spent 35 yuan](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-longrun-dark.svg#only-dark)

Without Reins the agent has to keep watch itself: one session stayed open for 60 hours, polling, making 911
model requests and spending ¥287 — and when its process was killed, the task went with it.

With Reins the agent ends its turn after each step, and Reins calls it back into the same session when there
is something to act on. The same 60 hours cost ¥35. When every agent process was killed at hour 60 there was
no agent running to kill, and the task carried on to finish all 72 hours for ¥49.64 in total.

## While it waits, the agent isn't running

![Share of wall time: without Reins the agent is mostly alive and waiting; with Reins there is mostly no agent process at all](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-time-light.svg#only-light)
![Share of wall time: without Reins the agent is mostly alive and waiting; with Reins there is mostly no agent process at all](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-time-dark.svg#only-dark)

Without Reins the agent spends 84–95% of the run alive and waiting, and every look at the progress is a model
call. With Reins there is no agent process at all for 83–99% of it: no model calls, and nothing to crash.

## Short jobs don't pay extra

On a regular 4.8-hour task both arms cost the same (¥30.11 vs ¥30.59), polling drops from 98 to 13, and results
reach the session in a median 3 seconds. The longer the wait, the more Reins saves.

| | Without Reins | With Reins |
| --- | ---: | ---: |
| 3-day task, cost of the first 60 h | ¥286.78 | **¥35.17** |
| 3-day task, model requests in the first 60 h | 911 | **90** |
| After every agent process was killed at 60 h | task failed | **finished all 72 h** |
| 4.8-hour task, cost | ¥30.59 | **¥30.11** |
| 4.8-hour task, polls | 98 | **13** |

??? note "How we measured"

    - **Agent**: Codex CLI 0.162.1 with Kimi K3, identical in both arms except for Reins being installed.
    - **Tasks**: Terminal-Bench 4 `ctr-optimization` (an ad campaign on a real clock, 4.8 hours), and a 3-day version with the clock slowed 15×.
    - **Fault**: at hour 60, every Codex process in both containers was killed at the same moment.
    - **Cost**: every model request priced at list price: input ¥20, cache-hit input ¥2, output ¥100 per million tokens.

<p class="reins-scope">Reins changes what waiting costs and whether the work survives, not how well the model solves the task: both arms scored the same on these tasks.</p>
