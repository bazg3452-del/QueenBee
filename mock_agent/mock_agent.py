# -*- coding: utf-8 -*-
"""模拟解题 agent 进程（--mock 演示用）：模仿真实 agent——写黑板、模拟提交 flag、可选放弃。

用法: python mock_agent.py <cid> <blackboard_dir> <spec_json>
spec: {
  "recon_after": 秒,              # 写入 recon.json 的时刻
  "notes": [[延迟秒, "文本"], ...],  # 依次追加 facts
  "flag_after": 秒,               # 找到 flag 并写 SUBMITTED_FLAG 的时刻
  "flag_index": 1,
  "give_up_after": 秒,            # 写 GIVE_UP 的时刻
  "exit_after": 秒                # 进程退出时刻
}
"""
import json
import os
import sys
import time


def main():
    cid, bbdir, spec_s = sys.argv[1], sys.argv[2], sys.argv[3]
    spec = json.loads(spec_s)
    d = os.path.join(bbdir, cid)
    os.makedirs(d, exist_ok=True)
    t0 = time.time()

    def ts():
        return time.strftime("%H:%M:%S")

    def facts(line):
        with open(os.path.join(d, "facts.md"), "a", encoding="utf-8") as f:
            f.write(f"[{ts()}] {line}\n")

    def sleep_until(delay):
        left = delay - (time.time() - t0)
        if left > 0:
            time.sleep(left)

    # 1) 侦察
    sleep_until(spec.get("recon_after", 1))
    recon = {"ports": [80, 22],
             "services": [{"port": 80, "banner": "nginx/1.18.0 (Ubuntu)"}],
             "paths": ["/", "/admin", "/login", "/api"],
             "scanned_at": ts()}
    with open(os.path.join(d, "recon.json"), "w", encoding="utf-8") as f:
        json.dump(recon, f, ensure_ascii=False, indent=2)
    facts("侦察完成：80 nginx/1.18.0、22 ssh，发现 /admin /login /api 路径")

    # 2) 剧本 notes
    for delay, text in spec.get("notes", []):
        sleep_until(delay)
        facts(text)

    # 3) 找到 flag -> 写 SUBMITTED_FLAG（watcher 会模拟"agent 自己 curl 提交"）
    if spec.get("flag_after"):
        sleep_until(spec["flag_after"])
        flag = f"flag{{mock-{cid}-{spec.get('flag_index', 1)}}}"
        facts(f"在 /admin/backup.sql 中找到 flag：{flag}")
        facts(f"SUBMITTED_FLAG: {flag}")
        facts("提交响应 correct=true，已记录")

    # 4) 放弃
    if spec.get("give_up_after"):
        sleep_until(spec["give_up_after"])
        facts("GIVE_UP")

    # 5) 退出（自然死亡）
    sleep_until(spec.get("exit_after", spec.get("give_up_after", 10) + 3))


if __name__ == "__main__":
    main()
