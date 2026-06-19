# -*- coding: utf-8 -*-
"""
东方财富 Provider

封装所有东方财富行情接口：
- 行业板块列表   (17.push2.eastmoney.com/api/qt/clist/get)
- 行业板块实时行情 (91.push2.eastmoney.com/api/qt/stock/get)
- 行业板块成份股   (29.push2.eastmoney.com/api/qt/clist/get)
- A 股历史 K 线    (push2his.eastmoney.com/api/qt/stock/kline/get)
- 市场指数        (push2.eastmoney.com/api/qt/ulist.np/get)

Provider 只负责：
  1. 拼接 URL 和参数
  2. 解析原始字段（f12/f14/f3 等 → 语义化字段）
  3. 返回统一结构
Provider 不操作 UI 状态，不直接处理缓存。
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from core.request import get_json, get_text
from symbols import to_board_secid, to_eastmoney_secid

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────

EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
EM_REFERER = "https://quote.eastmoney.com/"

KLINE_UT = "7eea3edcaed734bea9cbfc24409ed989"


# ═══════════════════════════════════════════════════════════════
# 行业板块列表
# ═══════════════════════════════════════════════════════════════

def get_industry_boards(
    *,
    page: int = 1,
    page_size: int = 100,
    sort_field: str = "f3",
    sort_order: int = 1,
    fs_filter: str = "m:90 t:2 f:!50",
    fields: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    获取东方财富行业板块列表

    API: 17.push2.eastmoney.com/api/qt/clist/get

    Args:
        page: 页码（从 1 开始）
        page_size: 每页数量
        sort_field: 排序字段 (f3=涨跌幅)
        sort_order: 排序方向 (1=降序)
        fs_filter: 板块筛选表达式
        fields: 自定义字段列表

    Returns:
        [{code, name, price, changePercent, change, volume, amount,
          amplitude, turnoverRate, pe, pb, mainNetInflow, riseCount,
          fallCount, leadingStock, leadingStockChangePercent,
          mainNetInflowPercent}, ...]
    """
    if fields is None:
        fields = (
            "f12,f14,f2,f3,f4,f5,f6,f7,f8,f9,f23,"
            "f62,f104,f105,f128,f136,f184"
        )

    params = {
        "pn": str(page),
        "pz": str(page_size),
        "po": str(sort_order),
        "np": "1",
        "ut": EM_UT,
        "fltt": "2",
        "invt": "2",
        "fid": sort_field,
        "fs": fs_filter,
        "fields": fields,
    }

    url = f"https://17.push2.eastmoney.com/api/qt/clist/get?{urlencode(params, safe=':+,!')}"
    payload = get_json(url, referer=EM_REFERER, impersonate="auto")

    data = payload.get("data") or {}
    diff = data.get("diff") or []

    if isinstance(diff, dict):
        diff = list(diff.values())

    if not diff:
        logger.warning("[EastMoney] 行业板块列表返回空数据")
        return []

    return [_map_board_item(item) for item in diff if str(item.get("f12", "")).startswith("BK")]


def _map_board_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """将原始 f 字段映射为语义化字段"""
    return {
        "code": item.get("f12", ""),
        "name": item.get("f14", ""),
        "price": _float(item, "f2"),
        "changePercent": _float(item, "f3"),
        "change": _float(item, "f4"),
        "volume": _float(item, "f5"),
        "amount": _float(item, "f6"),
        "amplitude": _float(item, "f7"),
        "turnoverRate": _float(item, "f8"),
        "pe": _float(item, "f9"),
        "pb": _float(item, "f23"),
        "mainNetInflow": _float(item, "f62"),
        "riseCount": _int(item, "f104"),
        "fallCount": _int(item, "f105"),
        "leadingStock": item.get("f128", ""),
        "leadingStockChangePercent": _float(item, "f136"),
        "mainNetInflowPercent": _float(item, "f184"),
    }


# ═══════════════════════════════════════════════════════════════
# 行业板块实时行情
# ═══════════════════════════════════════════════════════════════

def get_industry_spot(board_code: str) -> Dict[str, Any]:
    """
    获取行业板块实时行情

    API: 91.push2.eastmoney.com/api/qt/stock/get

    Args:
        board_code: 板块代码，如 BK1027

    Returns:
        {latest, high, low, open, volume, amount, changePercent,
         amplitude, turnoverRate, change}

    注意：数值字段默认已做 /100 处理（price、涨跌幅等），volume/amount 保持原值。
    """
    secid = to_board_secid(board_code)

    params = {
        "fields": "f43,f44,f45,f46,f47,f48,f170,f171,f168,f169",
        "mpi": "1000",
        "invt": "2",
        "fltt": "1",
        "secid": secid,
    }

    url = f"https://91.push2.eastmoney.com/api/qt/stock/get?{urlencode(params)}"
    payload = get_json(url, referer=EM_REFERER, impersonate="auto")

    data = payload.get("data") or {}

    return {
        "code": board_code,
        "secid": secid,
        "latest": _float(data, "f43") / 100,
        "high": _float(data, "f44") / 100,
        "low": _float(data, "f45") / 100,
        "open": _float(data, "f46") / 100,
        "volume": _float(data, "f47"),        # 不除 100
        "amount": _float(data, "f48"),         # 不除 100
        "changePercent": _float(data, "f170") / 100,
        "amplitude": _float(data, "f171") / 100,
        "turnoverRate": _float(data, "f168") / 100,
        "change": _float(data, "f169") / 100,
    }


# ═══════════════════════════════════════════════════════════════
# 行业板块成份股
# ═══════════════════════════════════════════════════════════════

def get_industry_constituents(
    board_code: str,
    *,
    page: int = 1,
    page_size: int = 500,
) -> List[Dict[str, Any]]:
    """
    获取行业板块成份股

    API: 29.push2.eastmoney.com/api/qt/clist/get

    Returns:
        [{code, name, price, changePercent, change, volume, amount,
          amplitude, high, low, open, prevClose, turnoverRate, pe, pb}, ...]
    """
    params = {
        "pn": str(page),
        "pz": str(page_size),
        "po": "1",
        "np": "1",
        "ut": EM_UT,
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": f"b:{board_code} f:!50",
        "fields": "f12,f14,f2,f3,f4,f5,f6,f7,f15,f16,f17,f18,f8,f9,f23",
    }

    url = f"https://29.push2.eastmoney.com/api/qt/clist/get?{urlencode(params, safe=':+,!')}"
    payload = get_json(url, referer=EM_REFERER, impersonate="auto")

    data = payload.get("data") or {}
    diff = data.get("diff") or []

    if isinstance(diff, dict):
        diff = list(diff.values())

    return [_map_constituent_item(item) for item in diff]


def _map_constituent_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": item.get("f12", ""),
        "name": item.get("f14", ""),
        "price": _float(item, "f2"),
        "changePercent": _float(item, "f3"),
        "change": _float(item, "f4"),
        "volume": _float(item, "f5"),
        "amount": _float(item, "f6"),
        "amplitude": _float(item, "f7"),
        "high": _float(item, "f15"),
        "low": _float(item, "f16"),
        "open": _float(item, "f17"),
        "prevClose": _float(item, "f18"),
        "turnoverRate": _float(item, "f8"),
        "pe": _float(item, "f9"),
        "pb": _float(item, "f23"),
    }


# ═══════════════════════════════════════════════════════════════
# A 股历史 K 线
# ═══════════════════════════════════════════════════════════════

KLT_MAP = {
    "daily": "101",
    "weekly": "102",
    "monthly": "103",
}

FQT_MAP = {
    "none": "0",
    "qfq": "1",   # 前复权
    "hfq": "2",   # 后复权
}


def get_a_stock_kline(
    stock_code: str,
    *,
    klt: str = "daily",
    fqt: str = "qfq",
    start_date: str = "",
    end_date: str = "",
) -> List[Dict[str, Any]]:
    """
    获取 A 股历史 K 线

    API: push2his.eastmoney.com/api/qt/stock/kline/get

    Args:
        stock_code: 股票代码（纯数字，如 600519）
        klt: K 线类型 (daily/weekly/monthly)
        fqt: 复权类型 (none/qfq/hfq)
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD

    Returns:
        [{date, open, close, high, low, volume, amount, amplitude,
          changePercent, change, turnoverRate}, ...]
    """
    secid = to_eastmoney_secid(stock_code)
    klt_val = KLT_MAP.get(klt, "101")
    fqt_val = FQT_MAP.get(fqt, "1")

    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": KLINE_UT,
        "klt": klt_val,
        "fqt": fqt_val,
        "secid": secid,
        "beg": start_date,
        "end": end_date,
    }

    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{urlencode(params)}"
    payload = get_json(url, referer=EM_REFERER, impersonate="auto")

    data = payload.get("data") or {}
    klines_raw = data.get("klines") or []

    result: List[Dict[str, Any]] = []
    for line in klines_raw:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        result.append({
            "date": parts[0],
            "open": _float_str(parts[1]),
            "close": _float_str(parts[2]),
            "high": _float_str(parts[3]),
            "low": _float_str(parts[4]),
            "volume": _float_str(parts[5]),
            "amount": _float_str(parts[6]),
            "amplitude": _float_str(parts[7]),
            "changePercent": _float_str(parts[8]),
            "change": _float_str(parts[9]),
            "turnoverRate": _float_str(parts[10]),
        })

    return result


# ═══════════════════════════════════════════════════════════════
# 市场指数（批量）
# ═══════════════════════════════════════════════════════════════

def get_market_indices(secids: List[str]) -> List[Dict[str, Any]]:
    """
    获取市场指数实时行情（批量）

    API: push2.eastmoney.com/api/qt/ulist.np/get

    Args:
        secids: 东方财富 secid 列表，如 ["1.000001", "0.399001"]

    Returns:
        [{code, name, price, changePercent, change}, ...]
    """
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f2,f3,f4,f12,f14",
        "secids": ",".join(secids),
    }

    url = f"https://push2.eastmoney.com/api/qt/ulist.np/get?{urlencode(params)}"
    payload = get_json(url, referer=EM_REFERER, impersonate="auto")

    diff = (payload.get("data") or {}).get("diff") or []
    return [
        {
            "code": item.get("f12", ""),
            "name": item.get("f14", ""),
            "price": _float(item, "f2"),
            "changePercent": _float(item, "f3"),
            "change": _float(item, "f4"),
        }
        for item in diff
    ]


# ═══════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════

def _float(obj: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        v = obj.get(key)
        if v is None or v == "-" or v == "":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _int(obj: Dict[str, Any], key: str, default: int = 0) -> int:
    try:
        v = obj.get(key)
        if v is None or v == "-" or v == "":
            return default
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _float_str(val: str, default: float = 0.0) -> float:
    try:
        if val in ("-", "", None):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default
