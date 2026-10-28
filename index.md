A harness for your harnesses

# Harness Reins

把训练、测试、长时间实验和子 Agent 交给独立的系统进程托管。Agent 不必守着等待；任务一结束，结果就回到发起它的那个会话。Codex、Claude Code、Pi 都能用。

[开始使用](quickstart.md)
[看实测数据](benchmark.md)


**8×**
*更省钱*
3 天任务前 60 小时：¥35 对比 ¥287

**72 h**
*进程被杀也跑完*
不用 Reins 的那一组当场失败

**2 秒**
*结果回到原会话*
任务结束到会话恢复的中位延迟


## 一个任务，五步走完

![提交、托管、运行、排队、恢复五个步骤；还有下一阶段时，Agent 再提交一次](https://raw.githubusercontent.com/lz59970062/harness-reins/main/assets/readme/workflow-zh-light.svg)

在 Agent 会话里提交一条训练命令：

```bash
reins run --cwd "$PWD" --task "训练模型" -- python train.py --config configs/exp.yaml
```

Reins 立刻返回任务 ID 和日志路径，Agent 结束回合。任务结束后，同一个会话收到回调，Agent 检查输出并执行回调里的 ACK 命令。
第一次使用可以先按[快速开始](quickstart.md)跑一个十秒钟的示例。

## 你想做什么


-   **运行长命令**

    训练、测试、构建、数据处理，跑完自动回到会话。

    [运行命令](guides/run.md)

-   **启动子 Agent**

    把一块独立的工作交给另一个 Agent，结论送回你这里。

    [启动子 Agent](guides/agents.md)

-   **接入已有任务**

    Slurm、tmux 里已经在跑的任务，结束时同样回调。

    [接入已有任务](guides/external-jobs.md)

-   **复用任务模板**

    把审查、分析的提示词写成模板，一条命令派发。

    [编写任务模板](guides/templates.md)

-   **跟踪多阶段目标**

    训练、评估、写报告串成一个目标，每一阶段都有核对。

    [跟踪多阶段目标](guides/goals.md)

-   **看实测数据**

    3 天任务省 8 倍，进程被杀也照样跑完。

    [基准测试](benchmark.md)


## 接入你的环境

Reins 支持 Linux、macOS 和 Windows。按[安装指南](installation.md)配置运行环境，再选择你的 Agent：
[Codex CLI](integrations/codex.md)、[Claude Code](integrations/claude.md)、[Pi](integrations/pi.md) 或 [Codex Desktop](integrations/desktop.md)。
[Harness 支持情况](integrations/support.md)列出了各接入方式的版本和回调路径，[典型使用场景](guides/use-cases.md)给出了完整的工作流程示例。

## 继续阅读

[架构概览](overview.md)介绍一次任务从提交到回调的全过程。[命令速查](reference/cli.md)方便随用随查，遇到问题时查看[常见问题](troubleshooting.md)。
模块说明、平台实现与验证记录收录在[开发资料](reference/engineering.md)。
