# QueenBee · TSecBench 智能调度引擎 v2

以 Docker 镜像形式部署到 TSecBench 平台的自动跑分调度引擎。

**代号 QueenBee（蜂后）**：蜂后只调度不下场；工蜂（worker）按 Skill 方法论采蜜、斥候蜂（scout）自由探路只读蜂巢；蜜（flag）入蜂巢（黑板）。引擎管容器、管进程、管黑板、管定时器、管通关检测；解题完全交给 one-shot Claude agent，agent 自己 curl 提交 flag，引擎轮询平台检测通关后 kill + close 换题。

## 架构

```
scheduler_engine.py      ← 入口 + 主循环
engine/
├── config.py            环境变量 + 时间参数
├── tsec_adapter.py      平台 API（纯标准库 urllib + 错误码映射）
├── mock_adapter.py      假平台（--mock 演示用）
├── agent_manager.py     spawn/kill one-shot claude -p 子进程
├── blackboard.py        黑板（每题独立目录）读写 + 活动度
├── watcher.py           黑板监视（活动度 / GIVE_UP / mock 提交扫描）
└── scheduler.py         纯策略：选题排序、同网段判断
mock_agent/mock_agent.py 模拟解题 agent（--mock 演示用）
agent_prompts/default.md agent 提示词模板（引擎填充变量）
tests/                   单元测试（标准库 unittest，32 个用例）
blackboard/              黑板（运行时生成，每题一个目录）
agent_logs/              agent 进程日志（运行时生成）
```

## 设计要点（与方案 v3.2 对应）

- **agent 只写黑板**（facts.md / recon.json），不读任何文件；所有"读"发生在 spawn 时刻（引擎把黑板摘要附进 prompt）
- **agent 自己 curl 提交 flag**（命令内嵌 prompt，真实 URL/token 已填充），不经过引擎
- **通关检测 = 轮询 list**：flag 全齐 → kill 该题全部 agent；容器还活着则 close
- **超时漏斗**：20min 无活动 → 拉 hint（扣分）+ 双线新 agent；30min（带 hint 后）无新 flag → skip；40min 强制 skip
- **二次解题**：agent 自然退出/GIVE_UP → 用最新黑板内容重新 spawn（每题最多 3 次）
- **尾声补题**：槽位空且无未开始题 → 从 skip 清单挑 facts 最丰富的题补做一次
- **零第三方依赖**：纯 Python 标准库

## 快速开始

### 本地演示（--mock：假平台 + 假 agent，不联网、不扣分）

```bash
# 压缩时间参数，几十秒看完完整流程：
# 通关 / 提示双线 / GIVE_UP 二次解题 / skip / 尾声补题
python scheduler_engine.py --mock --slots 2 --poll 2 --tick 1 --t20 8 --t30 8 --t40 25
```

### 真实模式（容器内，平台注入环境变量）

```bash
# 需要环境变量（平台托管模式自动注入）：
#   BENCHMARK_TOKEN / BENCHMARK_BASE_URL（平台）
#   ANTHROPIC_AUTH_TOKEN（claude 子进程继承）
python scheduler_engine.py
```

真实模式下会：健康检查（带 token 的 list）→ 读题目列表 → 按 easy→hard、低分→高分排序 → 3 槽位并发调度。

### 命令行参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--mock` | 关 | 本地演示模式 |
| `--slots` | 3 | 并发槽位（平台上限 3） |
| `--t20` | 1200 | 20min 检查点（秒） |
| `--t30` | 1800 | 30min 终局（秒） |
| `--t40` | 2400 | 40min 强制终止（秒） |
| `--poll` | 10 | 通关检测轮询间隔（秒） |
| `--tick` | 5 | 黑板监视间隔（秒） |
| `--start-wait` | 120 | start 后等容器就绪超时（秒） |
| `--bb` | 项目下 blackboard/ | 黑板目录 |

## 测试

```bash
python -m unittest discover -s tests -t .
```

## 产物

- `blackboard/{challenge_id}/` — 每题完整档案（facts/recon/hint/progress）
- `agent_logs/` — 各 agent 进程日志
- `engine.log` — 引擎日志
- `summary.md` — 跑分总结（通关数、估算得分）

## 集成进镜像（P4）

构建脚本 `build-claude-run.sh` 的 `[5/8]` 增加：

```bash
docker cp "$ROOT/scheduler_engine.py" $CNAME:/workspace/
docker cp "$ROOT/engine"             $CNAME:/workspace/engine
docker cp "$ROOT/agent_prompts"      $CNAME:/workspace/agent_prompts
```

启动：`python3 /workspace/scheduler_engine.py`
