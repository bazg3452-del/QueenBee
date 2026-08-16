# QueenBee Docker 构建

本目录提供 QueenBee 的托管模式镜像构建脚本。

## 文件

- `build-claude-engine.sh` — pro 版（deepseek-v4-pro[1m]），含工具链与镜像双重硬校验
- `build-claude-engine-flash.sh` — flash 版（deepseek-v4-flash[1m]），其余一致
- `claude-settings-pro.json` — 容器内 Claude Code 配置（DeepSeek 网关 + bypassPermissions）

## 构建前置依赖（来自私有项目 blackboard-agent）

构建脚本按 `$ROOT` 相对路径引用以下目录，需与脚本同级准备好：

| 引用 | 说明 |
|------|------|
| `$ROOT/pwnkit` | 解题工具脚本集（recon/sqli/flag/tsecbench/submit） |
| `$ROOT/docker/skills-all` | 技能库（含 37 个 offensive-* 技能） |

## 构建

```bash
bash docker/build-claude-engine.sh
# 产物: agent-engine-deepseek-v2.tar.gz（上传 TSecBench 托管模式，≤3GB）
```

运行时环境变量：平台注入 `BENCHMARK_TOKEN` / `BENCHMARK_BASE_URL`；用户配置 `ANTHROPIC_AUTH_TOKEN`（DeepSeek key）。
