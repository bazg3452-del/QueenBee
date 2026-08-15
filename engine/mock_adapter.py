# -*- coding: utf-8 -*-
"""Mock 平台：本地演示（--mock）用，不联网、不扣分、不动真实靶场。

行为与真实平台一致：
- 同时最多 max_slots 个 available 容器（由引擎保证，这里不额外限制）
- submit_flag 由 watcher 在 mock 模式下调用（模拟"agent 自己 curl 提交"）
- flag 全提交 -> is_completed = True
"""
import copy
import threading
import time


class MockAdapter:
    def __init__(self, max_slots=3):
        self.lock = threading.Lock()
        self.max_slots = max_slots
        self._ip_n = 0
        self.hint_calls = {}   # cid -> 次数
        self.submits = []      # 提交记录
        self.challenges = [
            {"unique_code": "mock-01", "description": "模拟题1：后台登录弱口令（easy）",
             "difficulty": "easy", "level": 1, "total_score": 100, "flag_count": 1,
             "correct_flag_count": 0, "is_completed": False,
             "container_status": "stopped", "container_addr": []},
            {"unique_code": "mock-02", "description": "模拟题2：SQL 注入拖库（medium）",
             "difficulty": "medium", "level": 1, "total_score": 300, "flag_count": 1,
             "correct_flag_count": 0, "is_completed": False,
             "container_status": "stopped", "container_addr": []},
            {"unique_code": "mock-03", "description": "模拟题3：双 flag 组件漏洞（hard）",
             "difficulty": "hard", "level": 1, "total_score": 500, "flag_count": 2,
             "correct_flag_count": 0, "is_completed": False,
             "container_status": "stopped", "container_addr": []},
        ]

    def _get(self, code):
        for c in self.challenges:
            if c["unique_code"] == code:
                return c
        raise KeyError(code)

    # ---------- 平台接口 ----------
    def list_challenges(self):
        with self.lock:
            return copy.deepcopy(self.challenges)

    def start_challenge(self, unique_code):
        with self.lock:
            c = self._get(unique_code)
            if c["container_status"] == "available":
                return {"unique_code": unique_code, "container_addr": c["container_addr"]}
            avail = sum(1 for x in self.challenges if x["container_status"] == "available")
            if avail >= self.max_slots:
                raise ActiveLimitStub(unique_code)
            self._ip_n += 1
            c["container_status"] = "available"
            c["container_addr"] = [f"10.99.{self._ip_n}.1:80"]
            return {"unique_code": unique_code, "container_addr": c["container_addr"]}

    def close_challenge(self, unique_code):
        with self.lock:
            c = self._get(unique_code)
            c["container_status"] = "stopped"
            c["container_addr"] = []
            return {"unique_code": unique_code, "closed": True}

    def get_hint(self, unique_code):
        with self.lock:
            self.hint_calls[unique_code] = self.hint_calls.get(unique_code, 0) + 1
            return {"unique_code": unique_code,
                    "hint": f"[模拟提示] 试试默认口令 admin/admin（第 {self.hint_calls[unique_code]} 次拉取，已扣分）"}

    def submit_flag(self, unique_code, flag):
        """mock 模式下由 watcher 调用，模拟 agent 自己 curl 提交。"""
        with self.lock:
            c = self._get(unique_code)
            self.submits.append({"code": unique_code, "flag": flag, "ts": time.time()})
            if c["correct_flag_count"] < c["flag_count"]:
                c["correct_flag_count"] += 1
                if c["correct_flag_count"] == c["flag_count"]:
                    c["is_completed"] = True
            per_flag = c["total_score"] // c["flag_count"]
            return {"correct": True, "awarded": per_flag,
                    "cumulative_score": c["correct_flag_count"] * per_flag,
                    "correct_flag_count": c["correct_flag_count"],
                    "total_flag_count": c["flag_count"],
                    "matched_flag_index": c["correct_flag_count"]}

    def health_check(self):
        return True


class ActiveLimitStub(Exception):
    """mock 平台模拟 409 容器满（引擎按 ActiveLimitError 一样处置）。"""
    pass
