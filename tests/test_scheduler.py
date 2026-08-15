# -*- coding: utf-8 -*-
"""调度策略单元测试：选题排序 + 同网段判断。"""
import unittest

from engine.scheduler import same_subnet, select_next


def ch(code, difficulty="easy", score=100, completed=False):
    return {"unique_code": code, "difficulty": difficulty, "total_score": score,
            "is_completed": completed, "flag_count": 1, "correct_flag_count": 0}


class TestSelectNext(unittest.TestCase):
    def setUp(self):
        self.chs = [
            ch("a", "hard", 500),
            ch("b", "easy", 200),
            ch("c", "easy", 100),
            ch("d", "medium", 300),
        ]

    def test_order_easy_first_then_score_asc(self):
        sel = select_next(self.chs, skip=[], active_cids=set())
        self.assertEqual(sel["unique_code"], "c")  # easy 100 < easy 200

    def test_skip_filtered(self):
        sel = select_next(self.chs, skip=["c", "b"], active_cids=set())
        self.assertEqual(sel["unique_code"], "d")  # 下一个 medium

    def test_active_filtered(self):
        sel = select_next(self.chs, skip=[], active_cids={"c", "b", "d"})
        self.assertEqual(sel["unique_code"], "a")

    def test_completed_filtered(self):
        chs = self.chs + [ch("e", "easy", 50, completed=True)]
        sel = select_next(chs, skip=[], active_cids=set())
        self.assertNotEqual(sel["unique_code"], "e")

    def test_none_when_all_done(self):
        sel = select_next([ch("x", completed=True)], skip=[], active_cids=set())
        self.assertIsNone(sel)

    def test_blocked_cooldown(self):
        import time
        blocked = {"c": time.time() + 100}
        sel = select_next(self.chs, skip=[], active_cids=set(), blocked=blocked)
        self.assertEqual(sel["unique_code"], "b")  # c 在冷却期


class TestSameSubnet(unittest.TestCase):
    def test_same_v4_subnet(self):
        self.assertTrue(same_subnet(["10.0.1.5:8080"], ["10.0.1.99:80"]))

    def test_diff_v4_subnet(self):
        self.assertFalse(same_subnet(["10.0.1.5:8080"], ["10.0.2.5:80"]))

    def test_empty_addrs(self):
        self.assertFalse(same_subnet([], ["10.0.1.5:80"]))
        self.assertFalse(same_subnet(["10.0.1.5:80"], []))

    def test_bad_ip_ignored(self):
        self.assertFalse(same_subnet(["not-an-ip:80"], ["not-an-ip:80"]))


if __name__ == "__main__":
    unittest.main()
