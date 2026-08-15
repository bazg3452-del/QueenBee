# -*- coding: utf-8 -*-
"""TSecBench 平台 API 封装（纯标准库 urllib）。

错误码 -> 异常映射（按方案 v3.2 处置表）：
  task_not_found(404)        TaskNotFoundError        致命
  challenge_not_found(404)   ChallengeNotFoundError   记 skip
  invalid_state(409)         start 接口 -> ActiveLimitError（容器满）
                             其他接口 -> TaskEndedError（任务结束）
  duplicate(409)             DuplicateError           忽略
  resource_unavailable(503)  ResourceUnavailableError 退避重试
  internal_error(500)        InternalError            重试一次
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class AdapterError(Exception):
    def __init__(self, code, message, http_status=None, context=""):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.context = context
        super().__init__(f"[{http_status}] {code}: {message}")


class TaskNotFoundError(AdapterError):        # 404 task_not_found：token 无效，致命
    pass


class ChallengeNotFoundError(AdapterError):   # 404 challenge_not_found：记 skip
    pass


class ActiveLimitError(AdapterError):         # 409 容器满（仅 start 接口）
    pass


class TaskEndedError(AdapterError):           # 409 任务已结束：优雅退出
    pass


class DuplicateError(AdapterError):           # 409 duplicate：忽略
    pass


class ResourceUnavailableError(AdapterError):  # 503：退避重试
    pass


class InternalError(AdapterError):            # 500：重试一次
    pass


ERROR_MAP = {
    "task_not_found": TaskNotFoundError,
    "challenge_not_found": ChallengeNotFoundError,
    "duplicate": DuplicateError,
    "resource_unavailable": ResourceUnavailableError,
    "internal_error": InternalError,
}

RETRYABLE = {"resource_unavailable", "internal_error"}


class TSecBenchAdapter:
    def __init__(self, base_url, token, timeout=60):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ---------- 底层请求 ----------
    def _request(self, method, path, body=None, retries=0):
        """请求平台 API；HTTP 错误按错误码映射抛 AdapterError 子类。
        retries: 对可重试错误码（503/500）的额外重试次数，退避 2s/4s/8s。"""
        url = self.base_url + path
        headers = {"BENCHMARK_TOKEN": self.token}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last_status, last_payload = None, None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, data=data, method=method, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read().decode("utf-8")
                    return r.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as e:
                last_status = e.code
                try:
                    last_payload = json.loads(e.read().decode("utf-8"))
                except Exception:
                    last_payload = {}
                code = last_payload.get("code", "")
                if code in RETRYABLE and attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                self._raise(last_status, last_payload, path)
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise AdapterError("network_error", str(e), None, path)
        # 理论到不了这里；防御性兜底
        self._raise(last_status, last_payload or {}, path)

    def _raise(self, status, payload, path):
        code = payload.get("code", "unknown")
        msg = payload.get("message", "")
        if code == "invalid_state":
            # 上下文区分：仅 start 接口的 invalid_state 表示"容器数达上限"
            if path.startswith("/openapi/v1/challenges/start"):
                raise ActiveLimitError(code, msg, status, path)
            raise TaskEndedError(code, msg, status, path)
        cls = ERROR_MAP.get(code, AdapterError)
        raise cls(code, msg, status, path)

    # ---------- 平台接口 ----------
    def list_challenges(self):
        _, j = self._request("GET", "/openapi/v1/challenges")
        return j

    def start_challenge(self, unique_code, retries=3):
        """启动容器。503 退避重试（2s/4s/8s）后仍败抛 ResourceUnavailableError。"""
        path = "/openapi/v1/challenges/start?unique_code=" + urllib.parse.quote(unique_code)
        _, j = self._request("POST", path, retries=retries)
        return j

    def close_challenge(self, unique_code, retries=1):
        path = "/openapi/v1/challenges/close?unique_code=" + urllib.parse.quote(unique_code)
        _, j = self._request("POST", path, retries=retries)
        return j

    def get_hint(self, unique_code):
        path = "/openapi/v1/challenges/hint?unique_code=" + urllib.parse.quote(unique_code)
        _, j = self._request("GET", path)
        return j

    def health_check(self):
        """预检：带 token 调 list，200 即健康（同时验证 token 有效性与任务状态）。"""
        try:
            self.list_challenges()
            return True
        except Exception:
            return False
