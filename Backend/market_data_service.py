# -*- coding: utf-8 -*-
"""
实时市场数据服务 (增强版)
使用 akshare 库获取 A 股市场的实时行情数据，包括：
- 核心指数（上证、深证、创业板等）
- 成交额、振幅
- 北向资金流向
- 主力资金流向
- 涨跌停统计
- 热门板块排行

参考 daily_stock_analysis 项目的数据获取模式
"""

import logging
import time
import random
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 尝试导入 akshare
try:
    import akshare as ak
    import pandas as pd
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    logger.warning("akshare 未安装，部分功能将不可用。请运行: pip install akshare")

try:
    import efinance as ef
    EF_AVAILABLE = True
except Exception:
    EF_AVAILABLE = False


@dataclass
class MarketIndex:
    """大盘指数数据"""
    code: str
    name: str
    price: float = 0.0
    change_pct: float = 0.0
    change_amt: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    amplitude: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    prev_close: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'price': self.price,
            'change_pct': self.change_pct,
            'change_amt': self.change_amt,
            'volume': self.volume,
            'amount': self.amount,
            'amplitude': self.amplitude,
            'high': self.high,
            'low': self.low,
            'open': self.open,
            'prev_close': self.prev_close,
        }


@dataclass
class MarketOverview:
    """市场概览数据"""
    date: str
    indices: List[Dict] = field(default_factory=list)
    up_count: int = 0
    down_count: int = 0
    flat_count: int = 0
    limit_up_count: int = 0
    limit_down_count: int = 0
    total_amount: float = 0.0
    north_flow: Dict = field(default_factory=dict)
    main_flow: Dict = field(default_factory=dict)
    top_sectors: List[Dict] = field(default_factory=list)
    bottom_sectors: List[Dict] = field(default_factory=list)


class MarketDataService:
    """
    实时市场数据服务
    使用 akshare 获取数据，带缓存和重试机制
    """
    
    _instance = None
    
    # 主要指数代码映射 (akshare 格式)
    MAIN_INDICES = {
        'sh000001': '上证指数',
        'sz399001': '深证成指',
        'sz399006': '创业板指',
        'sh000688': '科创50',
        'sh000300': '沪深300',
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MarketDataService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._cache = {}
        self._cache_time = {}
        self._cache_ttl = 60  # 缓存60秒
        self._initialized = True
        self._last_request_time = None
        self._min_interval = 1.0  # 最小请求间隔（秒）
    
    def _enforce_rate_limit(self):
        """强制执行速率限制，避免被封禁"""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()
    
    def _is_cache_valid(self, key: str) -> bool:
        """检查缓存是否有效"""
        if key not in self._cache_time:
            return False
        return (time.time() - self._cache_time[key]) < self._cache_ttl
    
    def _set_cache(self, key: str, data: Any):
        """设置缓存"""
        self._cache[key] = data
        self._cache_time[key] = time.time()
    
    def _get_cache(self, key: str) -> Optional[Any]:
        """获取缓存"""
        if self._is_cache_valid(key):
            return self._cache.get(key)
        return None
    
    def _call_akshare_with_retry(self, fn, name: str, attempts: int = 2):
        """带重试的 akshare 调用"""
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                self._enforce_rate_limit()
                return fn()
            except Exception as e:
                last_error = e
                logger.warning(f"[市场数据] {name} 获取失败 (attempt {attempt}/{attempts}): {e}")
                if attempt < attempts:
                    time.sleep(min(2 ** attempt, 5))
        logger.error(f"[市场数据] {name} 最终失败: {last_error}")
        return None
    
    def _fetch_em_json(self, url: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """获取东方财富 JSON 数据，依次尝试多种网络会话"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://quote.eastmoney.com/"
        }
        errors = []

        # 会话1: 使用系统代理（匹配浏览器行为）
        s1 = requests.Session()
        s1.trust_env = True
        try:
            resp = s1.get(url, params=params or {}, headers=headers, timeout=8, verify=False)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            errors.append(str(e)[:80])

        # 会话2: 不使用系统代理
        s2 = requests.Session()
        s2.trust_env = False
        try:
            resp = s2.get(url, params=params or {}, headers=headers, timeout=8, verify=False)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            errors.append(str(e)[:80])

        if errors:
            logger.warning(f"[市场数据] EM {url} 请求失败: {'; '.join(errors)}")
        return None
    
    def _safe_float(self, val, default=0.0):
        """安全转换为浮点数"""
        try:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return default
            return float(val)
        except (ValueError, TypeError):
            return default
    
    def get_index_realtime(self) -> List[Dict[str, Any]]:
        """
        获取核心指数实时行情
        使用 akshare 的 stock_zh_index_spot_sina 接口
        """
        cache_key = 'index_realtime'
        cached = self._get_cache(cache_key)
        if cached:
            logger.debug("[市场数据] 使用缓存的指数行情数据")
            return cached
        
        if not AKSHARE_AVAILABLE:
            logger.warning("[市场数据] akshare 不可用，返回空数据")
            return self._get_fallback_indices()
        
        indices = []
        
        try:
            logger.info("[市场数据] 获取主要指数实时行情...")
            
            # 使用新浪财经接口获取指数行情
            df = self._call_akshare_with_retry(
                ak.stock_zh_index_spot_sina,
                "指数行情"
            )
            
            if df is not None and not df.empty:
                for code, name in self.MAIN_INDICES.items():
                    # 查找对应指数
                    row = df[df['代码'] == code]
                    if row.empty:
                        # 尝试不带前缀查找
                        row = df[df['代码'].str.contains(code[-6:])]
                    
                    if not row.empty:
                        row = row.iloc[0]
                        prev_close = self._safe_float(row.get('昨收', 0))
                        high = self._safe_float(row.get('最高', 0))
                        low = self._safe_float(row.get('最低', 0))
                        
                        # 计算振幅
                        amplitude = 0
                        if prev_close > 0:
                            amplitude = (high - low) / prev_close * 100
                        
                        index_data = {
                            'code': code[-6:],  # 去掉前缀
                            'name': name,
                            'price': self._safe_float(row.get('最新价', 0)),
                            'change_pct': self._safe_float(row.get('涨跌幅', 0)),
                            'change_amt': self._safe_float(row.get('涨跌额', 0)),
                            'volume': self._safe_float(row.get('成交量', 0)),
                            'amount': self._safe_float(row.get('成交额', 0)),
                            'amplitude': round(amplitude, 2),
                            'high': high,
                            'low': low,
                            'open': self._safe_float(row.get('今开', 0)),
                            'prev_close': prev_close,
                        }
                        indices.append(index_data)
                
                logger.info(f"[市场数据] 获取到 {len(indices)} 个指数行情")
            
            if not indices:
                data = self._fetch_em_json(
                    "https://push2.eastmoney.com/api/qt/ulist/get",
                    {
                        "fltt": "2",
                        "invt": "2",
                        "fields": "f2,f3,f4,f12,f13,f14",
                        "secids": "1.000001,0.399001,0.399006"
                    }
                )
                if data and data.get("data") and data["data"].get("diff"):
                    diff = data["data"]["diff"]
                    mapping = {"1.000001": "上证指数", "0.399001": "深证成指", "0.399006": "创业板指"}
                    for item in diff:
                        secid = f"{item.get('f13')}.{item.get('f12')}"
                        name = mapping.get(secid)
                        if not name:
                            continue
                        indices.append({
                            "code": item.get("f12"),
                            "name": name,
                            "price": float(item.get("f2") or 0),
                            "change_pct": float(item.get("f3") or 0),
                            "change_amt": float(item.get("f4") or 0),
                            "volume": 0.0,
                            "amount": 0.0,
                            "amplitude": 0.0,
                            "high": 0.0,
                            "low": 0.0,
                            "open": 0.0,
                            "prev_close": 0.0
                        })
            
        except Exception as e:
            logger.error(f"[市场数据] 获取指数行情失败: {e}")
        
        if not indices:
            indices = self._get_fallback_indices()
        
        self._set_cache(cache_key, indices)
        return indices
    
    def _get_fallback_indices(self) -> List[Dict[str, Any]]:
        """获取备用指数数据（当API失败时）"""
        return [
            {'code': '000001', 'name': '上证指数', 'price': 0, 'change_pct': 0, 'amount': 0, 'amplitude': 0},
            {'code': '399001', 'name': '深证成指', 'price': 0, 'change_pct': 0, 'amount': 0, 'amplitude': 0},
            {'code': '399006', 'name': '创业板指', 'price': 0, 'change_pct': 0, 'amount': 0, 'amplitude': 0},
        ]
    
    def get_north_flow(self) -> Dict[str, Any]:
        """
        获取北向资金流向数据
        使用 akshare 的沪深港通接口
        注意：部分接口可能不稳定，使用多种备选方案
        """
        cache_key = 'north_flow'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        result = {
            'total': 0,
            'sh': 0,  # 沪股通（亿元）
            'sz': 0,  # 深股通（亿元）
            'update_time': datetime.now().strftime('%H:%M:%S'),
            'status': 'unknown'
        }
        
        if not AKSHARE_AVAILABLE:
            return result
        
        try:
            logger.info("[市场数据] 获取北向资金数据...")
            
            # 方案1: 尝试使用 stock_hsgt_hist_em 获取历史数据（取最新一天）
            try:
                df = self._call_akshare_with_retry(
                    lambda: ak.stock_hsgt_hist_em(symbol="沪股通"),
                    "沪股通历史"
                )
                if df is not None and not df.empty:
                    latest = df.iloc[-1]
                    for col in ['当日资金流入', '资金流入', '当日净流入']:
                        if col in df.columns:
                            result['sh'] = round(self._safe_float(latest[col]), 2)
                            break
                    logger.info(f"[市场数据] 沪股通: {result['sh']}亿")
            except Exception as e:
                logger.warning(f"[市场数据] 沪股通数据获取失败: {e}")
            
            try:
                df = self._call_akshare_with_retry(
                    lambda: ak.stock_hsgt_hist_em(symbol="深股通"),
                    "深股通历史"
                )
                if df is not None and not df.empty:
                    latest = df.iloc[-1]
                    for col in ['当日资金流入', '资金流入', '当日净流入']:
                        if col in df.columns:
                            result['sz'] = round(self._safe_float(latest[col]), 2)
                            break
                    logger.info(f"[市场数据] 深股通: {result['sz']}亿")
            except Exception as e:
                logger.warning(f"[市场数据] 深股通数据获取失败: {e}")
            
            # 计算总额
            result['total'] = round(result['sh'] + result['sz'], 2)
            if result['total'] != 0:
                result['status'] = 'trading'
            
            logger.info(f"[市场数据] 北向资金净流入: {result['total']}亿 (沪:{result['sh']} 深:{result['sz']})")
                
        except Exception as e:
            logger.error(f"[市场数据] 获取北向资金失败: {e}")
        
        self._set_cache(cache_key, result)
        return result
    
    def get_main_flow(self) -> Dict[str, Any]:
        """
        获取主力资金流向数据
        使用 akshare 的资金流向接口
        """
        cache_key = 'main_flow'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        result = {
            'main_net': 0,  # 主力净流入（亿元）
            'super_large': 0,  # 超大单净流入
            'large': 0,  # 大单净流入
            'medium': 0,  # 中单净流入
            'small': 0,  # 小单净流入
            'update_time': datetime.now().strftime('%H:%M:%S')
        }
        
        if not AKSHARE_AVAILABLE:
            return result
        
        try:
            logger.info("[市场数据] 获取主力资金数据...")
            
            # 获取大盘资金流向
            df = self._call_akshare_with_retry(
                ak.stock_market_fund_flow,
                "主力资金"
            )
            
            if df is not None and not df.empty:
                # 取最新一条数据
                latest = df.iloc[-1]
                
                # 主力净流入 = 超大单 + 大单
                super_large = self._safe_float(latest.get('超大单净流入', 0))
                large = self._safe_float(latest.get('大单净流入', 0))
                medium = self._safe_float(latest.get('中单净流入', 0))
                small = self._safe_float(latest.get('小单净流入', 0))
                
                result['super_large'] = round(super_large / 1e8, 2)
                result['large'] = round(large / 1e8, 2)
                result['medium'] = round(medium / 1e8, 2)
                result['small'] = round(small / 1e8, 2)
                result['main_net'] = round((super_large + large) / 1e8, 2)
                
                logger.info(f"[市场数据] 主力净流入: {result['main_net']}亿")
                
        except Exception as e:
            logger.error(f"[市场数据] 获取主力资金失败: {e}")
        
        self._set_cache(cache_key, result)
        return result
    
    def get_market_breadth(self) -> Dict[str, Any]:
        """
        获取市场广度数据（涨跌统计）
        使用 akshare 的 A 股实时行情接口
        """
        cache_key = 'market_breadth'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        result = {
            'up_count': 0,
            'down_count': 0,
            'flat_count': 0,
            'limit_up': 0,
            'limit_down': 0,
            'update_time': datetime.now().strftime('%H:%M:%S')
        }
        
        if not AKSHARE_AVAILABLE:
            return result
        
        try:
            logger.info("[市场数据] 获取市场涨跌统计...")
            
            # 获取全部A股实时行情
            df = self._call_akshare_with_retry(ak.stock_zh_a_spot_em, "A股实时行情")
            if (df is None or df.empty) and AKSHARE_AVAILABLE:
                try:
                    df = self._call_akshare_with_retry(ak.stock_zh_a_spot, "A股实时行情(Sina)")
                except Exception as _:
                    df = None
            if (df is None or df.empty) and EF_AVAILABLE:
                try:
                    df = ef.stock.get_realtime_quotes()
                except Exception as _:
                    df = None
            
            if df is not None and not df.empty:
                change_col = '涨跌幅'
                if change_col in df.columns:
                    df[change_col] = pd.to_numeric(df[change_col], errors='coerce')
                    
                    result['up_count'] = len(df[df[change_col] > 0])
                    result['down_count'] = len(df[df[change_col] < 0])
                    result['flat_count'] = len(df[df[change_col] == 0])
                    
                    # 涨停跌停统计（涨跌幅 >= 9.9% 或 <= -9.9%）
                    result['limit_up'] = len(df[df[change_col] >= 9.9])
                    result['limit_down'] = len(df[df[change_col] <= -9.9])
                
                logger.info(f"[市场数据] 涨:{result['up_count']} 跌:{result['down_count']} "
                          f"涨停:{result['limit_up']} 跌停:{result['limit_down']}")
                
        except Exception as e:
            logger.error(f"[市场数据] 获取市场广度失败: {e}")
        
        self._set_cache(cache_key, result)
        return result
    
    def get_hot_sectors(self) -> List[Dict[str, Any]]:
        """
        获取热门板块排行
        使用 akshare 的行业板块接口
        """
        cache_key = 'hot_sectors'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        sectors = []
        
        if not AKSHARE_AVAILABLE:
            return sectors
        
        try:
            logger.info("[市场数据] 获取热门板块...")
            
            # 获取行业板块行情
            # 优先使用同花顺接口 (东财接口容易失败)
            df = None
            if AKSHARE_AVAILABLE:
                try:
                    df = self._call_akshare_with_retry(ak.stock_board_industry_summary_ths, "行业板块(THS)")
                except Exception as _:
                    df = None
            if df is None or df.empty:
                data = self._fetch_em_json(
                    "https://push2.eastmoney.com/api/qt/clist/get",
                    {
                        "pn": "1",
                        "pz": "50",
                        "po": "1",
                        "np": "1",
                        "fltt": "2",
                        "invt": "2",
                        "fid": "f3",
                        "fs": "m:90 t:2",
                        "fields": "f12,f14,f3,f62"
                    }
                )
                if data and data.get("data") and data["data"].get("diff"):
                    em_rows = data["data"]["diff"]
                    sectors = []
                    for item in sorted(em_rows, key=lambda x: float(x.get("f3") or 0), reverse=True)[:10]:
                        sectors.append({
                            'code': str(item.get('f12', '')),
                            'name': str(item.get('f14', '')),
                            'change_pct': float(item.get('f3') or 0),
                            'up_count': 0,
                            'down_count': 0,
                            'leader': '',
                            'leader_pct': 0.0,
                            'amount': self._safe_float(item.get('f62', 0))
                        })
                    self._set_cache(cache_key, sectors)
                    return sectors
            
            if df is not None and not df.empty:
                change_col = '涨跌幅'
                if change_col in df.columns:
                    df[change_col] = pd.to_numeric(df[change_col], errors='coerce')
                    df = df.dropna(subset=[change_col])
                    
                    df_sorted = df.sort_values(by=change_col, ascending=False)
                    
                    for _, row in df_sorted.head(10).iterrows():
                        name = row.get('板块名称')
                        if name is None:
                            name = row.get('板块')
                        leader = row.get('领涨股票')
                        if leader is None:
                            leader = row.get('领 涨股')
                        leader_pct = row.get('领涨股票-涨跌幅')
                        if leader_pct is None:
                            leader_pct = row.get('领涨股-涨跌幅')
                        amount = row.get('总成交额')
                        if amount is None:
                            amount = row.get('总成交额', 0)
                        sector = {
                            'code': str(row.get('板块代码', '')),
                            'name': str(name or ''),
                            'change_pct': self._safe_float(row.get(change_col, 0)),
                            'up_count': int(self._safe_float(row.get('上涨家数', 0))),
                            'down_count': int(self._safe_float(row.get('下跌家数', 0))),
                            'leader': str(leader or ''),
                            'leader_pct': self._safe_float(leader_pct or 0),
                            'amount': self._safe_float(amount or 0),
                        }
                        sectors.append(sector)
                    
                    logger.info(f"[市场数据] 获取到 {len(sectors)} 个热门板块")
                    if sectors:
                        logger.info(f"[市场数据] 领涨板块: {[s['name'] for s in sectors[:3]]}")
                        
        except Exception as e:
            logger.error(f"[市场数据] 获取热门板块失败: {e}")
        
        self._set_cache(cache_key, sectors)
        return sectors
    
    def get_limit_up_stocks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取涨停股票列表
        使用 akshare 的涨停板接口
        """
        cache_key = f'limit_up_stocks_{limit}'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        stocks = []
        
        if not AKSHARE_AVAILABLE:
            return stocks
        
        try:
            logger.info("[市场数据] 获取涨停股票...")
            
            # 获取涨停股池
            today = datetime.now().strftime('%Y%m%d')
            df = self._call_akshare_with_retry(
                lambda: ak.stock_zt_pool_em(date=today),
                "涨停股池"
            )
            
            if df is not None and not df.empty:
                for _, row in df.head(limit).iterrows():
                    stock = {
                        'code': str(row.get('代码', '')),
                        'name': str(row.get('名称', '')),
                        'price': self._safe_float(row.get('最新价', 0)),
                        'change_pct': self._safe_float(row.get('涨跌幅', 0)),
                        'amount': round(self._safe_float(row.get('成交额', 0)) / 1e8, 2),
                        'reason': str(row.get('涨停原因', '')),
                        'first_time': str(row.get('首次封板时间', '')),
                        'last_time': str(row.get('最后封板时间', '')),
                        'open_count': int(self._safe_float(row.get('炸板次数', 0))),
                        'continuous_days': int(self._safe_float(row.get('连板数', 1))),
                    }
                    stocks.append(stock)
                
                logger.info(f"[市场数据] 获取到 {len(stocks)} 只涨停股票")
                
        except Exception as e:
            logger.error(f"[市场数据] 获取涨停股票失败: {e}")
        
        self._set_cache(cache_key, stocks)
        return stocks
    
    def get_concept_sectors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取概念板块排行
        使用 akshare 的概念板块接口
        """
        cache_key = f'concept_sectors_{limit}'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        sectors = []
        
        if not AKSHARE_AVAILABLE:
            return sectors
        
        try:
            logger.info("[市场数据] 获取概念板块...")
            
            df = self._call_akshare_with_retry(
                ak.stock_board_concept_name_em,
                "概念板块"
            )
            
            if df is not None and not df.empty:
                change_col = '涨跌幅'
                if change_col in df.columns:
                    df[change_col] = pd.to_numeric(df[change_col], errors='coerce')
                    df = df.dropna(subset=[change_col])
                    df_sorted = df.sort_values(by=change_col, ascending=False)
                    
                    for _, row in df_sorted.head(limit).iterrows():
                        sector = {
                            'code': str(row.get('板块代码', '')),
                            'name': str(row.get('板块名称', '')),
                            'change_pct': self._safe_float(row.get(change_col, 0)),
                            'up_count': int(self._safe_float(row.get('上涨家数', 0))),
                            'down_count': int(self._safe_float(row.get('下跌家数', 0))),
                            'leader': str(row.get('领涨股票', '')),
                            'leader_pct': self._safe_float(row.get('领涨股票-涨跌幅', 0)),
                        }
                        sectors.append(sector)
                    
                    logger.info(f"[市场数据] 获取到 {len(sectors)} 个概念板块")
                    
        except Exception as e:
            logger.error(f"[市场数据] 获取概念板块失败: {e}")
        
        self._set_cache(cache_key, sectors)
        return sectors
    
    def get_market_overview(self) -> Dict[str, Any]:
        """
        获取市场概览数据（整合所有数据）
        """
        logger.info("========== 开始获取市场概览数据 ==========")
        
        indices = self.get_index_realtime()
        north_flow = self.get_north_flow()
        main_flow = self.get_main_flow()
        breadth = self.get_market_breadth()
        hot_sectors = self.get_hot_sectors()
        limit_up_stocks = self.get_limit_up_stocks(5)
        
        # 计算两市总成交额
        total_amount = sum(idx.get('amount', 0) for idx in indices[:2]) / 1e8 if indices else 0
        
        result = {
            'indices': indices,
            'north_flow': north_flow,
            'main_flow': main_flow,
            'breadth': breadth,
            'hot_sectors': hot_sectors,
            'limit_up_stocks': limit_up_stocks,
            'total_amount': round(total_amount, 2),
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        logger.info("========== 市场概览数据获取完成 ==========")
        return result
    
    def generate_ai_strategy(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据市场数据生成 AI 策略建议
        """
        indices = market_data.get('indices', [])
        north_flow = market_data.get('north_flow', {})
        main_flow = market_data.get('main_flow', {})
        breadth = market_data.get('breadth', {})
        
        # 计算综合得分
        score = 50  # 基础分
        signals = []
        
        # 1. 指数涨跌
        sh_index = next((i for i in indices if '上证' in i.get('name', '')), None)
        if sh_index:
            change = sh_index.get('change_pct', 0)
            if change > 1:
                score += 10
                signals.append('大盘上涨')
            elif change > 0:
                score += 5
                signals.append('大盘微涨')
            elif change < -1:
                score -= 10
                signals.append('大盘下跌')
            elif change < 0:
                score -= 5
                signals.append('大盘微跌')
        
        # 2. 成交量
        total_amount = market_data.get('total_amount', 0)
        if total_amount > 10000:
            score += 10
            signals.append('成交放量')
        elif total_amount > 8000:
            score += 5
            signals.append('成交活跃')
        elif total_amount < 6000:
            score -= 5
            signals.append('成交萎缩')
        
        # 3. 北向资金
        north_total = north_flow.get('total', 0)
        if north_total > 50:
            score += 15
            signals.append('北向大幅流入')
        elif north_total > 20:
            score += 10
            signals.append('北向流入')
        elif north_total < -50:
            score -= 15
            signals.append('北向大幅流出')
        elif north_total < -20:
            score -= 10
            signals.append('北向流出')
        
        # 4. 主力资金
        main_net = main_flow.get('main_net', 0)
        if main_net > 100:
            score += 10
            signals.append('主力大幅流入')
        elif main_net > 0:
            score += 5
            signals.append('主力流入')
        elif main_net < -100:
            score -= 10
            signals.append('主力大幅流出')
        elif main_net < 0:
            score -= 5
            signals.append('主力流出')
        
        # 5. 涨跌家数
        up_count = breadth.get('up_count', 0)
        down_count = breadth.get('down_count', 0)
        if up_count > 0 and down_count > 0:
            ratio = up_count / (up_count + down_count)
            if ratio > 0.7:
                score += 15
                signals.append('普涨行情')
            elif ratio > 0.5:
                score += 5
                signals.append('多数上涨')
            elif ratio < 0.3:
                score -= 15
                signals.append('普跌行情')
            elif ratio < 0.5:
                score -= 5
                signals.append('多数下跌')
        
        # 6. 涨跌停比
        limit_up = breadth.get('limit_up', 0)
        limit_down = breadth.get('limit_down', 0)
        if limit_up > 100:
            score += 10
            signals.append(f'涨停{limit_up}家')
        elif limit_up > 50:
            score += 5
            signals.append(f'涨停{limit_up}家')
        if limit_down > 50:
            score -= 10
            signals.append(f'跌停{limit_down}家')
        elif limit_down > 20:
            score -= 5
            signals.append(f'跌停{limit_down}家')
        
        # 限制分数范围
        score = max(0, min(100, score))
        
        # 生成建议
        sentiment_desc = ""
        suggestion_detail = ""
        
        # 详细分析文案生成
        if score >= 70:
            sentiment = '乐观'
            sentiment_desc = "市场多头氛围浓厚，资金进场意愿强烈。"
            suggestion = '市场情绪积极，可适当增加仓位，关注热门板块龙头'
            suggestion_detail = "建议重点关注资金持续流入的强势板块，利用回调机会积极布局。当前市场风险偏好提升，可适当提高仓位，但需警惕短期乖离率过大的获利回吐风险。"
            risk_level = '低'
        elif score >= 55:
            sentiment = '偏多'
            sentiment_desc = "市场整体震荡上行，结构性机会为主。"
            suggestion = '市场整体偏强，可维持仓位，精选个股'
            suggestion_detail = "指数表现稳健，但板块轮动较快。建议“轻指数、重个股”，关注业绩确定性强的优质标的，避免盲目追高。资金流向分化，需甄别真假突破。"
            risk_level = '中低'
        elif score >= 45:
            sentiment = '中性'
            sentiment_desc = "多空双方势均力敌，市场进入观望期。"
            suggestion = '市场震荡整理，建议控制仓位，等待方向明确'
            suggestion_detail = "当前市场缺乏明确主线，成交量未能有效放大，上方压力显现。建议多看少动，控制仓位在半仓以下，耐心等待市场方向选择。可关注防御性板块进行避险。"
            risk_level = '中'
        elif score >= 30:
            sentiment = '偏空'
            sentiment_desc = "空头力量占据上风，市场情绪低迷。"
            suggestion = '市场偏弱，建议降低仓位，注意风险控制'
            suggestion_detail = "指数承压下行，资金流出迹象明显。建议严格执行止损纪律，降低仓位，避免接飞刀。耐心等待底部形态确立后再考虑进场。"
            risk_level = '中高'
        else:
            sentiment = '悲观'
            sentiment_desc = "市场恐慌情绪蔓延，下跌趋势明显。"
            suggestion = '市场风险较大，建议轻仓或观望，严格止损'
            suggestion_detail = "系统性风险释放中，切勿盲目抄底。建议保持极低仓位或空仓观望，现金为王，等待市场企稳信号出现。"
            risk_level = '高'
            
        # 动态补充分析
        if '成交放量' in signals:
            suggestion_detail += " 今日成交量有效放大，显示有增量资金入场，有利于行情延续。"
        elif '成交萎缩' in signals:
            suggestion_detail += " 缩量整理意味着变盘节点临近，需密切关注量能变化。"
            
        if '北向大幅流入' in signals:
            suggestion_detail += " 北向资金大幅净买入，外资对A股配置信心增强，核心资产有望受益。"
        elif '北向大幅流出' in signals:
            suggestion_detail += " 北向资金大幅流出，需警惕权重股抛压。"

        return {
            'score': score,
            'sentiment': sentiment,
            'signals': signals,
            'suggestion': suggestion, # 简短建议（用于标题或摘要）
            'suggestion_detail': f"{sentiment_desc} {suggestion_detail}", # 详细建议（用于正文）
            'risk_level': risk_level,
            'update_time': datetime.now().strftime('%H:%M:%S')
        }


# 单例获取函数
_market_data_service_instance = None

def get_market_data_service() -> MarketDataService:
    """获取市场数据服务单例"""
    global _market_data_service_instance
    if _market_data_service_instance is None:
        _market_data_service_instance = MarketDataService()
    return _market_data_service_instance
