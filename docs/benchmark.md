---
hide:
  - toc
---

# 实测：长任务交给 Reins

<p class="reins-lead">同一个 Agent、同一个模型、同一个任务。唯一的区别是：等待的时候，Agent 自己守着，还是交给 Reins。</p>

<div class="reins-stats" markdown>
<div class="reins-stat" markdown>
**8×**
*更省钱*
<span>3 天任务前 60 小时：¥35 对比 ¥287</span>
</div>
<div class="reins-stat" markdown>
**10×**
*更少模型请求*
<span>90 次对比 911 次</span>
</div>
<div class="reins-stat" markdown>
**2 秒**
*结果回到原会话*
<span>任务结束到会话恢复的中位延迟</span>
</div>
<div class="reins-stat" markdown>
**72 h**
*进程被杀也跑完*
<span>不用 Reins 的那一组当场失败</span>
</div>
</div>

## 三天的任务，Agent 不必醒着

![60 小时内，不用 Reins 的 Agent 发出 911 次请求、花费 287 元后被杀；用 Reins 只发出 90 次请求、花费 35 元](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-longrun-zh-light.svg#only-light)
![60 小时内，不用 Reins 的 Agent 发出 911 次请求、花费 287 元后被杀；用 Reins 只发出 90 次请求、花费 35 元](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-longrun-zh-dark.svg#only-dark)

不用 Reins，Agent 只能自己守着：一个会话开了 60 小时，不停轮询，发出 911 次模型请求、花掉 ¥287，
进程一被杀，任务也就没了。

用 Reins，Agent 每做完一步就结束回合，有结果时 Reins 把它叫回同一个会话。同样 60 小时只花 ¥35；
第 60 小时杀掉所有 Agent 进程时，根本没有 Agent 在运行，任务照常跑完全部 72 小时，总共 ¥49.64。

## 等待的时候，Agent 不在运行

![墙钟时间占比：不用 Reins 时大部分时间 Agent 活着干等；用 Reins 时大部分时间没有任何 Agent 进程](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-time-zh-light.svg#only-light)
![墙钟时间占比：不用 Reins 时大部分时间 Agent 活着干等；用 Reins 时大部分时间没有任何 Agent 进程](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/benchmark-time-zh-dark.svg#only-dark)

没有 Reins，Agent 84%–95% 的时间都活着干等，每次查看进度都是一次模型调用。有了 Reins，83%–99% 的时间里
根本没有 Agent 进程：不耗模型调用，也不怕进程崩溃。

## 短任务也不吃亏

在 4.8 小时的常规任务上，两边花费持平（¥30.11 对 ¥30.59），轮询从 98 次降到 13 次，结果回到原会话的中位延迟只有 3 秒。
等待越长，Reins 省得越多。

| | 不用 Reins | 用 Reins |
| --- | ---: | ---: |
| 3 天任务，前 60 小时花费 | ¥286.78 | **¥35.17** |
| 3 天任务，前 60 小时模型请求 | 911 | **90** |
| 第 60 小时杀掉 Agent 进程后 | 任务失败 | **跑完 72 小时** |
| 4.8 小时任务花费 | ¥30.59 | **¥30.11** |
| 4.8 小时任务轮询次数 | 98 | **13** |

??? note "怎么测的"

    - **Agent**：Codex CLI 0.162.1 + Kimi K3，两组完全相同，唯一区别是是否安装 Reins。
    - **任务**：Terminal-Bench 4 的 `ctr-optimization`（按真实时钟推进的广告投放，4.8 小时），以及把时钟放慢 15 倍的 3 天版本。
    - **故障**：第 60 小时，同时杀掉两组容器里所有的 Codex 进程。
    - **花费**：每次模型请求按标价单独计算：输入 ¥20、缓存命中 ¥2、输出 ¥100 / 百万 token。

<p class="reins-scope">Reins 改变的是等待的成本和可靠性，不改变模型解题的能力：两组在这些任务上的得分相同。</p>
