# -*- coding: utf-8 -*-
# DEPRECATED:
# This module is kept as fallback during the DataService migration.
# New external financial data access should be implemented in DataService providers.
# Do not add new third-party data source calls here.
# Target replacement: DataService marketService / EastMoneyMarketProvider.

"""
市场数据统一服务层

业务层统一入口。页面 / 组件 / 后端 API 只能调用此 Service，
不要直接调用 providers。

负责：
- 组合 provider 调用
- 缓存管理
- 兜底 / 降级策略
- 数据格式转换（适配现有前端接口）
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.errors import MarketDataError
from providers import eastmoney as em
from providers import tencent as tx
from symbols import to_board_secid, to_eastmoney_secid, to_exchange_code

logger = logging.getLogger(__name__)


def _akshare_disabled() -> bool:
    """Check if akshare fallback should be skipped.

    Set DISABLE_AKSHARE_FALLBACK=1 env var to bypass akshare.
    """
    if os.environ.get('DISABLE_AKSHARE_FALLBACK') == '1':
        return True
    return False


class MarketDataService:
    """
    市场数据服务（单例）

    - 前端 / 后端 API → 此 Service → Provider → 网络
    - 内置内存缓存 + 文件缓存兜底
    """

    _instance: Optional["MarketDataService"] = None

    # 缓存 TTL（秒）
    CACHE_TTL: Dict[str, int] = {
        "industry_boards": 300,
        "industry_spot": 60,
        "industry_constituents": 300,
        "a_stock_kline": 60,
        "realtime_quotes": 60,
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._cache: Dict[str, Any] = {}
        self._cache_time: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._initialized = True

    # ── 缓存 ──────────────────────────────────────────────────

    def _cache_get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                data, expire = self._cache[key]
                if time.time() < expire:
                    return data
        return None

    def _cache_get_stale(self, key: str) -> Optional[Any]:
        """获取过期缓存（兜底用）"""
        with self._lock:
            entry = self._cache.get(key)
            return entry[0] if entry else None

    def _cache_set(self, key: str, data: Any, ttl_key: str):
        with self._lock:
            ttl = self.CACHE_TTL.get(ttl_key, 60)
            self._cache[key] = (data, time.time() + ttl)

    # ── 行业板块列表 ──────────────────────────────────────────

    def get_industry_boards(
        self,
        *,
        page: int = 1,
        page_size: int = 500,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        获取行业板块列表

        兜底顺序：内存缓存 → 同花顺 (akshare) → 东方财富 API → 文件缓存 → 占位数据
        """
        cache_key = f"boards_{page}_{page_size}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached:
                return cached

        data_date = self._last_trading_date()
        boards: List[Dict] = []
        source = ""

        # 同花顺 (akshare) —— 唯一数据源
        try:
            boards = self._get_boards_akshare(page_size)
            if boards:
                source = "akshare"
                logger.info(f"[MarketData] 同花顺板块数据获取成功，共 {len(boards)} 条")
        except Exception as exc:
            logger.warning(f"[MarketData] akshare 板块列表失败: {exc}")

        # 缓存兜底
        if not boards:
            stale = self._cache_get_stale(cache_key)
            if stale and stale.get("data"):
                stale["source"] = "stale_cache"
                stale["is_stale"] = True
                return stale
            file_cached = self._load_file_cache("sector_rank", page_size)
            if file_cached:
                file_cached["source"] = "file_cache"
                file_cached["is_stale"] = True
                self._cache_set(cache_key, file_cached, "industry_boards")
                return file_cached
            # 无数据
            return {
                "success": False,
                "data": [],
                "error": "获取板块列表失败",
            }

        # 转换为兼容旧前端的格式
        result = {
            "success": True,
            "data": [_board_to_legacy(b) for b in boards],
            "total_count": len(boards),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": data_date,
            "source": source,
        }
        self._cache_set(cache_key, result, "industry_boards")
        self._save_file_cache("sector_rank", result)
        return result

    # ── 行业板块实时行情 ──────────────────────────────────────

    def get_industry_spot(self, board_code: str, use_cache: bool = True) -> Dict[str, Any]:
        """获取单个板块实时行情"""
        cache_key = f"spot_{board_code}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached:
                return cached

        try:
            spot = em.get_industry_spot(board_code)
            spot["update_time"] = datetime.now().strftime("%H:%M:%S")
            self._cache_set(cache_key, spot, "industry_spot")
            return spot
        except MarketDataError as exc:
            logger.warning(f"[MarketData] 板块 {board_code} 实时行情失败: {exc}")
            stale = self._cache_get_stale(cache_key)
            if stale:
                stale["is_stale"] = True
                return stale
            return {"code": board_code, "error": str(exc)}

    # ── 行业板块成份股 ────────────────────────────────────────

    def get_industry_constituents(
        self,
        board_code: str,
        *,
        page: int = 1,
        page_size: int = 500,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """获取行业板块成份股（East Money 失败时降级 akshare）"""
        cache_key = f"constituents_{board_code}_{page}_{page_size}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached:
                return cached

        stocks: List[Dict] = []
        source = ""

        # 方案 1: East Money
        try:
            stocks = em.get_industry_constituents(board_code, page=page, page_size=page_size)
            source = "eastmoney"
        except MarketDataError as exc:
            logger.warning(f"[MarketData] 板块 {board_code} 成份股 East Money 失败: {exc}")

        # 方案 2: akshare
        if not stocks:
            try:
                stocks = self._get_constituents_akshare(board_code, page_size)
                source = "akshare"
            except Exception as exc:
                logger.warning(f"[MarketData] 板块 {board_code} 成份股 akshare 失败: {exc}")

        if not stocks:
            stale = self._cache_get_stale(cache_key)
            if stale and stale.get("data"):
                stale["is_stale"] = True
                return stale
            return {"success": False, "data": [], "error": "获取成份股失败"}

        result = {
            "success": True,
            "data": stocks,
            "total_count": len(stocks),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
        }
        self._cache_set(cache_key, result, "industry_constituents")
        return result

    # ── A 股 K 线 ─────────────────────────────────────────────

    def get_a_stock_kline(
        self,
        stock_code: str,
        *,
        klt: str = "daily",
        fqt: str = "qfq",
        start_date: str = "",
        end_date: str = "",
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """获取 A 股 K 线（腾讯 → 东方财富 → akshare 兜底）"""
        cache_key = f"kline_{stock_code}_{klt}_{fqt}_{start_date}_{end_date}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached:
                return cached

        # 1) 腾讯财经（主要数据源）
        try:
            klines = tx.get_kline(
                stock_code,
                period=klt,
                adjust=fqt,
                start_date=_to_date_dash(start_date),
                end_date=_to_date_dash(end_date),
            )
            if klines:
                result = {
                    "success": True,
                    "data": klines,
                    "total_count": len(klines),
                    "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "tencent",
                }
                self._cache_set(cache_key, result, "a_stock_kline")
                return result
            logger.warning(f"[MarketData] 腾讯 K线 {stock_code} 返回空数据，尝试东方财富")
        except Exception as exc:
            logger.warning(f"[MarketData] 腾讯 K线 {stock_code} 失败: {exc}，尝试东方财富")

        # 2) 东方财富
        try:
            klines = em.get_a_stock_kline(
                stock_code,
                klt=klt,
                fqt=fqt,
                start_date=start_date,
                end_date=end_date,
            )
            result = {
                "success": True,
                "data": klines,
                "total_count": len(klines),
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "eastmoney",
            }
            self._cache_set(cache_key, result, "a_stock_kline")
            return result
        except MarketDataError as exc:
            logger.warning(f"[MarketData] EastMoney K线 {stock_code} 失败: {exc}")

        # 3) akshare 兜底
        if not _akshare_disabled():
            try:
                klines = self._get_kline_akshare(
                    stock_code,
                    klt=klt,
                    fqt=fqt,
                    start_date=start_date,
                    end_date=end_date,
                )
                if klines:
                    result = {
                        "success": True,
                        "data": klines,
                        "total_count": len(klines),
                        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "akshare",
                    }
                    self._cache_set(cache_key, result, "a_stock_kline")
                    return result
                logger.warning(f"[MarketData] akshare K线 {stock_code} 返回空数据")
            except Exception as exc:
                logger.warning(f"[MarketData] akshare K线 {stock_code} 也失败: {exc}")

        return {"success": False, "data": [], "error": "所有数据源（腾讯/东方财富/akshare）均不可用"}

    def _get_kline_akshare(
        self,
        stock_code: str,
        *,
        klt: str = "daily",
        fqt: str = "qfq",
        start_date: str = "",
        end_date: str = "",
    ) -> List[Dict[str, Any]]:
        """通过 akshare 获取 A 股历史日 K 线（仅支持日线前复权）"""
        import akshare as ak

        # akshare 仅支持日线
        if klt not in ("daily", ""):
            logger.warning(f"[MarketData] akshare 仅支持日线，收到 {klt}，降级为 daily")
            klt = "daily"

        # akshare 复权参数映射
        akshare_adjust = ""
        if fqt == "qfq":
            akshare_adjust = "qfq"
        elif fqt == "hfq":
            akshare_adjust = "hfq"

        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period=klt,
            start_date=start_date,
            end_date=end_date,
            adjust=akshare_adjust,
        )

        if df is None or df.empty:
            return []

        # DataFrame 列名映射（中文 → 英文）
        COLUMN_MAP = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "changePercent",
            "涨跌额": "change",
            "换手率": "turnoverRate",
        }

        result: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            item: Dict[str, Any] = {}
            for cn_col, en_col in COLUMN_MAP.items():
                if cn_col in df.columns:
                    val = row[cn_col]
                    if en_col == "date":
                        # akshare 返回的日期可能是 datetime / str "YYYY-MM-DD" → 统一 "YYYYMMDD"
                        item[en_col] = _format_kline_date(val)
                    elif en_col in ("volume", "amount"):
                        item[en_col] = str(val) if val is not None else "0"
                    else:
                        item[en_col] = _safe_float_str(val)
            if item.get("date"):
                result.append(item)

        return result

    # ── 腾讯实时行情 ───────────────────────────────────────────

    def get_realtime_quotes(
        self,
        codes: List[str],
        *,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """批量获取腾讯实时行情"""
        cache_key = f"qt_{'_'.join(sorted(codes))}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached:
                return cached

        try:
            quotes = tx.get_realtime_quotes(codes)
            result = {
                "success": True,
                "data": quotes,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._cache_set(cache_key, result, "realtime_quotes")
            return result
        except MarketDataError as exc:
            logger.warning(f"[MarketData] 腾讯行情失败: {exc}")
            return {"success": False, "data": [], "error": str(exc)}

    def get_realtime_quote(self, code: str, *, use_cache: bool = True) -> Dict[str, Any]:
        """获取单只股票腾讯实时行情"""
        result = self.get_realtime_quotes([code], use_cache=use_cache)
        if result["success"] and result["data"]:
            return result["data"][0]
        return {}

    # ── 市场指数（批量）────────────────────────────────────────

    def get_market_indices(self, secids: List[str]) -> Dict[str, Any]:
        """获取市场指数"""
        try:
            indices = em.get_market_indices(secids)
            return {
                "success": True,
                "data": indices,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except MarketDataError as exc:
            logger.warning(f"[MarketData] 指数失败: {exc}")
            return {"success": False, "data": [], "error": str(exc)}

    # ── 内部：akshare 备选 ────────────────────────────────────

    def _get_boards_akshare(self, limit: int) -> List[Dict]:
        """通过 akshare 获取板块（备选），提取包括主力资金在内的全部可用字段"""
        if _akshare_disabled():
            return []
        try:
            import akshare as ak
        except ImportError:
            return []

        # 优先同花顺（有净流入字段），次选东财
        for fn_name in ("stock_board_industry_summary_ths", "stock_board_industry_name_em"):
            try:
                fn = getattr(ak, fn_name, None)
                if not fn:
                    continue
                df = fn()
                if df is None or df.empty:
                    continue
                rows = []
                for _, row in df.head(max(limit, 80)).iterrows():
                    name = row.get("板块") or row.get("板块名称") or row.get("名称")
                    if not name:
                        continue
                    change = row.get("涨跌幅") or row.get("涨幅") or 0
                    # 同花顺: 净流入(亿) / 东财可能有不同的列名
                    net_inflow = row.get("净流入") or row.get("主力净流入") or 0
                    # akshare 返回的 总成交额 和 净流入 单位都是 亿
                    # 统一转为 元 存储（与 East Money f62 字段一致）
                    amount_yi = _safe_float(row.get("总成交额", 0))
                    inflow_yi = _safe_float(net_inflow)
                    rows.append({
                        "name": str(name),
                        "code": str(row.get("板块代码", row.get("代码", ""))),
                        "changePercent": _safe_float(change),
                        "price": _safe_float(row.get("均价", 0)),
                        "volume": _safe_float(row.get("总成交量", 0)),
                        "amount": amount_yi * 1e8,          # 亿 → 元
                        "mainNetInflow": inflow_yi * 1e8,   # 亿 → 元
                        "riseCount": int(_safe_float(row.get("上涨家数", 0))),
                        "fallCount": int(_safe_float(row.get("下跌家数", 0))),
                        "leadingStock": str(row.get("领涨股", "")),
                        "leadingStockPrice": _safe_float(row.get("领涨股-最新价", 0)),
                        "leadingStockChangePercent": _safe_float(row.get("领涨股-涨跌幅", 0)),
                    })
                rows.sort(key=lambda x: x["changePercent"], reverse=True)
                if rows:
                    return rows
            except Exception:
                continue
        return []

    def _get_constituents_akshare(self, board_code: str, limit: int) -> List[Dict]:
        """通过 akshare 获取板块成份股（备选）"""
        if _akshare_disabled():
            return []
        try:
            import akshare as ak
        except ImportError:
            return []

        try:
            df = ak.stock_board_cons_ths(board_code)
            if df is None or df.empty:
                return []
            rows = []
            for _, row in df.head(limit).iterrows():
                rows.append({
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "price": _safe_float(row.get("最新价", 0)),
                    "changePercent": _safe_float(row.get("涨跌幅", 0)),
                    "change": _safe_float(row.get("涨跌额", 0)),
                })
            return rows
        except Exception:
            return []

    # ── 内部：日期工具 ────────────────────────────────────────

    def _last_trading_date(self) -> str:
        now = datetime.now()
        day = now.date()
        minutes = now.hour * 60 + now.minute
        if day.weekday() >= 5 or minutes < 9 * 60 + 30:
            day -= datetime.timedelta(days=1)
        while day.weekday() >= 5:
            day -= datetime.timedelta(days=1)
        return day.strftime("%Y-%m-%d")

    # ── 内部：文件缓存 ────────────────────────────────────────

    def _cache_file_path(self, name: str) -> str:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "Data", f"{name}_cache.json")

    def _save_file_cache(self, name: str, data: Dict):
        try:
            path = self._cache_file_path(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_file_cache(self, name: str, limit: int) -> Optional[Dict]:
        try:
            path = self._cache_file_path(name)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            rows = cached.get("data") or []
            if not rows:
                return None
            if any("?" in str(r.get("name") or "") for r in rows):
                return None
            return {**cached, "data": rows[:limit]}
        except Exception:
            return None


# ── 单例 ──────────────────────────────────────────────────────

_service_instance: Optional[MarketDataService] = None


def get_market_data_service() -> MarketDataService:
    global _service_instance
    if _service_instance is None:
        _service_instance = MarketDataService()
    return _service_instance


# ── 格式适配（兼容旧前端）──────────────────────────────────────

def _board_to_legacy(b: Dict[str, Any]) -> Dict[str, Any]:
    """将新 provider 格式转为旧前端兼容格式"""
    raw_change = b.get("changePercent", 0)
    raw_inflow = b.get("mainNetInflow", 0)
    # 主力净流入占比：如果有净流入和成交额则计算，否则为 0
    raw_amount = b.get("amount", 0)
    inflow_pct = (raw_inflow / raw_amount * 100) if raw_amount else 0
    # 如果 provider 明确给了占比则用 provider 的值
    if b.get("mainNetInflowPercent"):
        inflow_pct = b["mainNetInflowPercent"]

    return {
        "name": b.get("name", ""),
        "code": b.get("code", ""),
        "change_pct": f"{round(raw_change, 2)}%",
        "main_inflow": _format_amount_yi(raw_inflow),
        "main_inflow_pct": f"{round(inflow_pct, 2)}%",
        "raw_change": raw_change,
        "raw_main_inflow": raw_inflow,
        "rise_count": b.get("riseCount", 0),
        "fall_count": b.get("fallCount", 0),
        "leading_stock": b.get("leadingStock", ""),
        "leading_pct": b.get("leadingStockChangePercent", 0),
    }


def _format_amount_yi(value: float) -> str:
    if not value:
        return "0亿"
    return f"{round(value / 1e8, 2)}亿"


def _format_pct(value: float) -> str:
    return f"{round(value, 2)}%"


def _safe_float(val, default=0.0):
    try:
        if val in (None, "", "-", "--"):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_float_str(val, default: str = "0.00") -> str:
    """将值安全转为浮点数字符串（保留两位小数）"""
    try:
        if val in (None, "", "-", "--"):
            return default
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return default


def _format_kline_date(val) -> str:
    """将 akshare 返回的日期转为 YYYYMMDD 字符串"""
    from datetime import date as dt_date, datetime as dt_datetime
    if isinstance(val, dt_datetime) or isinstance(val, dt_date):
        return val.strftime("%Y%m%d")
    s = str(val).strip()
    # YYYY-MM-DD → YYYYMMDD
    if "-" in s:
        return s.replace("-", "")
    # 已经是 YYYYMMDD 格式
    if s.isdigit() and len(s) == 8:
        return s
    return s


def _to_date_dash(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（腾讯接口需要），空字符串保持空字符串"""
    if not date_str:
        return ""
    s = str(date_str).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s
