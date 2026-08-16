#!/bin/bash
# build-claude-engine-flash.sh - 引擎版镜像构建（flash 模型版）
# 与 build-claude-engine.sh 完全相同，仅模型配置换成 flash（deepseek-v4-flash[1m]），
# 产物独立命名。包含两道硬校验（[2/8] 工具链 + [7.5/8] 镜像自检）。
# 产物：agent-engine-flash-v1.tar.gz
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER_DIR="$ROOT/docker"
CNAME=tsecbench-engine-flash-build
IMG=tsecbench-agent-engine-flash-v1:latest
OUT="$ROOT/agent-engine-flash-v1.tar.gz"
PROXY_ENV="-e HTTP_PROXY=http://http.docker.internal:3128 -e HTTPS_PROXY=http://http.docker.internal:3128 -e NO_PROXY=localhost,127.0.0.1"

echo "===== 引擎版镜像逐层构建（flash 模型，run+commit，exec 注入代理）====="

docker rm -f $CNAME 2>/dev/null || true
echo "[1/8] 启动基础容器..."
docker run -d --name $CNAME node:22-slim sleep 7200 >/dev/null

echo "[2/8] apt 换阿里源 + 装 17 个渗透工具（走代理，约 5-8 分钟）..."
docker exec $PROXY_ENV $CNAME bash -c "
sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
  || sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list
apt-get update && apt-get install -y --no-install-recommends --fix-missing \
  curl wget python3 python3-pip python3-boto3 git ripgrep netcat-openbsd socat \
  nmap sqlmap john dirb dnsutils tcpdump jq ca-certificates procps unzip openssl
pip3 install --break-system-packages dirsearch
rm -rf /var/lib/apt/lists/*
echo APT_DONE
"
docker exec $CNAME bash -c "python3 --version && curl --version | head -1" \
  || { echo "!!! [2/8] 工具链校验失败：python3/curl 缺失"; docker rm -f $CNAME >/dev/null; exit 1; }

echo "[3/8] npm 装 claude-code..."
docker exec $PROXY_ENV $CNAME bash -c "npm install -g @anthropic-ai/claude-code && echo NPM_DONE"

echo "[4/8] 建 agent 用户 + 目录..."
docker exec $CNAME bash -c "
useradd -m -s /bin/bash agent
mkdir -p /home/agent/.claude /workspace/.opencode
"

echo "[5/8] 复制配置/技能库/引擎代码/工具..."
docker cp "$DOCKER_DIR/claude-settings.json" $CNAME:/home/agent/.claude/settings.json
docker cp "$DOCKER_DIR/skills-all" $CNAME:/home/agent/.claude/skills
docker cp "$ROOT/pwnkit" $CNAME:/workspace/.opencode/
docker cp "$ROOT/scheduler_engine.py" $CNAME:/workspace/scheduler_engine.py
docker cp "$ROOT/engine" $CNAME:/workspace/engine
docker cp "$ROOT/agent_prompts" $CNAME:/workspace/agent_prompts
docker cp "$ROOT/engine-entrypoint.sh" $CNAME:/workspace/engine-entrypoint.sh
docker exec $CNAME bash -c "
chmod +x /workspace/engine-entrypoint.sh
chown -R agent:agent /home/agent/.claude /workspace
"

echo "[6/8] nuclei 模板预下载（容忍失败）..."
docker exec -u agent $PROXY_ENV $CNAME bash -c "nuclei -update-templates -silent 2>/dev/null || true"

echo "[7/8] commit 为镜像（USER agent / WORKDIR /workspace / ENTRYPOINT=引擎）..."
docker commit \
  --change='USER agent' \
  --change='WORKDIR /workspace' \
  --change='ENTRYPOINT ["/workspace/engine-entrypoint.sh"]' \
  $CNAME $IMG
docker rm -f $CNAME >/dev/null

echo "[7.5/8] 镜像自检（python3/curl/claude 必须存在）..."
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint bash $IMG -c \
  "which python3 curl claude" | grep -q python3 \
  || { echo "!!! 镜像自检失败：缺少 python3/curl/claude"; exit 1; }

echo "[8/8] 导出 tar.gz..."
docker save $IMG | gzip > "$OUT"
ls -lh "$OUT"

echo ""
echo "✅ 完成！上传文件: $OUT"
echo "   平台「运行时环境变量」需配置: ANTHROPIC_AUTH_TOKEN = 你的 DeepSeek key"
echo "   （BENCHMARK_TOKEN / BENCHMARK_BASE_URL 由平台自动注入）"
