# QueenBee

面向攻防评测场景的多 agent 调度引擎，以 Docker 镜像交付，容器启动后自主完成评测全流程。

引擎不参与解题。解题由独立运行的 Claude Code 进程完成；引擎负责容器生命周期、agent 派发与回收、超时控制与情报交接。

## 设计原则

1. **编排优先**：复用成熟的 agent 工具与技能库，不重复实现解题能力；引擎只做确定性调度。
2. **双 agent 并行**：每道题运行两名 agent。worker 加载技能库、按既有方法论推进；scout 不施加约束，独立发挥，对黑板只读。
3. **状态外置**：agent 的中间成果全部写入黑板（文件系统），更换 agent 时上下文经黑板摘要交接，不损失信息。
4. **代码级确定性**：超时、换人、收尾等策略由引擎代码执行，不依赖模型自觉。

## 架构

```
scheduler_engine.py      # 入口 + 主循环
engine/
├── config.py            # 环境变量与时间参数
├── tsec_adapter.py      # 平台 API（urllib，错误码分类与退避重试）
├── mock_adapter.py      # 本地演示用假平台
├── agent_manager.py     # agent 进程管理（spawn/kill，POSIX 进程组回收）
├── blackboard.py        # 题级黑板读写与活动度统计
├── watcher.py           # 黑板监视（活动度 / GIVE_UP / 提交事件）
└── scheduler.py         # 选题排序与同网段判断
agent_prompts/
├── default.md           # worker 模板（技能约束）
└── default2.md          # scout 模板（零约束，只读黑板）
monitor.py + web/        # 本地监控面板（:8000）
mock_agent/              # 本地演示用剧本 agent
tests/                   # 单元测试（32 项）
docker/                  # 镜像构建脚本（pro / flash）
```

## 核心机制

- **双 agent 与停止计数**：worker 第一次停止（自然退出或主动放弃）换人，scout 不受影响；worker 第二次停止则整题放弃，回收全部 agent 与容器。
- **换人前查平台**：agent 退出后先同步查询平台状态，已通关则直接回收，不再派发多余 agent。
- **通关检测**：每 10 秒轮询平台，flag 集齐即回收该题全部进程并关闭容器，补位下一题。
- **难度感知超时漏斗**：

| 题目 | 无进展触发提示与二次解题 | 二次解题后仍未产出则放弃 | 兜底（前两项之和） |
|------|------------------------|------------------------|-------------------|
| easy | 15 分钟 | 30 分钟 | 45 分钟 |
| medium | 30 分钟 | 30 分钟 | 60 分钟 |
| hard | 40 分钟 | 30 分钟 | 70 分钟 |
| 多 flag | 50 分钟 | 40 分钟 | 90 分钟 |

- **情报共享**：同网段题目的侦察结果与发现摘要并入新 agent 的提示词。
- **尾声补题**：所有题目处理完后，从跳过清单按黑板情报量挑一道补做，每题一次。

## 快速开始

本地演示（假平台 + 剧本 agent，不联网）：

```bash
python scheduler_engine.py --mock --slots 2 --poll 2 --tick 1 --t20 8 --t30 8
```

真实模式（平台注入环境变量 BENCHMARK_TOKEN / BENCHMARK_BASE_URL）：

```bash
python scheduler_engine.py
```

监控面板：

```bash
python monitor.py            # 浏览器打开 http://127.0.0.1:8000
```

### 命令行参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--mock` | 关 | 本地演示模式 |
| `--slots` | 3 | 并发槽位 |
| `--poll` | 10 | 通关检测轮询间隔（秒） |
| `--tick` | 5 | 黑板监视间隔（秒） |
| `--t20-easy` / `--t20` / `--t20-hard` / `--t20-multi` | 900 / 1800 / 2400 / 3000 | 各档看提示阈值（秒） |
| `--t30` / `--t30-multi` | 1800 / 2400 | 二次解题期限（秒） |
| `--t40` | 600 | 兼容保留 |
| `--only` | 无 | 只解指定题目（逗号分隔） |
| `--bb` | 项目下 blackboard/ | 黑板目录 |
| `--start-wait` | 120 | 容器就绪等待上限（秒） |

## 测试

```bash
python -m unittest discover -s tests -t .
```

## 镜像构建

```bash
bash docker/build-claude-engine.sh        # pro：deepseek-v4-pro[1m]
bash docker/build-claude-engine-flash.sh  # flash：deepseek-v4-flash[1m]
```

构建含两道硬校验（工具链与镜像自检），产物 tar.gz 上传平台托管运行。构建前置依赖（pwnkit、skills-all）见 `docker/README.md`。

## 运行产物

- `blackboard/{challenge_id}/` — 每题档案（facts / recon / hint / progress）
- `agent_logs/` — agent 进程日志
- `queenbee.log` — 引擎日志
- `engine_status.json` / `events.jsonl` — 监控快照与事件流
- `summary.md` — 跑分总结
