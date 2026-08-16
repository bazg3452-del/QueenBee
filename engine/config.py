# -*- coding: utf-8 -*-
"""全局配置：环境变量 + CLI 参数（时间参数可覆盖，便于本地 mock 演示）。"""
import os


class Config:
    def __init__(self, mock=False, t20=1800, t30=1800, t40=600,
                 poll=10, watcher_tick=5, slots=3, start_wait=120,
                 bb_dir=None, logs_dir=None, only=None, t20_easy=900,
                 t20_hard=2400, t20_multi=3000, t30_multi=2400):
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
        self.t20 = t20                        # 看提示阈值（无进展）：medium 默认 1200s
        self.t20_easy = t20_easy              # easy 看提示阈值 600s（10min）
        self.t20_hard = t20_hard              # hard 看提示阈值 1800s（30min）
        self.t20_multi = t20_multi            # 多 flag 题看提示阈值 2400s（40min，不分难度）
        self.t30 = t30                        # 二次解题后强制停止：单 flag 1800s（30min）
        self.t30_multi = t30_multi            # 二次解题后强制停止：多 flag 2400s（40min）
        self.t40 = t40                        # 保留兼容（兜底 = 看提示阈值 + 二次解题期限）
        self.poll_interval = poll             # 通关检测轮询间隔
        self.watcher_tick = watcher_tick      # 黑板监视间隔
        self.max_slots = slots                # 平台并发容器上限（3）
        self.start_wait_timeout = start_wait  # start 后等 available 超时
        self.max_attempts = 3                 # 每题最多 spawn 次数
        self.only = list(only) if only else []  # 限定题目范围（空 = 全部）
        self.status_path = os.path.join(self.root, "engine_status.json")
        self.events_path = os.path.join(self.root, "events.jsonl")
