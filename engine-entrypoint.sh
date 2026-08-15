#!/bin/bash
# engine-entrypoint.sh - 智能调度引擎容器入口（托管模式：平台注入环境变量，启动即跑分）
set -u

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "=== QueenBee 智能调度引擎 v2 启动 ==="
log "python3: $(python3 --version 2>/dev/null || echo missing)"

MISSING=""
[ -z "${BENCHMARK_TOKEN:-}" ] && MISSING="$MISSING BENCHMARK_TOKEN"
[ -z "${BENCHMARK_BASE_URL:-}" ] && MISSING="$MISSING BENCHMARK_BASE_URL"
[ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] && MISSING="$MISSING ANTHROPIC_AUTH_TOKEN"
if [ -n "$MISSING" ]; then
  log "!!! 缺少必需环境变量:$MISSING"
  sleep 30
  exit 1
fi

# VPN 预检（托管模式平台已打通，仅记录不做硬阻断）
if curl -s -m 5 http://10.0.100.58 2>/dev/null | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
  log "VPN 预检通过"
else
  log "VPN 预检不可达（托管模式可忽略，继续）"
fi

log "蜂后启动（python3 /workspace/scheduler_engine.py）..."
exec python3 /workspace/scheduler_engine.py
