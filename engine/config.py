# -*- coding: utf-8 -*-
"""全局配置：环境变量 + CLI 参数（时间参数可覆盖，便于本地 mock 演示）。"""
import os


class Config:
    def __init__(self, mock=False, t20=1200, t30=1200, t40=600,
                 poll=10, watcher_tick=5, slots=3, start_wait=120,
                 bb_dir=None, logs_dir=None, only=None, t20_easy=600,
                 t20_hard=1800, t30_easy=900, t30_hard=1500,
                 t20_multi_easy=1800, t20_multi_medium=2400, t20_multi_hard=3000,
                 t30_multi_easy=1200, t30_multi_medium=2100, t30_multi_hard=2400):
        self.mock = mock
        # 平台凭证（真实模式必需；mock 模式不需要）
        self.token = os.environ.get("BENCHMARK_TOKEN", "")
        self.base_url = (os.environ.get("BENCHMARK_BASE_URL", "")).rstrip("/")
        # 项目根目录：本文件在 engine/ 下，根是上一级
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.blackboard_dir = bb_dir or os.path.join(self.root, "blackboard")
        self.logs_dir = logs_dir or os.path.join(self.root, "agent_logs")
        self.prompts_dir = os.path.join(self.root, "agent_prompts")
        # 时间参数（秒）
        self.t20 = t20                        # 看提示阈值：medium 1200s（20min）
        self.t20_easy = t20_easy              # 看提示阈值：easy 600s（10min）
        self.t20_hard = t20_hard              # 看提示阈值：hard 1800s（30min）
        self.t20_multi_easy = t20_multi_easy    # 看提示阈值：多flag+easy 1800s（30min）
        self.t20_multi_medium = t20_multi_medium  # 看提示阈值：多flag+medium 2400s（40min）
        self.t20_multi_hard = t20_multi_hard    # 看提示阈值：多flag+hard 3000s（50min）
        self.t30 = t30                        # 二次解题后强制停止：medium 1200s（20min）
        self.t30_hard = t30_hard              # 二次解题后强制停止：hard 1500s（25min）
        self.t30_easy = t30_easy              # 二次解题后强制停止：easy 900s（15min）
        self.t30_multi_easy = t30_multi_easy    # 二次解题后强制停止：多flag+easy 1200s（20min）
        self.t30_multi_medium = t30_multi_medium  # 二次解题后强制停止：多flag+medium 2100s（35min）
        self.t30_multi_hard = t30_multi_hard    # 二次解题后强制停止：多flag+hard 2400s（40min）
        self.t40 = t40                        # 保留兼容（兜底 = 看提示阈值 + 二次解题期限）
        self.poll_interval = poll             # 通关检测轮询间隔
        self.watcher_tick = watcher_tick      # 黑板监视间隔
        self.max_slots = slots                # 平台并发容器上限（3）
        self.start_wait_timeout = start_wait  # start 后等 available 超时
        self.max_attempts = 3                 # 每题最多 spawn 次数
        self.only = list(only) if only else []  # 限定题目范围（空 = 全部）
        self.status_path = os.path.join(self.root, "engine_status.json")
        self.events_path = os.path.join(self.root, "events.jsonl")
