# -*- coding: utf-8 -*-
"""
腾讯财经 Provider

封装腾讯财经实时行情接口：
- A 股个股实时行情  (qt.gtimg.cn)
- 批量代码请求，自动解析 GBK 文本

Provider 只负责：
  1. 拼接 URL 和参数
  2. 解析原始文本（GBK → 结构化字段）
  3. 返回统一对象数组
"""

import logging
import re
from typing import Any, Dict, List, Optional

from core.request import get_text
from symbols import to_exchange_code

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────

TENCENT_QT_URL = "https://qt.gtimg.cn/"
TENCENT_REFERER = "https://gu.qq.com/"


# ── 腾讯字段位置映射（A 股）────────────────────────────────────

# qt.gtimg.cn 返回格式（~ 分隔）：
# 0: 名称, 1: 代码, 2: ... , 3: 当前价, 4: 昨收, 5: 今开,
# 29: 总市值, 30: ... , 31: 涨跌, 32: 涨跌幅, 33: 最高, 34: 最低,
# 36: 成交量(手), 37: 成交额(万), 38: 换手率, 43: 振幅, 44: 量比,
# 45: 委比, 47: 市盈率(动), ...

# 注意：腾讯字段索引可能随版本变化，这里是经验值


def get_realtime_quotes(codes: List[str]) -> List[Dict[str, Any]]:
    """
    批量获取 A 股实时行情

    API: qt.gtimg.cn/?q=sh600519,sz000858

    Args:
        codes: 股票代码列表，支持纯数字或带前缀格式

    Returns:
        [{code, name, price, prevClose, open, high, low, volume,
          amount, changePercent, change, turnoverRate, pe, amplitude,
          marketCap, exchange}, ...]
    """
    if not codes:
        return []

    # 统一转为 sh/sz/bj 前缀格式
    qt_codes = [to_exchange_code(c) for c in codes]
    joined = ",".join(qt_codes)

    url = f"{TENCENT_QT_URL}?q={joined}"
    text = get_text(url, referer=TENCENT_REFERER, encoding="gbk", timeout=8)

    return _parse_qt_text(text)


def get_realtime_quote(code: str) -> Optional[Dict[str, Any]]:
    """获取单只股票实时行情"""
    results = get_realtime_quotes([code])
    return results[0] if results else None


def _parse_qt_text(text: str) -> List[Dict[str, Any]]:
    """解析腾讯财经返回的 GBK 文本"""
    results: List[Dict[str, Any]] = []

    # 腾讯返回格式: v_sh600519="1~贵州茅台~600519~..."
    # 也兼容旧格式: var hq_str_sh600519="..."
    pattern = r'(?:var\s+hq_str_|v_)(\w+)="([^"]*)"'
    matches = re.findall(pattern, text)

    for qt_code, raw in matches:
        parts = raw.split("~")
        if len(parts) < 40:
            logger.warning(f"[Tencent] {qt_code} 字段不足 (got {len(parts)})")
            continue

        try:
            item = {
                "name": parts[1] if len(parts) > 1 else "",
                "code": parts[2] if len(parts) > 2 else qt_code,
                "price": _float(parts[3]),
                "prevClose": _float(parts[4]),
                "open": _float(parts[5]),
                "volume": _float(parts[6]),         # 成交量（手）
                "high": _float(parts[33]),
                "low": _float(parts[34]),
                "change": _float(parts[31]),
                "changePercent": _float(parts[32]),
                "turnoverRate": _float(parts[38]),
                "amount": _float(parts[37]),         # 成交额（万）
                "amplitude": _float(parts[43]) if len(parts) > 43 else 0.0,
                "pe": _float(parts[39]) if len(parts) > 39 else 0.0,
                "marketCap": _float(parts[45]) if len(parts) > 45 else 0.0,
                "exchange": _detect_exchange(qt_code),
            }
            results.append(item)
        except (ValueError, IndexError) as exc:
            logger.warning(f"[Tencent] 解析 {qt_code} 失败: {exc}")
            continue

    return results


def _float(val: str, default: float = 0.0) -> float:
    """安全转 float"""
    try:
        v = val.strip()
        if v in ("", "-", "--", "N/A"):
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _detect_exchange(qt_code: str) -> str:
    """从腾讯代码格式判断交易所"""
    code = qt_code.lower()
    if code.startswith("sh"):
        return "sh"
    if code.startswith("sz"):
        return "sz"
    if code.startswith("bj"):
        return "bj"
    return ""
