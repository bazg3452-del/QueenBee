# -*- coding: utf-8 -*-
"""黑板监视器（引擎心跳，每 tick 一拍）：
1. 更新 registry 里各 agent 的 last_activity（facts/recon mtime）
2. 检测 GIVE_UP 标记（facts 尾部新内容）-> 提前处置
3. mock 模式：扫描 facts 里的 SUBMITTED_FLAG 行 -> 调 mock 平台 submit（模拟 agent 自己 curl）
"""
import logging
import re
import threading
import time

log = logging.getLogger("queenbee.watcher")

SUBMIT_RE = re.compile(r"SUBMITTED_FLAG:\s*(\S+)")


class Watcher(threading.Thread):
    def __init__(self, engine, tick=5):
        super().__init__(daemon=True, name="watcher")
        self.bee = engine
        self.tick = tick
        self._stop = False
        self._flags_offset = {}   # cid -> 已处理的 facts 字符数（SUBMITTED_FLAG 断点续读）
        self._giveup_offset = {}  # cid -> 已处理的 GIVE_UP 检查偏移

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            try:
                self.tick_once()
            except Exception:
                log.exception("watcher tick 出错")
            time.sleep(self.tick)

    def tick_once(self):
        am = self.bee.agents
        for aid, a in list(am.registry.items()):
            if a["status"] != "running":
                continue
            cid = a["challenge_id"]
            # 1) 活动度
            act = self.bee.bb.activity(cid)
            if act and act > a.get("last_activity", 0):
                a["last_activity"] = act
            # 2) GIVE_UP（只处理新出现的）
            facts = self.bee.bb.read_facts(cid)
            off = self._giveup_offset.get(cid, 0)
            if "GIVE_UP" in facts[off:]:
                self._giveup_offset[cid] = len(facts)
                self.bee.on_agent_give_up(cid)
            else:
                self._giveup_offset[cid] = len(facts)
        # 3) mock 提交扫描
        if self.bee.cfg.mock:
            self.mock_scan()

    def mock_scan(self):
        for aid, a in list(self.bee.agents.registry.items()):
            if a["status"] != "running":
                continue
            cid = a["challenge_id"]
            facts = self.bee.bb.read_facts(cid)
            off = self._flags_offset.get(cid, 0)
            if len(facts) <= off:
                continue
            for m in SUBMIT_RE.finditer(facts[off:]):
                self.bee.on_mock_flag_submitted(cid, m.group(1))
            self._flags_offset[cid] = len(facts)
