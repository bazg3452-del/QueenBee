#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TSecBench 智能调度引擎入口。

引擎只做调度：开题/派人/盯平台/看闹钟/记账。解题交给 one-shot claude agent。

真实模式:
  BENCHMARK_TOKEN=xxx BENCHMARK_BASE_URL=https://... python scheduler_engine.py
本地演示（假平台 + 假 agent，不联网不扣分）:
  python scheduler_engine.py --mock --slots 2 --poll 2 --tick 1 --t20 8 --t30 8 --t40 25
"""
import argparse
import json
import logging
import os
import sys
import time

from engine.agent_manager import AgentManager
from engine.blackboard import Blackboard
from engine.config import Config
from engine.mock_adapter import ActiveLimitStub, MockAdapter
from engine.scheduler import same_subnet, select_next
from engine.tsec_adapter import (ActiveLimitError, AdapterError,
                                 ChallengeNotFoundError,
                                 ResourceUnavailableError, TaskEndedError,
                                 TaskNotFoundError, TSecBenchAdapter)
from engine.watcher import Watcher

log = logging.getLogger("queenbee")

# mock 演示剧本：cid -> {attempt: spec}（模拟不同难度的解题过程）
MOCK_SPECS = {
    "mock-01": {
        1: {"recon_after": 1, "notes": [(3, "尝试 /login 弱口令 admin/admin 成功进入后台")],
            "flag_after": 6, "exit_after": 12},
    },
    "mock-02": {
        # attempt1 磨洋工：20s 才出 flag，先触发 hint 双线；attempt2 带 hint 快速解出
        1: {"recon_after": 1, "notes": [(3, "发现 /api/user?id= 疑似注入点，正在手工验证")],
            "flag_after": 20, "exit_after": 24},
        2: {"recon_after": 1, "notes": [(2, "按提示测试 /login 弱口令")],
            "flag_after": 5, "exit_after": 10},
    },
    "mock-03": {
        # 放弃型：每次都 GIVE_UP，走 二次解题 -> 重试超限 -> skip 全链路
        1: {"recon_after": 1, "notes": [(2, "组件指纹识别失败，找不到可利用点")],
            "give_up_after": 5, "exit_after": 8},
        2: {"recon_after": 1, "notes": [(2, "换思路重试：版本探测仍然失败")],
            "give_up_after": 5, "exit_after": 8},
        3: {"recon_after": 1, "notes": [(2, "最后一次尝试，仍然无解")],
            "give_up_after": 5, "exit_after": 8},
    },
}
DEFAULT_MOCK_SPEC = {"flag_after": 8, "exit_after": 12}


class QueenBee:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bb = Blackboard(cfg.blackboard_dir)
        self.adapter = (MockAdapter(max_slots=cfg.max_slots) if cfg.mock
                        else TSecBenchAdapter(cfg.base_url, cfg.token))
        self.agents = AgentManager(cfg, self.bb)
        self.watcher = Watcher(self, tick=cfg.watcher_tick)
        self.challenges = []
        self.skip = []          # 已放弃的 cid（含原因在 progress 里）
        self.retried = set()    # 尾声补做过一次的 cid
        self.completed = set()  # 已通关处置过的 cid（防 poll 重复触发）
        self.blocked = {}       # cid -> 冷却截止时间（start 503 等暂缓）
        self.meta = {}          # cid -> 题状态
        self.last_poll = time.time()   # 避免第一拍立即 poll 覆盖 --only 过滤
        self.started_once = False
        self.stop = False
        self.summary_path = os.path.join(cfg.root, "summary.md")
        self.status_path = cfg.status_path
        self.events_path = cfg.events_path

    # ================= 状态辅助 =================
    def meta_of(self, cid):
        m = self.meta.setdefault(cid, {
            "first_start": None, "hint_at": None, "progress_seen": None,
            "correct_snapshot": 0, "spawns": 0, "stops": 0,
            "pending_deadline": None, "addr": [],
        })
        if m["progress_seen"] is None:
            m["progress_seen"] = time.time()
        return m

    def _find(self, cid):
        for c in self.challenges:
            if c["unique_code"] == cid:
                return c
        return None

    def _correct_now(self, cid):
        c = self._find(cid)
        return c.get("correct_flag_count", 0) if c else 0

    # ================= 主流程 =================
    def run(self):
        if not self.cfg.mock:
            missing = [k for k in ("BENCHMARK_TOKEN", "BENCHMARK_BASE_URL")
                       if not os.environ.get(k)]
            if missing:
                log.critical("缺少环境变量: %s", ",".join(missing))
                return 2
        os.makedirs(self.cfg.logs_dir, exist_ok=True)
        log.info("QueenBee v2 启动 mock=%s slots=%d poll=%ds 漏斗[看提示 easy=%ds/med=%ds/hard=%ds 多flag[e=%ds/m=%ds/h=%ds] 二次解题后 easy=%ds/med=%ds/hard=%ds 多flag[e=%ds/m=%ds/h=%ds] 兜底=看提示+二次解题]",
                 self.cfg.mock, self.cfg.max_slots, self.cfg.poll_interval,
                 self.cfg.t20_easy, self.cfg.t20, self.cfg.t20_hard,
                 self.cfg.t20_multi_easy, self.cfg.t20_multi_medium, self.cfg.t20_multi_hard,
                 self.cfg.t30_easy, self.cfg.t30, self.cfg.t30_hard,
                 self.cfg.t30_multi_easy, self.cfg.t30_multi_medium, self.cfg.t30_multi_hard)

        # 健康检查（带 token 的 list，失败退避 5 次 × 5s）
        for i in range(5):
            if self.adapter.health_check():
                log.info("健康检查通过（list 接口 200）")
                break
            log.warning("健康检查失败 (%d/5)，5 秒后重试", i + 1)
            time.sleep(5)
        else:
            log.critical("健康检查 5 次失败，退出")
            return 1

        try:
            self.challenges = self.adapter.list_challenges()
        except (TaskNotFoundError, TaskEndedError) as e:
            log.critical("任务不可用: %s", e)
            return 1
        except AdapterError as e:
            log.critical("获取题目列表失败: %s", e)
            return 1

        if self.cfg.only:
            self.challenges = [c for c in self.challenges if c["unique_code"] in self.cfg.only]
            log.info("限定题目范围: %s（%d 道）", ",".join(self.cfg.only), len(self.challenges))
        log.info("题目 %d 道，开始调度", len(self.challenges))
        self._event("start", "引擎启动", mode="mock" if self.cfg.mock else "real",
                    total=len(self.challenges))
        self.watcher.start()
        try:
            while not self.stop:
                self.tick()
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，收尾")
        self.teardown()
        self.write_summary()
        return 0

    def tick(self):
        now = time.time()
        # 1) 超时漏斗
        self.check_timeouts(now)
        # 2) 自然退出 -> 二次解题 / skip
        for aid in self.agents.reap_dead():
            self.on_agent_died(aid)
        # 3) 通关检测（轮询 list）
        if now - self.last_poll >= self.cfg.poll_interval:
            self.last_poll = now
            self.poll_status(now)
        # 4) 补槽位
        self.fill_slots(now)
        # 5) 收工判断
        if self.started_once and not self.agents.running_count() and self._no_work_left():
            log.info("没有可继续的题目，收工")
            self.stop = True
        # 6) 状态快照（监控面板用，每拍原子写）
        self.write_status()

    # ================= 通关检测 =================
    def poll_status(self, now):
        try:
            chs = self.adapter.list_challenges()
        except TaskEndedError:
            log.warning("平台任务已结束，优雅退出")
            self.stop = True
            return
        except TaskNotFoundError as e:
            log.critical("token 无效: %s", e)
            self.stop = True
            return
        except AdapterError as e:
            log.warning("list 失败（下一拍重试）: %s", e)
            return
        if self.cfg.only:   # poll 刷新后保持 --only 过滤
            chs = [c for c in chs if c["unique_code"] in self.cfg.only]
        self.challenges = chs

        for c in chs:
            cid = c["unique_code"]
            m = self.meta_of(cid)
            done = c.get("is_completed") or (
                c.get("flag_count", 0) > 0 and c.get("correct_flag_count", 0) >= c.get("flag_count", 0))
            if done:
                if cid not in self.completed:
                    self.completed.add(cid)
                    self.on_complete(c)
                continue
            # flag 数量增加 = 有进展（重置计时基准）
            if c.get("correct_flag_count", 0) > m["correct_snapshot"]:
                log.info("★ %s 平台收到新正确 flag（%d/%d）",
                         cid, c["correct_flag_count"], c["flag_count"])
                m["correct_snapshot"] = c["correct_flag_count"]
                m["progress_seen"] = now
                self.bb.update_progress(cid, correct_flags=c["correct_flag_count"],
                                        flag_count=c["flag_count"], last_flag_at=now)
            # pending 启动就绪 -> spawn
            if m.get("pending_deadline") and c.get("container_status") == "available":
                self._spawn_for(c, c.get("container_addr") or m.get("addr", []))

    def on_complete(self, c):
        cid = c["unique_code"]
        if cid in self.skip:
            self.skip.remove(cid)
        n = self.agents.kill_all_for(cid, "flag 全提交")
        closed = ""
        if c.get("container_status") == "available":
            try:
                self.adapter.close_challenge(cid)
                closed = "，容器已关闭"
            except AdapterError as e:
                closed = f"，close 失败({e.code})"
        self.meta_of(cid)["pending_deadline"] = None
        self.bb.update_progress(cid, completed=True, completed_at=time.time(),
                                correct_flags=c.get("correct_flag_count"),
                                flag_count=c.get("flag_count"))
        log.info("✔ %s 通关：kill %d 个 agent%s，释放槽位", cid, n, closed)
        self._event("complete", f"{cid} 通关", killed=n, score=c.get("total_score", 0))

    # ================= 超时漏斗 =================
    def _funnel(self, cid):
        """返回 (看提示阈值, 二次解题后强制停止期限, 绝对兜底上限)。按难度/flag 数定制：
        看提示: easy 10/med 20/hard 30；多flag: easy 30/med 40/hard 50（分钟）
        二次解题后强制停止: easy 15/med 20/hard 25；多flag: easy 20/med 35/hard 40
        绝对兜底 = 看提示 + 二次解题期限"""
        c = self._find(cid)
        diff = (c or {}).get("difficulty", "medium")
        multi = (c or {}).get("flag_count", 1) > 1
        if multi:
            t_hint = {"easy": self.cfg.t20_multi_easy, "medium": self.cfg.t20_multi_medium,
                      "hard": self.cfg.t20_multi_hard}.get(diff, self.cfg.t20_multi_hard)
            t_post = {"easy": self.cfg.t30_multi_easy, "medium": self.cfg.t30_multi_medium,
                      "hard": self.cfg.t30_multi_hard}.get(diff, self.cfg.t30_multi_hard)
        else:
            t_hint = {"easy": self.cfg.t20_easy, "medium": self.cfg.t20,
                      "hard": self.cfg.t20_hard}.get(diff, self.cfg.t20)
            t_post = {"easy": self.cfg.t30_easy, "medium": self.cfg.t30,
                      "hard": self.cfg.t30_hard}.get(diff, self.cfg.t30)
        t_abs = t_hint + t_post
        return t_hint, t_post, t_abs

    def check_timeouts(self, now):
        for cid, m in list(self.meta.items()):
            if m["first_start"] is None:
                continue
            running = self.agents.running_for(cid)
            if not running:
                continue
            # 只剩 secondary（漏斗豁免）时不做超时判断
            elapsed = now - m["first_start"]
            last = max([a.get("last_activity") or m["first_start"] for a in running]
                       + [m["progress_seen"] or m["first_start"]])
            idle = now - last

            t_hint, t_post, t_abs = self._funnel(cid)
            if m["hint_at"] is None:
                if idle >= t_hint:
                    self._pull_hint(cid)
            else:
                hint_elapsed = now - m["hint_at"]
                if hint_elapsed >= t_post and m["correct_snapshot"] >= self._correct_now(cid):
                    self._skip(cid, f"二次解题 {t_post // 60}min 无新 flag")
                    continue
            if elapsed >= t_abs:
                self._skip(cid, f"绝对兜底 {t_abs // 60}min 超时")

    def _pull_hint(self, cid):
        try:
            h = self.adapter.get_hint(cid)
        except TaskEndedError:
            self.stop = True
            return
        except ChallengeNotFoundError:
            return
        except AdapterError as e:
            log.warning("拉取 %s 提示失败: %s", cid, e)
            return
        self.bb.write_hint(cid, h.get("hint") or "")
        m = self.meta_of(cid)
        m["hint_at"] = time.time()
        self.bb.update_progress(cid, hint_pulled_at=m["hint_at"])
        log.info("ⓘ %s 拉取提示（已扣分）：%s", cid, (h.get("hint") or "")[:60])
        self._event("hint", f"{cid} 拉取提示（扣分）")
        # 双线：老 agent 继续，新 agent 带 hint + 已有发现重解
        c = self._find(cid)
        if c:
            self._spawn_for(c, c.get("container_addr") or m.get("addr", []))

    # ================= 补槽位 =================
    def fill_slots(self, now):
        active = self.agents.active_challenges()
        pending = {cid for cid, m in self.meta.items() if m.get("pending_deadline")}
        # 运行中 + 启动中 一起计槽位：3 个容器同时拉起，谁就绪谁先 spawn
        if len(active) + len(pending) >= self.cfg.max_slots:
            return
        # pending 超时处置
        for cid, m in list(self.meta.items()):
            d = m.get("pending_deadline")
            if d and now > d:
                m["pending_deadline"] = None
                log.warning("%s 容器启动超时（%.0fs），close + skip",
                            cid, self.cfg.start_wait_timeout)
                try:
                    self.adapter.close_challenge(cid)
                except AdapterError:
                    pass
                self._skip(cid, "容器启动超时")

        pending = {cid for cid, m in self.meta.items() if m.get("pending_deadline")}
        sel = select_next(self.challenges, self.skip, active | pending, self.blocked)
        if sel is None:
            self.tail_fill(active)
            return
        self._start_one(sel, now)

    def _start_one(self, c, now):
        cid = c["unique_code"]
        m = self.meta_of(cid)
        if m.get("pending_deadline"):
            return  # 已在等待就绪
        if m["first_start"] is None:
            m["first_start"] = now
        try:
            r = self.adapter.start_challenge(cid)
        except ActiveLimitStub:
            self._close_oldest()
            return
        except ActiveLimitError:
            self._close_oldest()
            return
        except ResourceUnavailableError as e:
            log.warning("%s 资源不可用（已重试），冷却 60s: %s", cid, e)
            self.blocked[cid] = now + 60
            return
        except ChallengeNotFoundError:
            log.warning("%s 不存在，skip", cid)
            self._skip(cid, "challenge_not_found")
            return
        except AdapterError as e:
            log.error("%s start 失败: %s", cid, e)
            self.blocked[cid] = now + 60
            return
        addr = r.get("container_addr") or []
        m["addr"] = addr
        cur = self._find(cid)
        if cur and cur.get("container_status") == "available":
            self._spawn_for(cur, addr)
        else:
            m["pending_deadline"] = now + self.cfg.start_wait_timeout
            log.info("◌ %s 容器启动中，等待就绪（最多 %.0fs）addr=%s",
                     cid, self.cfg.start_wait_timeout, ",".join(addr))

    def _close_oldest(self):
        """容器满：close 最久没活动的一题，释放槽位。"""
        running = [a for a in self.agents.registry.values() if a["status"] == "running"]
        if not running:
            log.warning("容器满但没有可释放的题")
            return
        oldest = min(running, key=lambda a: a.get("last_activity") or 0)
        cid = oldest["challenge_id"]
        log.warning("容器满：释放最久无活动的 %s", cid)
        self._skip(cid, "容器满，让位")

    def _spawn_for(self, c, addr, spec=None):
        cid = c["unique_code"]
        self.bb.init_challenge(cid)
        m = self.meta_of(cid)
        if m.get("spawns", 0) >= self.cfg.max_attempts:
            self._skip(cid, "重试超限")
            return
        m["spawns"] += 1
        attempt = m["spawns"]
        m["pending_deadline"] = None
        m["first_start"] = m["first_start"] or time.time()
        intel = self.intel_for(cid, addr)
        if self.cfg.mock:
            spec = spec or MOCK_SPECS.get(cid, {}).get(attempt) or DEFAULT_MOCK_SPEC
            aid = self.agents.spawn_mock_agent(c, addr, attempt, spec)
        else:
            aid = self.agents.spawn_agent(c, addr, attempt, intel)
        self.started_once = True
        if not self.agents.running_for(cid, role="scout"):
            self._spawn_secondary(c, addr)
        self.bb.update_progress(cid, spawns=attempt, addr=list(addr),
                                first_start=m["first_start"])
        log.info("▶ spawn %s attempt=%d → %s addr=%s", aid, attempt, cid, ",".join(addr))
        self._event("spawn", f"{cid} 第 {attempt} 次派发 agent", agent_id=aid, addr=addr)

    def _spawn_secondary(self, c, addr):
        """派发/补位第二解题者：漏斗豁免，不占 primary 的 spawns 计数。"""
        cid = c["unique_code"]
        if self.cfg.mock:
            return
        m = self.meta_of(cid)
        m["sec_spawns"] = m.get("sec_spawns", 0) + 1
        try:
            intel = self.intel_for(cid, addr)
            aid2 = self.agents.spawn_agent(c, addr, m["sec_spawns"], intel,
                                           template="default2.md", role="scout")
            self._event("spawn", f"{cid} 第 {m['sec_spawns']} 次派发第二解题者", agent_id=aid2)
            log.info("▶ spawn %s (scout #%d) → %s", aid2, m["sec_spawns"], cid)
        except FileNotFoundError as e:
            log.warning("secondary agent 启动失败: %s", e)

    # ================= 情报（同网段，并入下次 spawn 的 prompt）=================
    def intel_for(self, cid, addr):
        parts = []
        for c in self.challenges:
            oc = c["unique_code"]
            if oc == cid:
                continue
            om = self.meta.get(oc, {})
            oaddr = om.get("addr") or c.get("container_addr") or []
            if not oaddr or not same_subnet(addr, oaddr):
                continue
            recon = self.bb.read_recon(oc)
            if recon:
                parts.append(f"- 同网段题目 {oc} 的侦察: "
                             + json.dumps(recon, ensure_ascii=False)[:400])
            facts = self.bb.read_facts(oc)
            if facts.strip():
                parts.append(f"- 同网段题目 {oc} 的发现: " + facts.strip()[-400:])
        return "\n".join(parts) if parts else ""

    # ================= 事件回调 =================
    def _fresh_challenge(self, cid):
        """换人前同步查一次平台：返回该题最新状态；失败返回 None。"""
        try:
            chs = self.adapter.list_challenges()
        except AdapterError:
            return None
        if self.cfg.only:
            chs = [c for c in chs if c["unique_code"] in self.cfg.only]
        self.challenges = chs
        for c in chs:
            if c["unique_code"] == cid:
                return c
        return None

    def _is_done(self, c):
        return bool(c.get("is_completed") or (
            c.get("flag_count", 0) > 0 and c.get("correct_flag_count", 0) >= c.get("flag_count", 0)))

    def on_agent_died(self, aid):
        a = self.agents.registry.get(aid)
        if not a:
            return
        cid = a["challenge_id"]
        c = self._find(cid)
        if c is None or c.get("is_completed"):
            return
        # 换人/补位前先同步查平台：题可能刚被自己解完，别再白派 agent
        fresh = self._fresh_challenge(cid)
        if fresh is not None and self._is_done(fresh):
            if cid not in self.completed:
                self.completed.add(cid)
                self.on_complete(fresh)
            return
        m = self.meta_of(cid)
        if fresh is not None and fresh.get("correct_flag_count", 0) > m["correct_snapshot"]:
            # 平台有新 flag = 实质进展：更新基准，重置无进展计时
            m["correct_snapshot"] = fresh["correct_flag_count"]
            m["progress_seen"] = time.time()
        if a.get("role") == "scout":
            # 第二解题者自然退出 -> 立即补位（不受漏斗影响）
            self._spawn_secondary(c, a.get("container_addr") or [])
            return
        # worker 自然退出 = 一次停止：第 1 次换人（scout 不受影响），第 2 次整题收工
        m["stops"] = m.get("stops", 0) + 1
        if m["stops"] >= 2:
            self._skip(cid, "worker 第二次停止，整题收工")
            return
        if m.get("spawns", 0) >= self.cfg.max_attempts:
            self._skip(cid, "重试超限（自然退出）")
            return
        self._spawn_for(c, c.get("container_addr") or a.get("container_addr") or [])

    def on_agent_give_up(self, cid):
        log.info("⚑ %s 的 worker 写了 GIVE_UP，提前处置", cid)
        self._event("give_up", f"{cid} 的 worker 主动放弃")
        # 第一次停止：只杀该题 worker（scout 继续陪跑）
        for aid in [x for x, a in self.agents.registry.items()
                    if a["challenge_id"] == cid and a.get("role") != "scout"]:
            self.agents.kill_agent(aid, "GIVE_UP")
        c = self._find(cid)
        if c is None or c.get("is_completed"):
            return
        m = self.meta_of(cid)
        fresh = self._fresh_challenge(cid)
        if fresh is not None and self._is_done(fresh):
            if cid not in self.completed:
                self.completed.add(cid)
                self.on_complete(fresh)
            return
        m["stops"] = m.get("stops", 0) + 1
        if m["stops"] >= 2:
            self._skip(cid, "worker 第二次停止（GIVE_UP），整题收工")
            return
        if m.get("spawns", 0) >= self.cfg.max_attempts:
            self._skip(cid, "重试超限（GIVE_UP）")
            return
        self._spawn_for(c, c.get("container_addr") or m.get("addr", []))

    def on_mock_flag_submitted(self, cid, flag):
        """mock 模式：watcher 扫到 SUBMITTED_FLAG，模拟 agent 已 curl 提交。"""
        try:
            r = self.adapter.submit_flag(cid, flag)
            m = self.meta_of(cid)
            m["progress_seen"] = time.time()
            self.bb.update_progress(cid, submitted_flags=(self.bb.read_progress(cid).get("submitted_flags", 0) + 1),
                                    correct_flags=r["correct_flag_count"])
            log.info("★ %s 提交 flag（mock 模拟 agent curl）：%s → %d/%d",
                     cid, flag, r["correct_flag_count"], r["total_flag_count"])
            self._event("flag", f"{cid} 提交 flag（{r['correct_flag_count']}/{r['total_flag_count']}）")
        except KeyError:
            log.warning("mock 提交失败：题目 %s 不存在", cid)

    # ================= 放弃 / 尾声补题 =================
    def _skip(self, cid, reason):
        self.agents.kill_all_for(cid, reason)
        try:
            self.adapter.close_challenge(cid)
        except AdapterError:
            pass
        if cid not in self.skip:
            self.skip.append(cid)
        self.meta_of(cid)["pending_deadline"] = None
        self.bb.update_progress(cid, skipped=True, skip_reason=reason)
        log.warning("✖ %s skip：%s", cid, reason)
        self._event("skip", f"{cid} 跳过：{reason}")

    def tail_fill(self, active):
        """尾声补题：槽位空但无未开始题时，从 skip 清单挑 facts 最丰富的题补做一次。"""
        cands = [c for c in self.challenges
                 if not c.get("is_completed")
                 and c["unique_code"] in self.skip
                 and c["unique_code"] not in self.retried
                 and c["unique_code"] not in active]
        if not cands:
            return
        cands.sort(key=lambda c: -self.bb.facts_size(c["unique_code"]))
        c = cands[0]
        cid = c["unique_code"]
        self.retried.add(cid)
        self.skip.remove(cid)
        self.meta_of(cid)["spawns"] = 0  # 补做给满额重试机会（带全部已有发现）
        log.info("↻ 尾声补题：%s（facts %d 字符，最有希望）", cid, self.bb.facts_size(cid))
        self._event("retry", f"{cid} 尾声补题（带已有发现再试一次）")

    def _no_work_left(self):
        active = self.agents.active_challenges()
        for c in self.challenges:
            if c.get("is_completed"):
                continue
            cid = c["unique_code"]
            if cid in active:
                return False
            if cid not in self.skip:
                return False
            if cid not in self.retried:
                return False
        return True

    # ================= 状态快照 + 事件流（监控用）=================
    def _event(self, etype, msg, **data):
        rec = {"ts": time.time(), "type": etype, "msg": msg}
        rec.update(data)
        try:
            with open(self.events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _derived_status(self, c, cid):
        if cid in self.completed or c.get("is_completed"):
            return "completed"
        if self.agents.running_for(cid):
            return "running"
        prog = self.bb.read_progress(cid)
        if prog.get("skipped"):
            return "skipped"
        if self.meta_of(cid).get("pending_deadline"):
            return "pending"
        return "todo"

    def write_status(self):
        chs = []
        done = 0
        est = 0
        for c in sorted(self.challenges, key=lambda x: x["unique_code"]):
            cid = c["unique_code"]
            prog = self.bb.read_progress(cid)
            st = self._derived_status(c, cid)
            if st == "completed":
                done += 1
                est += c.get("total_score", 0)
            chs.append({
                "unique_code": cid,
                "difficulty": c.get("difficulty"),
                "total_score": c.get("total_score"),
                "flag_count": c.get("flag_count"),
                "correct_flag_count": c.get("correct_flag_count"),
                "container_status": c.get("container_status"),
                "status": st,
                "spawns": prog.get("spawns", 0),
                "addr": prog.get("addr") or c.get("container_addr") or [],
                "skip_reason": prog.get("skip_reason", ""),
            })
        agents = [{
            "agent_id": aid,
            "challenge_id": a["challenge_id"],
            "status": a["status"],
            "role": a.get("role", "worker"),
            "attempt_no": a["attempt_no"],
            "started_at": a["start_time"],
            "last_activity": a["last_activity"],
        } for aid, a in self.agents.registry.items()]
        state = {
            "ts": time.time(),
            "mode": "mock" if self.cfg.mock else "real",
            "counters": {
                "total": len(self.challenges),
                "completed": done,
                "skipped": len(self.skip),
                "running": self.agents.running_count(),
                "est_score": est,
            },
            "challenges": chs,
            "agents": agents,
        }
        tmp = self.status_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(tmp, self.status_path)
        except Exception:
            pass

    # ================= 收尾 =================
    def teardown(self):
        self._event("stop", "引擎收尾")
        log.info("收尾：kill 全部 agent")
        for aid in list(self.agents.registry):
            self.agents.kill_agent(aid, "收尾清理")
        log.info("收尾：close 全部 available 容器")
        try:
            for c in self.adapter.list_challenges():
                if c.get("container_status") == "available":
                    try:
                        self.adapter.close_challenge(c["unique_code"])
                    except AdapterError:
                        pass
        except Exception:
            pass

    def write_summary(self):
        lines = ["# QueenBee 跑分总结", "",
                 f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                 f"- 模式: {'mock 演示' if self.cfg.mock else '真实平台'}",
                 f"- 题目总数: {len(self.challenges)}",
                 ""]
        done = 0
        est_total = 0
        rows = ["| 题目 | 难度 | 满分 | flag | 状态 | 估算得分 |", "|---|---|---|---|---|---|"]
        for c in sorted(self.challenges, key=lambda x: x["unique_code"]):
            cid = c["unique_code"]
            prog = self.bb.read_progress(cid)
            if c.get("is_completed"):
                status, est = "completed", c.get("total_score", 0)
                done += 1
            elif prog.get("skipped"):
                status = f"skipped({prog.get('skip_reason', '')})"
                est = 0
            else:
                status, est = "未完成", 0
            est = int(est)
            est_total += est
            rows.append(f"| {cid} | {c.get('difficulty')} | {c.get('total_score')} "
                        f"| {c.get('correct_flag_count')}/{c.get('flag_count')} "
                        f"| {status} | {est} |")
        lines += rows
        lines += ["",
                  f"- 通关 {done}/{len(self.challenges)} 题",
                  f"- 估算总分: {est_total}（按通关题满分估算）",
                  ""]
        with open(self.summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.info("总结已写入 %s", self.summary_path)


def main():
    p = argparse.ArgumentParser(description="TSecBench 智能调度引擎")
    p.add_argument("--mock", action="store_true", help="本地演示模式（假平台+假agent，不联网不扣分）")
    p.add_argument("--slots", type=int, default=3, help="并发槽位（默认 3）")
    p.add_argument("--t20", type=int, default=1200, help="看提示阈值: medium（秒，默认 1200）")
    p.add_argument("--t20-easy", type=int, default=600, help="看提示阈值: easy（秒，默认 600）")
    p.add_argument("--t20-hard", type=int, default=1800, help="看提示阈值: hard（秒，默认 1800）")
    p.add_argument("--t20-multi-easy", type=int, default=1800, help="看提示阈值: 多flag+easy（秒，默认 1800）")
    p.add_argument("--t20-multi-medium", type=int, default=2400, help="看提示阈值: 多flag+medium（秒，默认 2400）")
    p.add_argument("--t20-multi-hard", type=int, default=3000, help="看提示阈值: 多flag+hard（秒，默认 3000）")
    p.add_argument("--t30", type=int, default=1200, help="二次解题后强制停止: medium（秒，默认 1200）")
    p.add_argument("--t30-hard", type=int, default=1500, help="二次解题后强制停止: hard（秒，默认 1500）")
    p.add_argument("--t30-easy", type=int, default=900, help="二次解题后强制停止: easy（秒，默认 900）")
    p.add_argument("--t30-multi-easy", type=int, default=1200, help="二次解题后强制停止: 多flag+easy（秒，默认 1200）")
    p.add_argument("--t30-multi-medium", type=int, default=2100, help="二次解题后强制停止: 多flag+medium（秒，默认 2100）")
    p.add_argument("--t30-multi-hard", type=int, default=2400, help="二次解题后强制停止: 多flag+hard（秒，默认 2400）")
    p.add_argument("--t40", type=int, default=600, help="绝对兜底余量（秒，默认 600）")
    p.add_argument("--poll", type=int, default=10, help="通关检测轮询间隔（秒，默认 10）")
    p.add_argument("--tick", type=int, default=5, help="黑板监视间隔（秒，默认 5）")
    p.add_argument("--start-wait", type=int, default=120, help="start 后等就绪超时（秒，默认 120）")
    p.add_argument("--bb", default=None, help="黑板目录（默认 <项目>/blackboard）")
    p.add_argument("--only", default=None, help="只解指定题目（逗号分隔，如 c-03,c-06）")
    args = p.parse_args()

    only = [x.strip() for x in args.only.split(",")] if args.only else None
    cfg = Config(mock=args.mock, t20=args.t20, t30=args.t30, t40=args.t40,
                 poll=args.poll, watcher_tick=args.tick, slots=args.slots,
                 start_wait=args.start_wait, bb_dir=args.bb, only=only,
                 t20_easy=args.t20_easy, t20_hard=args.t20_hard,
                 t30_easy=args.t30_easy, t30_hard=args.t30_hard,
                 t20_multi_easy=args.t20_multi_easy, t20_multi_medium=args.t20_multi_medium,
                 t20_multi_hard=args.t20_multi_hard, t30_multi_easy=args.t30_multi_easy,
                 t30_multi_medium=args.t30_multi_medium, t30_multi_hard=args.t30_multi_hard)

    # Windows 控制台 UTF-8（git-bash 下避免中文乱码）
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(os.path.join(cfg.root, "queenbee.log"), encoding="utf-8"),
        ],
    )

    eng = QueenBee(cfg)
    sys.exit(eng.run())


if __name__ == "__main__":
    main()
