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

from core.request import get_json, get_text
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


# ═══════════════════════════════════════════════════════════════
# K 线（历史走势）
# ═══════════════════════════════════════════════════════════════

TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_KLINE_REFERER = "https://gu.qq.com/"

# 周期 → 腾讯参数
TENCENT_PERIOD_MAP = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
}

# 复权 → 腾讯参数
TENCENT_ADJUST_MAP = {
    "qfq": "qfq",
    "hfq": "hfq",
    "none": "",
}


def get_kline(
    code: str,
    *,
    period: str = "daily",
    adjust: str = "qfq",
    start_date: str = "",
    end_date: str = "",
    count: int = 640,
) -> List[Dict[str, Any]]:
    """
    获取 A 股历史 K 线（腾讯财经）

    API: web.ifzq.gtimg.cn/appstock/app/fqkline/get

    Args:
        code: 股票代码（纯数字，如 002463）
        period: daily / weekly / monthly
        adjust: qfq / hfq / none
        start_date: 开始日期 YYYY-MM-DD（可选）
        end_date: 结束日期 YYYY-MM-DD（可选）
        count: 返回条数上限（默认 640）

    Returns:
        [{date, open, close, high, low, volume, changePercent}, ...]
    """
    qt_code = to_exchange_code(code)
    tc_period = TENCENT_PERIOD_MAP.get(period, "day")
    tc_adjust = TENCENT_ADJUST_MAP.get(adjust, "")

    # 拼接腾讯参数：市场代码,周期,,,条数,复权方式
    param = f"{qt_code},{tc_period},,,{count},{tc_adjust}"
    url = f"{TENCENT_KLINE_URL}?param={param}"

    resp = get_json(url, referer=TENCENT_KLINE_REFERER, timeout=15)

    if resp.get("code") != 0:
        logger.warning(f"[Tencent] K线 {code} 返回错误: code={resp.get('code')}, msg={resp.get('msg')}")
        return []

    data = resp.get("data", {})
    stock_data = data.get(qt_code, {})
    if not stock_data:
        return []

    # 确定数据键名：如 qfqday / day / hfqweek
    data_key = f"{tc_adjust}{tc_period}" if tc_adjust else tc_period
    klines_raw = stock_data.get(data_key, [])

    if not klines_raw:
        # 尝试不带复权的键名
        klines_raw = stock_data.get(tc_period, [])

    result: List[Dict[str, Any]] = []
    prev_close = None

    for item in klines_raw:
        if not isinstance(item, list) or len(item) < 6:
            continue

        raw_date, raw_open, raw_close, raw_high, raw_low, raw_volume = item[:6]

        # 日期过滤
        if start_date and raw_date < start_date:
            continue
        if end_date and raw_date > end_date:
            continue

        close_val = _float(raw_close)
        open_val = _float(raw_open)

        # 计算涨跌幅（相对上一交易日收盘价）
        change_pct = 0.0
        if prev_close is not None and prev_close != 0:
            change_pct = round((close_val - prev_close) / prev_close * 100, 2)

        entry = {
            "date": raw_date.replace("-", ""),
            "open": str(open_val),
            "close": str(close_val),
            "high": str(_float(raw_high)),
            "low": str(_float(raw_low)),
            "volume": str(_float(raw_volume)),
            "amount": "0",
            "amplitude": "0.00",
            "changePercent": str(change_pct),
            "change": str(round(close_val - open_val, 2)),
            "turnoverRate": "0.00",
        }
        result.append(entry)
        prev_close = close_val

    return result


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
