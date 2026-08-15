# -*- coding: utf-8 -*-
"""黑板模块单元测试。运行: python -m unittest discover -s tests -t . """
import json
import os
import tempfile
import time
import unittest

from engine.blackboard import Blackboard


class TestBlackboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bb_test_")
        self.bb = Blackboard(os.path.join(self.tmp, "blackboard"))

    def test_init_challenge_creates_dir(self):
        self.bb.init_challenge("c-01")
        self.assertTrue(os.path.isdir(self.bb.dir_of("c-01")))

    def test_agent_writes_and_engine_reads_facts(self):
        self.bb.init_challenge("c-01")
        with open(self.bb.facts_path("c-01"), "a", encoding="utf-8") as f:
            f.write("found admin path\n")
        self.assertIn("admin", self.bb.read_facts("c-01"))

    def test_activity_returns_mtime(self):
        self.bb.init_challenge("c-01")
        self.assertIsNone(self.bb.activity("c-01"))
        with open(self.bb.facts_path("c-01"), "w", encoding="utf-8") as f:
            f.write("x")
        act = self.bb.activity("c-01")
        self.assertIsNotNone(act)
        self.assertAlmostEqual(act, time.time(), delta=5)

    def test_summary_contains_facts_recon_hint(self):
        self.bb.init_challenge("c-01")
        with open(self.bb.facts_path("c-01"), "w", encoding="utf-8") as f:
            f.write("injection confirmed")
        with open(self.bb.recon_path("c-01"), "w", encoding="utf-8") as f:
            json.dump({"ports": [80]}, f)
        self.bb.write_hint("c-01", "try default creds")
        s = self.bb.summary("c-01")
        self.assertIn("injection confirmed", s)
        self.assertIn("80", s)
        self.assertIn("try default creds", s)

    def test_has_give_up_with_offset(self):
        self.bb.init_challenge("c-01")
        with open(self.bb.facts_path("c-01"), "w", encoding="utf-8") as f:
            f.write("GIVE_UP\n")
        self.assertTrue(self.bb.has_give_up("c-01", offset=0))
        # offset 之后不再触发（避免 respawn 后新 agent 被旧标记误判）
        size = len(self.bb.read_facts("c-01"))
        self.assertFalse(self.bb.has_give_up("c-01", offset=size))

    def test_progress_merge(self):
        self.bb.update_progress("c-01", spawns=1)
        self.bb.update_progress("c-01", correct_flags=1)
        p = self.bb.read_progress("c-01")
        self.assertEqual(p["spawns"], 1)
        self.assertEqual(p["correct_flags"], 1)

    def test_empty_summary_when_no_data(self):
        self.bb.init_challenge("c-02")
        self.assertIn("暂无", self.bb.summary("c-02"))


if __name__ == "__main__":
    unittest.main()
