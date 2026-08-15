# -*- coding: utf-8 -*-
"""黑板：每题独立目录。agent 只写 facts/recon；引擎写 hint/progress；引擎读全部。

/workspace/blackboard/{challenge_id}/
├── facts.md        agent 写：确认事实 + 提交结果（追加）
├── recon.json      agent 写：侦察结果（端口/服务/指纹/路径）
├── hint.md         引擎写：平台提示原文（spawn 时附进 prompt）
└── progress.json   引擎写：进度快照、得分、时间戳
"""
import json
import os
import threading

FACTS = "facts.md"
RECON = "recon.json"
HINT = "hint.md"
PROGRESS = "progress.json"


class Blackboard:
    def __init__(self, root):
        self.root = root
        self._lock = threading.Lock()
        os.makedirs(root, exist_ok=True)

    # ---------- 路径 ----------
    def dir_of(self, cid):
        return os.path.join(self.root, cid)

    def init_challenge(self, cid):
        os.makedirs(self.dir_of(cid), exist_ok=True)

    def facts_path(self, cid):
        return os.path.join(self.dir_of(cid), FACTS)

    def recon_path(self, cid):
        return os.path.join(self.dir_of(cid), RECON)

    def hint_path(self, cid):
        return os.path.join(self.dir_of(cid), HINT)

    def progress_path(self, cid):
        return os.path.join(self.dir_of(cid), PROGRESS)

    # ---------- 读（agent 写的文件）----------
    def read_facts(self, cid):
        p = self.facts_path(cid)
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()

    def read_recon(self, cid):
        p = self.recon_path(cid)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return json.load(f)
        except Exception:
            return {}

    def read_hint(self, cid):
        p = self.hint_path(cid)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()

    # ---------- 引擎写 ----------
    def write_hint(self, cid, text):
        self.init_challenge(cid)
        with self._lock:
            with open(self.hint_path(cid), "w", encoding="utf-8") as f:
                f.write(text or "")

    def read_progress(self, cid):
        p = self.progress_path(cid)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def update_progress(self, cid, **fields):
        """合并更新 progress.json（引擎记账）。"""
        self.init_challenge(cid)
        with self._lock:
            data = self.read_progress(cid)
            data.update(fields)
            with open(self.progress_path(cid), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    # ---------- 活动度 ----------
    def activity(self, cid):
        """facts/recon 的最新 mtime（agent 是否在干活的依据）。"""
        best = None
        for p in (self.facts_path(cid), self.recon_path(cid)):
            try:
                m = os.path.getmtime(p)
                best = m if best is None else max(best, m)
            except OSError:
                pass
        return best

    # ---------- spawn 摘要 ----------
    def summary(self, cid, facts_chars=2000, recon_chars=1000):
        """生成 spawn 时附进 prompt 的接力内容。"""
        parts = []
        facts = self.read_facts(cid)
        if facts.strip():
            parts.append("## 已有发现(facts)\n" + facts[-facts_chars:].strip())
        recon = self.read_recon(cid)
        if recon:
            try:
                parts.append("## 已有侦察(recon)\n"
                             + json.dumps(recon, ensure_ascii=False, indent=2)[:recon_chars])
            except Exception:
                pass
        hint = self.read_hint(cid)
        if hint:
            parts.append("## 平台提示(hint)\n" + hint.strip())
        return "\n\n".join(parts) if parts else "（暂无，首次解题）"

    def has_give_up(self, cid, offset=0):
        """facts 尾部（offset 之后）是否出现 GIVE_UP 标记。"""
        facts = self.read_facts(cid)
        return "GIVE_UP" in facts[offset:]

    def facts_size(self, cid):
        return len(self.read_facts(cid))
