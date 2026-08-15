# -*- coding: utf-8 -*-
"""tsec_adapter 单元测试：错误码映射 + 重试逻辑（mock urlopen）。"""
import json
import unittest
import urllib.error
from unittest import mock

from engine.tsec_adapter import (ActiveLimitError, ChallengeNotFoundError,
                                 DuplicateError, ResourceUnavailableError,
                                 TaskEndedError, TaskNotFoundError,
                                 TSecBenchAdapter)


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_fake(seq):
    """seq: [(status, payload), ...] 依次返回；耗尽后回 200/[]。"""
    calls = []

    def fake(req, timeout=None):
        status, payload = seq[len(calls)] if len(calls) < len(seq) else (200, [])
        calls.append((req.full_url, req.get_method(),
                      {k.lower(): v for k, v in req.headers.items()}))
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "", None,
                                         FakeResponse(status, payload))
        return FakeResponse(status, payload)

    return fake, calls


class TestAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = TSecBenchAdapter("https://bench.example.com", "tok-1")

    def test_list_ok(self):
        fake, calls = make_fake([(200, [{"unique_code": "a-01"}])])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            chs = self.adapter.list_challenges()
        self.assertEqual(chs[0]["unique_code"], "a-01")
        # urllib 会把 header 键规范化为 "Benchmark_token"，按小写键查找
        self.assertEqual(calls[0][2].get("benchmark_token"), "tok-1")

    def test_task_not_found_fatal(self):
        fake, _ = make_fake([(404, {"code": "task_not_found", "message": "x"})])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            with self.assertRaises(TaskNotFoundError):
                self.adapter.list_challenges()

    def test_challenge_not_found(self):
        fake, _ = make_fake([(404, {"code": "challenge_not_found", "message": "x"})])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            with self.assertRaises(ChallengeNotFoundError):
                self.adapter.get_hint("nope")

    def test_invalid_state_on_start_means_active_limit(self):
        fake, _ = make_fake([(409, {"code": "invalid_state", "message": "x"})])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            with self.assertRaises(ActiveLimitError):
                self.adapter.start_challenge("c-01")

    def test_invalid_state_on_hint_means_task_ended(self):
        fake, _ = make_fake([(409, {"code": "invalid_state", "message": "x"})])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            with self.assertRaises(TaskEndedError):
                self.adapter.get_hint("c-01")

    def test_duplicate(self):
        fake, _ = make_fake([(409, {"code": "duplicate", "message": "x"})])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            with self.assertRaises(DuplicateError):
                self.adapter.start_challenge("c-01")

    def test_503_retries_then_success(self):
        seq = [(503, {"code": "resource_unavailable", "message": "x"})] * 3 + [
            (200, {"unique_code": "c-01", "container_addr": ["10.0.0.1:80"]})]
        fake, calls = make_fake(seq)
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake), \
             mock.patch("engine.tsec_adapter.time.sleep"):
            r = self.adapter.start_challenge("c-01")
        self.assertEqual(r["unique_code"], "c-01")
        self.assertEqual(len(calls), 4)  # 3 次 503 + 1 次成功

    def test_503_exhausted_raises(self):
        seq = [(503, {"code": "resource_unavailable", "message": "x"})] * 5
        fake, _ = make_fake(seq)
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake), \
             mock.patch("engine.tsec_adapter.time.sleep"):
            with self.assertRaises(ResourceUnavailableError):
                self.adapter.start_challenge("c-01")

    def test_start_url_has_unique_code(self):
        fake, calls = make_fake([(200, {"unique_code": "c-01", "container_addr": []})])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            self.adapter.start_challenge("c-01")
        self.assertIn("unique_code=c-01", calls[0][0])

    def test_health_check_true_on_200(self):
        fake, _ = make_fake([(200, [])])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            self.assertTrue(self.adapter.health_check())

    def test_health_check_false_on_404(self):
        fake, _ = make_fake([(404, {"code": "task_not_found", "message": "x"})])
        with mock.patch("engine.tsec_adapter.urllib.request.urlopen", fake):
            self.assertFalse(self.adapter.health_check())


if __name__ == "__main__":
    unittest.main()
