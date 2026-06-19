# -*- coding: utf-8 -*-
"""
股票 / 板块代码统一转换

支持的转换方向：
  - 纯数字代码 → 交易所前缀格式 (600519 → sh600519 / 1.600519)
  - 纯数字代码 → sz/sh/bj 前缀 (000858 → sz000858)
  - 板块代码 → 东方财富板块 secid (BK1027 → 90.BK1027)

规则：
  - 6xxxxx (60/68) → 上海交易所 (sh / 1.)
  - 0xxxxx (00/002/003) → 深圳主板 (sz / 0.)
  - 3xxxxx (30) → 深圳创业板 (sz / 0.)
  - 4xxxxx / 8xxxxx / 920xxx → 北京交易所 (bj / 0.)
  - BKxxxx → 东方财富板块 (90.)

后续如需完整支持港股/美股/基金/期货，在此模块扩展。
"""

from typing import Optional

# ── 交易所常量 ────────────────────────────────────────────────

SHANGHAI = "sh"
SHENZHEN = "sz"
BEIJING = "bj"

EASTMONEY_SH = "1"  # 东方财富市场编码：上交所
EASTMONEY_SZ = "0"  # 东方财富市场编码：深交所
EASTMONEY_BJ = "0"  # 东方财富市场编码：北交所（与深交所共用 0）

BOARD_MARKET = "90"  # 东方财富市场编码：板块


def _clean(code: str) -> str:
    """去除空格并统一为大写"""
    return str(code or "").strip().upper()


# ── 交易所判定 ────────────────────────────────────────────────

def get_exchange(code: str) -> Optional[str]:
    """根据纯数字代码判定交易所"""
    c = _clean(code)
    if not c.isdigit() or len(c) < 6:
        return None
    if c.startswith(("60", "68")):
        return SHANGHAI
    if c.startswith(("00", "30", "002", "003")):
        return SHENZHEN
    if c.startswith(("4", "8")) or c.startswith("920"):
        return BEIJING
    return None


# ── sz/sh/bj 格式 ─────────────────────────────────────────────

def to_exchange_code(code: str) -> str:
    """
    纯数字代码 → 交易所前缀格式

    Examples:
        600519 → sh600519
        000858 → sz000858
        300750 → sz300750
        688981 → sh688981
        430047 → bj430047
    """
    c = _clean(code)
    # 已有前缀 → 直接返回
    if c.startswith(("SH", "SZ", "BJ")):
        return c.lower()
    # 东方财富板块格式
    if c.startswith("BK"):
        return c

    exchange = get_exchange(c)
    if not exchange:
        return c.lower()
    return f"{exchange}{c.lower()}"


# ── 东方财富 1./0. 格式 ────────────────────────────────────────

def to_eastmoney_secid(code: str) -> str:
    """
    纯数字代码 → 东方财富 secid 格式

    Examples:
        600519 → 1.600519
        000858 → 0.000858
        300750 → 0.300750
        688981 → 1.688981
        430047 → 0.430047
    """
    c = _clean(code)
    # 纯数字
    if c.isdigit() and len(c) >= 6:
        exchange = get_exchange(c)
        market = EASTMONEY_SH if exchange == SHANGHAI else EASTMONEY_SZ
        return f"{market}.{c}"

    # 已有 1./0./90. 前缀
    if "." in c:
        return c

    return c


def to_board_secid(board_code: str) -> str:
    """
    板块代码 → 东方财富板块 secid

    Example:
        BK1027 → 90.BK1027
    """
    c = _clean(board_code)
    if c.startswith("BK"):
        return f"{BOARD_MARKET}.{c}"
    if c.startswith(f"{BOARD_MARKET}."):
        return c
    return c


# ── 批量转换 ──────────────────────────────────────────────────

def batch_to_exchange_codes(codes: list[str]) -> list[str]:
    """批量转换为交易所前缀格式"""
    return [to_exchange_code(c) for c in codes]


def batch_to_eastmoney_secids(codes: list[str]) -> list[str]:
    """批量转换为东方财富 secid 格式"""
    return [to_eastmoney_secid(c) for c in codes]
