# -*- coding: utf-8 -*-
"""prompt 模板填充测试：占位符全部替换 + 提交命令内嵌真实凭证。"""
import os
import tempfile
import unittest

from engine.agent_manager import AgentManager
from engine.blackboard import Blackboard
from engine.config import Config


class TestBuildPrompt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prompt_test_")
        os.environ["BENCHMARK_TOKEN"] = "test-token-123"
        os.environ["BENCHMARK_BASE_URL"] = "https://bench.example.com"
        # 项目结构：engine/ 与 agent_prompts/ 在根下
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cfg = Config(mock=True, bb_dir=os.path.join(self.tmp, "bb"),
                          logs_dir=os.path.join(self.tmp, "logs"))
        self.cfg.root = root
        self.cfg.prompts_dir = os.path.join(root, "agent_prompts")
        self.bb = Blackboard(self.cfg.blackboard_dir)
        self.am = AgentManager(self.cfg, self.bb)

    def tearDown(self):
        os.environ.pop("BENCHMARK_TOKEN", None)
        os.environ.pop("BENCHMARK_BASE_URL", None)

    def test_all_placeholders_replaced(self):
        ch = {"unique_code": "c-06", "difficulty": "easy", "flag_count": 1,
              "description": "测试题目", "total_score": 100}
        prompt = self.am.build_prompt(ch, ["10.0.1.5:80"], 2, "agent_x")
        self.assertNotIn("{unique_code}", prompt)
        self.assertNotIn("{container_addr}", prompt)
        self.assertNotIn("{facts_summary}", prompt)
        self.assertNotIn("{hint}", prompt)
        self.assertNotIn("{blackboard_dir}", prompt)
        self.assertNotIn("{intel}", prompt)
        self.assertNotIn("{submit_url}", prompt)
        self.assertNotIn("{submit_token}", prompt)
        self.assertNotIn("{attempt_no}", prompt)
        self.assertNotIn("{agent_id}", prompt)

    def test_submit_command_embedded(self):
        ch = {"unique_code": "c-06", "difficulty": "easy", "flag_count": 1,
              "description": "测试题目", "total_score": 100}
        prompt = self.am.build_prompt(ch, ["10.0.1.5:80"], 1, "agent_x")
        self.assertIn("https://bench.example.com/openapi/v1/challenges/submit", prompt)
        self.assertIn("BENCHMARK_TOKEN: test-token-123", prompt)
        self.assertIn('"unique_code":"c-06"', prompt)

    def test_facts_summary_included(self):
        self.bb.init_challenge("c-06")
        with open(self.bb.facts_path("c-06"), "w", encoding="utf-8") as f:
            f.write("前人的发现：/admin 后台可访问")
        ch = {"unique_code": "c-06", "difficulty": "easy", "flag_count": 1,
              "description": "测试题目"}
        prompt = self.am.build_prompt(ch, ["10.0.1.5:80"], 2, "agent_y")
        self.assertIn("前人的发现", prompt)

    def test_hint_included_after_pull(self):
        self.bb.write_hint("c-06", "提示：试试默认口令")
        ch = {"unique_code": "c-06", "difficulty": "easy", "flag_count": 1,
              "description": "测试题目"}
        prompt = self.am.build_prompt(ch, ["10.0.1.5:80"], 2, "agent_z")
        self.assertIn("提示：试试默认口令", prompt)


if __name__ == "__main__":
    unittest.main()
