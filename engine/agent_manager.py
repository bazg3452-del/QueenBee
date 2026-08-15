# -*- coding: utf-8 -*-
"""Agent 进程管理：spawn/kill one-shot claude -p 子进程（stdin=DEVNULL，stdout 落盘）。"""
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time

log = logging.getLogger("queenbee.agents")


class AgentManager:
    def __init__(self, cfg, blackboard):
        self.cfg = cfg
        self.bb = blackboard
        self.registry = {}
        self._template = None
        os.makedirs(cfg.logs_dir, exist_ok=True)

    # ---------- prompt ----------
    def _load_template(self, name="default.md"):
        if name == "default.md":
            if self._template is None:
                self._template = self._read_template(name)
            return self._template
        return self._read_template(name)

    def _read_template(self, name):
        path = os.path.join(self.cfg.prompts_dir, name)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def build_prompt(self, challenge, container_addr, attempt_no, agent_id, intel="", template="default.md"):
        cid = challenge["unique_code"]
        t = self._load_template(template)
        repl = {
            "{agent_id}": agent_id,
            "{attempt_no}": str(attempt_no),
            "{unique_code}": cid,
            "{difficulty}": str(challenge.get("difficulty", "")),
            "{flag_count}": str(challenge.get("flag_count", 1)),
            "{description}": str(challenge.get("description") or "无"),
            "{container_addr}": ",".join(container_addr) if container_addr else "（无）",
            "{intel}": intel or "无",
            "{facts_summary}": self.bb.summary(cid),
            "{hint}": self.bb.read_hint(cid) or "无",
            "{blackboard_dir}": self.bb.dir_of(cid),
            "{submit_url}": self.cfg.base_url,
            "{submit_token}": self.cfg.token,
        }
        for k, v in repl.items():
            t = t.replace(k, v)
        return t

    # ---------- spawn ----------
    def _new_agent_id(self, cid):
        return f"agent_{cid}_{int(time.time() * 1000)}"

    def _log_path(self, agent_id):
        return os.path.join(self.cfg.logs_dir, agent_id + ".log")

    def _register(self, agent_id, proc, challenge, container_addr, attempt_no, role="worker"):
        cid = challenge["unique_code"]
        self.registry[agent_id] = {
            "pid": proc.pid,
            "process": proc,
            "challenge_id": cid,
            "container_addr": list(container_addr),
            "start_time": time.time(),
            "status": "running",     # running | done | killed
            "attempt_no": attempt_no,
            "role": role,            # primary | secondary
            "last_activity": time.time(),
            "killed_reason": "",
        }
        return agent_id

    def spawn_agent(self, challenge, container_addr, attempt_no=1, intel="", template="default.md", role="worker"):
        """启动真实 claude -p one-shot agent。stdin=DEVNULL，stdout/stderr 落日志文件。
        role: worker=受漏斗管理 / scout=漏斗豁免（通关才杀）。"""
        cid = challenge["unique_code"]
        agent_id = self._new_agent_id(cid)
        prompt = self.build_prompt(challenge, container_addr, attempt_no, agent_id, intel, template)
        # bypassPermissions：无头模式无人批准，Bash/curl 必须直接放行（授权靶场内使用）
        cmd = self._claude_cmd() + ["-p", prompt, "--permission-mode", "bypassPermissions"]
        logf = open(self._log_path(agent_id), "wb")
        # POSIX 下让 agent 成为新会话组长，kill 时按进程组连孙进程一起带走（容器内防孤儿）
        kwargs = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            cwd=self.cfg.root,
            **kwargs,
        )
        logf.close()
        return self._register(agent_id, proc, challenge, container_addr, attempt_no, role)

    def spawn_mock_agent(self, challenge, container_addr, attempt_no=1, spec=None):
        """启动模拟 agent 进程（--mock 演示用），行为由 spec 剧本控制。"""
        cid = challenge["unique_code"]
        agent_id = self._new_agent_id(cid)
        script = os.path.join(self.cfg.root, "mock_agent", "mock_agent.py")
        spec = spec or {}
        cmd = [sys.executable, script, cid, self.cfg.blackboard_dir, json.dumps(spec, ensure_ascii=False)]
        logf = open(self._log_path(agent_id), "wb")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            cwd=self.cfg.root,
        )
        logf.close()
        return self._register(agent_id, proc, challenge, container_addr, attempt_no)

    def _claude_cmd(self):
        p = shutil.which("claude")
        if not p:
            raise FileNotFoundError("PATH 中未找到 claude CLI")
        # Windows 的 npm 全局安装是 .cmd shim：解析出真实 exe 直跑，
        # 绕开 cmd.exe 的引号/换行转义问题（prompt 含引号和换行）
        if os.name == "nt" and p.lower().endswith((".cmd", ".bat")):
            exe = self._resolve_windows_exe(p)
            if exe:
                return [exe]
        return [p]

    @staticmethod
    def _resolve_windows_exe(shim):
        """从 npm shim 里解析真实可执行文件（如 claude.exe）。失败返回 None。"""
        try:
            with open(shim, encoding="utf-8", errors="replace") as f:
                content = f.read()
            # shim 里最后一行通常是: "%dp0%\node_modules\...\claude.exe"  %*
            m = re.findall(r'"([^"\r\n]+\.exe)"', content)
            if m:
                path = m[-1].replace("%~dp0%", os.path.dirname(shim) + os.sep) \
                           .replace("%dp0%", os.path.dirname(shim) + os.sep)
                if os.path.exists(path):
                    return path
            # 标准 npm 全局布局兜底
            for guess in (
                os.path.join(os.path.dirname(shim), "node_modules",
                             "@anthropic-ai", "claude-code", "bin", "claude.exe"),
            ):
                if os.path.exists(guess):
                    return guess
        except Exception:
            pass
        return None

    # ---------- kill / 状态 ----------
    def kill_agent(self, agent_id, reason=""):
        a = self.registry.get(agent_id)
        if not a or a["status"] != "running":
            return
        # POSIX：按进程组杀（agent 是会话组长，孙进程一起带走）；Windows：terminate/kill
        if os.name != "nt" and hasattr(os, "killpg"):
            try:
                os.killpg(a["pid"], signal.SIGTERM)
                a["process"].wait(timeout=3)
            except Exception:
                try:
                    os.killpg(a["pid"], signal.SIGKILL)
                except Exception:
                    pass
        else:
            try:
                a["process"].terminate()
                a["process"].wait(timeout=3)
            except Exception:
                try:
                    a["process"].kill()
                except Exception:
                    pass
        a["status"] = "killed"
        a["killed_reason"] = reason

    def reap_dead(self):
        """自然退出的 agent：status -> done，返回 agent_id 列表。"""
        died = []
        for aid, a in self.registry.items():
            if a["status"] == "running" and a["process"].poll() is not None:
                a["status"] = "done"
                died.append(aid)
        return died

    def kill_all_for(self, cid, reason=""):
        n = 0
        for aid, a in list(self.registry.items()):
            if a["challenge_id"] == cid:
                self.kill_agent(aid, reason)
                n += 1
        return n

    def running_for(self, cid, role=None):
        return [a for a in self.registry.values()
                if a["challenge_id"] == cid and a["status"] == "running"
                and (role is None or a.get("role") == role)]

    def active_challenges(self):
        return {a["challenge_id"] for a in self.registry.values()
                if a["status"] == "running"}

    def running_count(self):
        return sum(1 for a in self.registry.values() if a["status"] == "running")
