# -*- coding: utf-8 -*-
"""
统一错误类型

用于归一化所有第三方 API 请求错误，让业务层无需关心具体网络库的异常类型。
"""


class MarketDataError(Exception):
    """市场数据通用错误"""

    def __init__(self, message: str, *, source: str = "", detail: str = ""):
        super().__init__(message)
        self.source = source
        self.detail = detail


class RequestTimeoutError(MarketDataError):
    """请求超时"""
    pass


class ConnectionError(MarketDataError):
    """连接失败（DNS / 代理 / 被拒）"""
    pass


class BadResponseError(MarketDataError):
    """响应状态码异常（非 2xx）"""
    pass


class ParseError(MarketDataError):
    """响应解析失败（JSON 解析 / 字段缺失）"""
    pass


class RateLimitError(MarketDataError):
    """速率限制"""
    pass
