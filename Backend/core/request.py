# -*- coding: utf-8 -*-
"""
统一 HTTP 请求层

特性：
- 超时控制
- 自动重试（指数退避）
- 统一请求头
- 错误归一化（转换为 MarketDataError）
- 支持多种会话后端（requests / curl_cffi）
- 为后续扩展 rate limit / cache 预留接口
"""

import logging
import time
import urllib3
from typing import Any, Dict, Optional

import requests

from .errors import (
    MarketDataError,
    RequestTimeoutError,
    ConnectionError as MDConnectionError,
    BadResponseError,
    ParseError,
)

urllib3.disable_warnings()

logger = logging.getLogger(__name__)

# ── curl_cffi 可选依赖 ──────────────────────────────────────────
try:
    from curl_cffi import requests as curl_requests

    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    curl_requests = None

# ── 默认配置 ────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 10  # 秒
DEFAULT_MAX_RETRIES = 2
DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ── East Money 熔断器 ────────────────────────────────────────────
# East Money API 频繁连接失败时，跳过它直接使用备用数据源，
# 避免每次请求都等待多层 session + curl_cffi 超时。

class EastMoneyCircuitBreaker:
    """East Money API 熔断器（模块级单例）"""

    EASTMONEY_DOMAINS = [
        "push2.eastmoney.com",
        "push2his.eastmoney.com",
        "17.push2.eastmoney.com",
        "91.push2.eastmoney.com",
        "29.push2.eastmoney.com",
    ]

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._state = "closed"          # closed | open | half_open
        self._open_time: float = 0.0
        self.FAILURE_THRESHOLD = 3       # 连续失败 3 次触发熔断
        self.RECOVERY_TIMEOUT = 300      # 5 分钟后尝试恢复

    @staticmethod
    def _is_eastmoney_url(url: str) -> bool:
        return any(d in url for d in EastMoneyCircuitBreaker.EASTMONEY_DOMAINS)

    def is_open(self, url: str = "") -> bool:
        """检查熔断器是否打开（应跳过 East Money 调用）"""
        if not self._is_eastmoney_url(url):
            return False
        with self._lock:
            if self._state == "closed":
                return False
            if self._state == "open":
                if time.time() - self._open_time >= self.RECOVERY_TIMEOUT:
                    self._state = "half_open"
                    logger.info("[CircuitBreaker] East Money 熔断器进入半开状态，尝试恢复")
                    return False
                return True
            # half_open: 放行一次探测请求
            return False

    def record_success(self, url: str = ""):
        """记录成功，闭合熔断器"""
        if not self._is_eastmoney_url(url):
            return
        with self._lock:
            was_open = self._state != "closed"
            self._consecutive_failures = 0
            self._state = "closed"
            if was_open:
                logger.info("[CircuitBreaker] East Money 熔断器已闭合，恢复正常")

    def record_failure(self, url: str = ""):
        """记录失败"""
        if not self._is_eastmoney_url(url):
            return
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.FAILURE_THRESHOLD and self._state != "open":
                self._state = "open"
                self._open_time = time.time()
                logger.warning(
                    "[CircuitBreaker] East Money 熔断器打开 "
                    f"（连续 {self._consecutive_failures} 次失败），"
                    f"将在 {self.RECOVERY_TIMEOUT}s 后重试"
                )


# 模块级单例
_circuit_breaker: Optional[EastMoneyCircuitBreaker] = None


def get_eastmoney_circuit_breaker() -> EastMoneyCircuitBreaker:
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = EastMoneyCircuitBreaker()
    return _circuit_breaker


# ── 公共 API ────────────────────────────────────────────────────

def get(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    referer: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    verify: bool = False,
    impersonate: Optional[str] = None,
) -> requests.Response:
    """
    发送 GET 请求（带重试 + 多会话兜底）

    Args:
        url: 请求地址
        params: 查询参数
        headers: 额外请求头（会与默认头合并）
        referer: Referer 头（便捷参数）
        timeout: 超时秒数
        max_retries: 最大重试次数（不含首次）
        verify: 是否验证 SSL 证书
        impersonate: curl_cffi 指纹伪装目标 (如 "chrome124")，为 None 时仅使用标准 requests

    Returns:
        requests.Response

    Raises:
        RequestTimeoutError: 超时
        MDConnectionError: 连接失败
        BadResponseError: 非 2xx 状态码
    """
    # ── 熔断检查：East Money 不可用时快速失败 ──
    cb = get_eastmoney_circuit_breaker()
    if cb.is_open(url):
        raise MDConnectionError(
            "East Money 熔断器已打开，跳过请求",
            source="circuit_breaker",
        )

    _headers = {**DEFAULT_HEADERS}
    if referer:
        _headers["Referer"] = referer
    if headers:
        _headers.update(headers)

    errors: list[str] = []
    is_em = cb._is_eastmoney_url(url)

    for attempt in range(max_retries + 1):
        # ── 尝试系统代理（匹配浏览器行为）──
        for session, label in _build_sessions(impersonate):
            try:
                if label.startswith("curl_cffi"):
                    resp = session.get(
                        url,
                        params=params,
                        headers=_headers,
                        timeout=timeout,
                        verify=verify,
                        impersonate=label.split(":")[1] if ":" in label else "chrome",
                    )
                else:
                    resp = session.get(
                        url,
                        params=params,
                        headers=_headers,
                        timeout=timeout,
                        verify=verify,
                    )
                resp.raise_for_status()
                if is_em:
                    cb.record_success(url)
                return resp
            except Exception as exc:
                short = _short_reason(exc)
                errors.append(f"{label}:{short}")
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, 4))

    # 所有尝试均失败 → 归一化错误
    if is_em:
        cb.record_failure(url)
    raise _normalize_error(errors)


def get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    referer: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    impersonate: Optional[str] = None,
) -> Any:
    """
    发送 GET 请求并返回解析后的 JSON

    Raises:
        ParseError: JSON 解析失败
        (同时可能抛出 get() 的异常)
    """
    resp = get(
        url,
        params=params,
        headers=headers,
        referer=referer,
        timeout=timeout,
        max_retries=max_retries,
        impersonate=impersonate,
    )
    try:
        return resp.json()
    except ValueError as exc:
        raise ParseError(
            f"JSON 解析失败: {exc}",
            source="request.get_json",
            detail=resp.text[:200],
        )


def get_text(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    referer: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    encoding: Optional[str] = None,
) -> str:
    """
    发送 GET 请求并返回文本

    用于腾讯财经等返回非 JSON 文本的接口。
    encoding 参数用于指定响应编码（如 'gbk'）。
    """
    resp = get(
        url,
        params=params,
        headers=headers,
        referer=referer,
        timeout=timeout,
        max_retries=max_retries,
    )
    if encoding:
        resp.encoding = encoding
    return resp.text


# ── 内部辅助 ────────────────────────────────────────────────────

def _build_sessions(impersonate: Optional[str] = None):
    """构建会话迭代器：env 代理 → 直连 → (可选) curl_cffi"""
    # 会话 1: 使用系统代理
    s1 = requests.Session()
    s1.trust_env = True
    yield s1, "env_proxy"

    # 会话 2: 不使用代理
    s2 = requests.Session()
    s2.trust_env = False
    yield s2, "direct"

    # 会话 3: curl_cffi Chrome 指纹模拟
    if CURL_CFFI_AVAILABLE and impersonate:
        targets = (
            [impersonate]
            if impersonate != "auto"
            else ["chrome124", "chrome120", "chrome110", "edge101"]
        )
        for target in targets:
            try:
                sess = curl_requests.Session()
                yield sess, f"curl_cffi:{target}"
            except Exception:
                continue


def _short_reason(exc: Exception) -> str:
    msg = str(exc)
    if "timed out" in msg.lower() or "timeout" in msg.lower():
        return "timeout"
    if "ProxyError" in msg or "proxy" in msg.lower():
        return "proxy_error"
    if "Connection aborted" in msg or "RemoteDisconnected" in msg or "Remote end closed" in msg:
        return "remote_closed"
    if "Connection" in msg or "connect" in msg.lower():
        return "connect_failed"
    return msg[:80]


def _normalize_error(errors: list[str]) -> MarketDataError:
    combined = "; ".join(errors[-3:])  # 只保留最近 3 条
    if "timeout" in combined.lower():
        return RequestTimeoutError(f"请求超时: {combined}", source="request")
    if "remote_closed" in combined or "proxy_error" in combined or "connect_failed" in combined:
        return MDConnectionError(f"连接失败: {combined}", source="request")
    return BadResponseError(f"请求失败: {combined}", source="request")
