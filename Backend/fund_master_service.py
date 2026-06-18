# -*- coding: UTF-8 -*-
"""
Fund-Master 核心功能服务模块
移植自 fund-master/fund.py，提供实时市场数据获取能力
包含：7x24快讯、行业板块排行、实时金价、历史金价、A股成交量、上证指数、市场指数汇总
"""

import datetime
import html
import json
import re
import time
import threading
import requests
import urllib3

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    curl_requests = None

urllib3.disable_warnings()

class FundMasterService:
    """Fund-Master 核心数据服务"""
    
    # 内存缓存（带过期时间）
    _cache = {}
    _cache_lock = threading.Lock()
    
    # 缓存过期时间配置（秒）
    CACHE_TTL = {
        'flash_news': 30,           # 快讯 30秒
        'sector_rank': 300,         # 板块排行 5分钟
        'market_index': 60,         # 市场指数 1分钟
        'gold_realtime': 60,        # 实时金价 1分钟
        'gold_history': 3600,       # 历史金价 1小时
        'a_volume_7days': 300,      # 7日成交量 5分钟
        'sse_30min': 60,            # 上证30分钟 1分钟
    }
    
    def __init__(self):
        self.session = requests.Session()
        # 市场数据接口经常被本机代理配置影响；这里禁用环境代理，避免 ProxyError 导致页面空白。
        self.session.trust_env = False
        self.baidu_session = None
        self._init_baidu_session()

    def _safe_float(self, value, default=0.0):
        try:
            if value in (None, "", "-", "--"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _get_json(self, url, params=None, headers=None, timeout=10):
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            verify=False,
        )
        response.raise_for_status()
        return response.json()

    def _short_error(self, error):
        message = str(error)
        if "ProxyError" in message:
            return "市场数据源代理连接失败"
        if "timed out" in message.lower() or "timeout" in message.lower():
            return "市场数据源请求超时"
        if "Connection" in message or "connect" in message.lower():
            return "市场数据源连接失败"
        return message[:120]
    
    def _init_baidu_session(self):
        """初始化百度股市通会话（使用 curl_cffi 绕过反爬）"""
        if CURL_CFFI_AVAILABLE:
            self.baidu_session = curl_requests.Session(impersonate="chrome")
            self.baidu_session.headers = {
                "accept": "application/vnd.finance-web.v1+json",
                "accept-language": "zh-CN,zh;q=0.9",
                "origin": "https://gushitong.baidu.com",
                "referer": "https://gushitong.baidu.com/",
                "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
            }
            # 预热会话
            try:
                self.baidu_session.get(
                    "https://gushitong.baidu.com/index/ab-000001",
                    headers={"user-agent": self.baidu_session.headers["user-agent"]},
                    timeout=10, 
                    verify=False
                )
            except Exception:
                pass
        else:
            # 降级使用普通 requests
            self.baidu_session = self.session
    
    def _get_cache(self, key: str):
        """获取缓存数据"""
        with self._cache_lock:
            if key in self._cache:
                data, expire_time = self._cache[key]
                if time.time() < expire_time:
                    return data
        return None

    def _get_stale_cache(self, key: str):
        """获取已过期缓存，用于外部数据源短暂不可用时兜底展示。"""
        with self._cache_lock:
            cached = self._cache.get(key)
            return cached[0] if cached else None
    
    def _set_cache(self, key: str, data, ttl_key: str):
        """设置缓存数据"""
        with self._cache_lock:
            ttl = self.CACHE_TTL.get(ttl_key, 60)
            self._cache[key] = (data, time.time() + ttl)
    
    # ==================== 7x24 快讯 ====================
    def get_flash_news(self, count: int = 20) -> dict:
        """
        获取7x24小时快讯
        数据源：百度股市通
        
        Args:
            count: 获取快讯数量，默认20条
            
        Returns:
            dict: {'success': bool, 'data': list, 'update_time': str}
        """
        cache_key = f'flash_news_{count}'
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        errors = []
        merged = []
        fetch_count = max(count * 2, 40)
        for source in (
            self._fetch_baidu_flash_news,
            self._fetch_eastmoney_flash_news,
            self._fetch_cls_flash_news,
        ):
            try:
                merged.extend(source(fetch_count))
            except Exception as e:
                errors.append(self._short_error(e))

        result = self._dedupe_sort_news(merged)[:count]
        if result:
            data = {
                "success": True,
                "data": result,
                "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "sources": list(sorted({item.get("source", "") for item in result if item.get("source")})),
            }
            self._set_cache(cache_key, data, 'flash_news')
            return data

        stale = self._get_stale_cache(cache_key)
        if stale and stale.get("data"):
            return {**stale, "source": "stale_cache"}
        return {"success": False, "error": "；".join(errors) or "获取快讯失败", "data": []}
        
        try:
            url = f"https://finance.pae.baidu.com/selfselect/expressnews?rn={count}&pn=0&tag=A股&finClientType=pc"
            response = self.baidu_session.get(url, timeout=10, verify=False)
            
            if response.json().get("ResultCode") == "0":
                news_list = response.json()["Result"]["content"]["list"]
                result = []
                
                for item in news_list:
                    evaluate = item.get("evaluate", "")
                    title = item.get("title", "")
                    if not title and item.get("content", {}).get("items"):
                        title = item["content"]["items"][0].get("data", "")
                    
                    publish_time = item.get("publish_time", "")
                    if publish_time:
                        publish_time = datetime.datetime.fromtimestamp(
                            int(publish_time)
                        ).strftime("%Y-%m-%d %H:%M:%S")
                    
                    # 相关股票
                    entities = item.get("entity", [])
                    related_stocks = [
                        {
                            "code": e.get("code", "").strip(),
                            "name": e.get("name", "").strip(),
                            "ratio": e.get("ratio", "").strip()
                        }
                        for e in entities if e.get("code")
                    ]
                    
                    result.append({
                        "title": title,
                        "evaluate": evaluate,  # 利好/利空/空
                        "publish_time": publish_time,
                        "related_stocks": related_stocks
                    })
                
                data = {
                    "success": True,
                    "data": result,
                    "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                self._set_cache(cache_key, data, 'flash_news')
                return data
            
            return {"success": False, "error": "获取快讯失败", "data": []}
        
        except Exception as e:
            return {"success": False, "error": str(e), "data": []}

    def _news_time_to_text(self, value):
        if not value:
            return ""
        try:
            if isinstance(value, (int, float)) or str(value).isdigit():
                ts = int(value)
                if ts > 10_000_000_000:
                    ts = ts // 1000
                return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        text = str(value).strip()
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}", text):
            return text if len(text.split(":")) >= 3 else f"{text}:00"
        if re.match(r"^\d{1,2}:\d{1,2}", text):
            return f"{datetime.datetime.now().strftime('%Y-%m-%d')} {text}:00"
        return text

    def _normalize_news_title(self, title):
        text = html.unescape(str(title or ""))
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def _dedupe_sort_news(self, items):
        seen = set()
        result = []
        for item in items:
            title = self._normalize_news_title(item.get("title"))
            if not title:
                continue
            key = re.sub(r"[^\w\u4e00-\u9fff]+", "", title.lower())[:80]
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title": title,
                "evaluate": item.get("evaluate", ""),
                "publish_time": self._news_time_to_text(item.get("publish_time")),
                "related_stocks": item.get("related_stocks") or [],
                "source": item.get("source", "")
            })

        def sort_key(item):
            try:
                return datetime.datetime.strptime((item.get("publish_time") or "")[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return datetime.datetime.min
        return sorted(result, key=sort_key, reverse=True)

    def _fetch_baidu_flash_news(self, count):
        url = f"https://finance.pae.baidu.com/selfselect/expressnews?rn={count}&pn=0&tag=A股&finClientType=pc"
        response = self.baidu_session.get(url, timeout=10, verify=False)
        payload = response.json()
        if payload.get("ResultCode") != "0":
            return []
        news_list = payload.get("Result", {}).get("content", {}).get("list", [])
        result = []
        for item in news_list:
            title = item.get("title", "")
            if not title and item.get("content", {}).get("items"):
                title = item["content"]["items"][0].get("data", "")
            entities = item.get("entity", [])
            related_stocks = [
                {
                    "code": e.get("code", "").strip(),
                    "name": e.get("name", "").strip(),
                    "ratio": e.get("ratio", "").strip()
                }
                for e in entities if e.get("code")
            ]
            result.append({
                "title": title,
                "evaluate": item.get("evaluate", ""),
                "publish_time": item.get("publish_time", ""),
                "related_stocks": related_stocks,
                "source": "百度股市通"
            })
        return result

    def _fetch_eastmoney_flash_news(self, count):
        url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_{count}_1_.html"
        response = self.session.get(
            url,
            headers={
                "Referer": "https://kuaixun.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            timeout=8,
            verify=False,
        )
        response.raise_for_status()
        text = response.text.strip()
        match = re.search(r"ajaxResult\s*=\s*(\{.*\})\s*;?\s*$", text, re.S)
        payload = json.loads(match.group(1) if match else text)
        news_list = payload.get("LivesList") or payload.get("data") or payload.get("list") or []
        result = []
        for item in news_list:
            result.append({
                "title": item.get("title") or item.get("digest") or item.get("simtitle"),
                "evaluate": "",
                "publish_time": item.get("showtime") or item.get("time") or item.get("ctime"),
                "related_stocks": [],
                "source": "东方财富"
            })
        return result

    def _fetch_cls_flash_news(self, count):
        payload = self._get_json(
            "https://www.cls.cn/nodeapi/telegraphList",
            params={
                "app": "CailianpressWeb",
                "category": "",
                "lastTime": "",
                "last_time": "",
                "os": "web",
                "refresh_type": "1",
                "rn": str(count),
                "sv": "8.4.6",
            },
            headers={
                "Referer": "https://www.cls.cn/telegraph",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            timeout=8
        )
        data = payload.get("data") or {}
        news_list = data.get("roll_data") or data.get("telegram") or data.get("list") or []
        result = []
        for item in news_list:
            result.append({
                "title": item.get("content") or item.get("title") or item.get("brief"),
                "evaluate": "",
                "publish_time": item.get("ctime") or item.get("time") or item.get("created_at"),
                "related_stocks": [],
                "source": "财联社"
            })
        return result
    
    # ==================== 行业板块排行 ====================
    def get_sector_rank(self, limit: int = 50) -> dict:
        """
        获取行业板块排行（按主力净流入排序）
        数据源：东方财富
        
        Args:
            limit: 返回板块数量，默认50
            
        Returns:
            dict: {'success': bool, 'data': list, 'update_time': str}
        """
        cache_key = f'sector_rank_{limit}'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "cb": "",
                "fid": "f62",
                "po": "1",
                "pz": str(limit),
                "pn": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
                "fs": "m:90 t:2",
                "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124,f1,f13"
            }
            headers = {
                "Referer": "https://fund.eastmoney.com/daogou/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            }
            
            resp_data = self._get_json(url, params=params, headers=headers, timeout=10)
            
            if resp_data.get("data"):
                result = []
                for bk in resp_data["data"]["diff"]:
                    # 主力净流入（转换为亿）
                    main_inflow = self._safe_float(bk.get("f62", 0))
                    main_inflow_str = f"{round(main_inflow / 100000000, 2)}亿" if main_inflow else "0亿"
                    
                    # 小单净流入（转换为亿）
                    small_inflow = self._safe_float(bk.get("f84", 0))
                    small_inflow_str = f"{round(small_inflow / 100000000, 2)}亿" if small_inflow else "0亿"
                    
                    result.append({
                        "name": bk.get("f14", ""),
                        "change_pct": f"{self._safe_float(bk.get('f3', 0))}%",
                        "main_inflow": main_inflow_str,
                        "main_inflow_pct": f"{round(self._safe_float(bk.get('f184', 0)), 2)}%",
                        "small_inflow": small_inflow_str,
                        "small_inflow_pct": f"{round(self._safe_float(bk.get('f87', 0)), 2)}%",
                        "raw_change": self._safe_float(bk.get('f3', 0)),
                        "raw_main_inflow": main_inflow
                    })
                
                # 按涨跌幅排序
                result.sort(key=lambda x: x['raw_change'], reverse=True)
                
                data = {
                    "success": True,
                    "data": result,
                    "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                self._set_cache(cache_key, data, 'sector_rank')
                return data
            
            stale = self._get_stale_cache(cache_key)
            if stale and stale.get("data"):
                stale = {**stale, "source": "stale_cache"}
                return stale
            fallback = self._get_sector_rank_fallback(limit)
            if fallback:
                data = {
                    "success": True,
                    "data": fallback,
                    "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "fallback"
                }
                self._set_cache(cache_key, data, 'sector_rank')
                return data
            return {"success": False, "error": "获取板块数据失败", "data": []}
        
        except Exception as e:
            stale = self._get_stale_cache(cache_key)
            if stale and stale.get("data"):
                stale = {**stale, "source": "stale_cache"}
                return stale
            fallback = self._get_sector_rank_fallback(limit)
            if fallback:
                data = {
                    "success": True,
                    "data": fallback,
                    "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "fallback"
                }
                self._set_cache(cache_key, data, 'sector_rank')
                return data
            return {"success": False, "error": self._short_error(e), "data": []}

    def _get_sector_rank_fallback(self, limit: int = 50) -> list:
        """返回结构稳定的板块占位数据，避免外部数据源故障时前端整块不可用。"""
        names = [
            "银行", "证券", "保险", "房地产开发", "半导体", "消费电子", "汽车整车", "医药商业",
            "白酒", "电池", "光伏设备", "通信设备", "软件开发", "游戏", "军工装备", "贵金属",
            "煤炭行业", "有色金属", "电力行业", "旅游酒店"
        ]
        result = []
        for name in names[:max(0, limit)]:
            result.append({
                "name": name,
                "change_pct": "0.00%",
                "main_inflow": "0亿",
                "main_inflow_pct": "0.00%",
                "small_inflow": "0亿",
                "small_inflow_pct": "0.00%",
                "raw_change": 0.0,
                "raw_main_inflow": 0.0
            })
        return result
    
    # ==================== 市场指数汇总 ====================
    def get_market_index(self) -> dict:
        """
        获取市场指数汇总（A股主要指数 + 全球指数）
        数据源：东方财富（更稳定）
        
        Returns:
            dict: {'success': bool, 'data': list, 'update_time': str}
        """
        cache_key = 'market_index'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        result = []
        try:
            # 使用东方财富 API 获取主要指数
            # A股/港股主要指数
            a_share_indices = [
                ("1.000001", "上证指数", "A股"),
                ("0.399001", "深证成指", "A股"),
                ("0.399006", "创业板指", "A股"),
                ("0.399005", "中小100", "A股"),
                ("1.000016", "上证50", "A股"),
                ("1.000300", "沪深300", "A股"),
                ("100.HSI", "恒生指数", "港股"),
                ("100.HSCEI", "国企指数", "港股"),
                ("100.HSTECH", "恒生科技", "港股"),
            ]
            
            # 全球主要指数
            global_indices = [
                ("100.NDX", "纳斯达克100", "全球"),
                ("100.DJIA", "道琼斯", "全球"),
                ("100.SPX", "标普500", "全球"),
                ("100.N225", "日经225", "全球"),
                ("100.KS11", "韩国综合", "全球"),
                ("100.FTSE", "英国富时100", "全球"),
                ("100.GDAXI", "德国DAX", "全球"),
                ("100.FCHI", "法国CAC40", "全球"),
                ("100.SENSEX", "印度SENSEX", "全球"),
            ]
            
            all_indices = a_share_indices + global_indices
            codes = ",".join([idx[0] for idx in all_indices])
            
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "fltt": "2",
                "invt": "2", 
                "fields": "f2,f3,f4,f12,f14",
                "secids": codes,
                "_": str(int(time.time() * 1000))
            }
            headers = {
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            resp_data = self._get_json(url, params=params, headers=headers, timeout=10)
            
            if resp_data.get("data") and resp_data["data"].get("diff"):
                idx_map = {idx[0].split(".")[1]: (idx[1], idx[2]) for idx in all_indices}
                expected_names = {idx[1] for idx in all_indices}
                
                for item in resp_data["data"]["diff"]:
                    code = item.get("f12", "")
                    name_market = idx_map.get(code)
                    if name_market:
                        price = item.get("f2", "-")
                        change_pct = item.get("f3", 0)
                        
                        # 格式化价格
                        price_num = self._safe_float(price, None)
                        if price_num is not None:
                            price = f"{price_num:.2f}"
                        
                        # 格式化涨跌幅
                        change_num = self._safe_float(change_pct, 0.0)
                        change_pct_str = f"{'+' if change_num >= 0 else ''}{change_num:.2f}%"
                        
                        result.append({
                            "name": name_market[0],
                            "price": str(price),
                            "change_pct": change_pct_str,
                            "market": name_market[1],
                            "raw_change": change_num
                        })

                returned_names = {item["name"] for item in result}
                for _, name, market in all_indices:
                    if name in expected_names and name not in returned_names:
                        result.append({
                            "name": name,
                            "price": "-",
                            "change_pct": "0.00%",
                            "market": market,
                            "raw_change": 0.0
                        })
            
            # 如果东方财富也失败，尝试新浪财经作为备选
            if not result:
                result = self._get_market_index_sina()
            
            if result:
                data = {
                    "success": True,
                    "data": result,
                    "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                self._set_cache(cache_key, data, 'market_index')
                return data
            
            return {"success": False, "error": "获取指数数据失败", "data": []}
        
        except Exception as e:
            stale = self._get_stale_cache(cache_key)
            if stale and stale.get("data"):
                stale = {**stale, "source": "stale_cache"}
                return stale
            result = result or self._get_market_index_sina()
            if result:
                data = {
                    "success": True,
                    "data": result,
                    "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "fallback"
                }
                self._set_cache(cache_key, data, 'market_index')
                return data
            return {"success": False, "error": self._short_error(e), "data": []}

    def _get_market_index_sina(self) -> list:
        """新浪指数兜底；如果网络仍不可用，返回结构稳定的占位行情。"""
        code_map = [
            ("sh000001", "上证指数", "A股"),
            ("sz399001", "深证成指", "A股"),
            ("sz399006", "创业板指", "A股"),
            ("sz399005", "中小100", "A股"),
            ("hkHSI", "恒生指数", "港股"),
            ("hkHSCEI", "国企指数", "港股"),
            ("gb_ixic", "纳斯达克", "美股"),
            ("gb_dji", "道琼斯", "美股"),
            ("gb_inx", "标普500", "美股"),
            ("b_NKY", "日经225", "全球"),
            ("b_KS11", "韩国综合", "全球"),
            ("b_UKX", "英国富时100", "全球"),
            ("b_DAX", "德国DAX", "全球"),
            ("b_CAC", "法国CAC40", "全球"),
            ("b_SENSEX", "印度SENSEX", "全球"),
        ]
        try:
            url = "https://hq.sinajs.cn/list=" + ",".join(code for code, _, _ in code_map)
            headers = {
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = self.session.get(url, headers=headers, timeout=8, verify=False)
            response.raise_for_status()
            text = response.text
            result = []
            for code, fallback_name, market in code_map:
                marker = f"var hq_str_{code}=\""
                start = text.find(marker)
                if start < 0:
                    continue
                start += len(marker)
                end = text.find("\";", start)
                payload = text[start:end]
                parts = payload.split(",")
                if len(parts) < 4:
                    continue

                if code.startswith(("sh", "sz")):
                    name = parts[0] or fallback_name
                    price = self._safe_float(parts[3])
                    prev_close = self._safe_float(parts[2])
                    pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
                elif code.startswith("hk"):
                    name = fallback_name
                    price = self._safe_float(parts[6] if len(parts) > 6 else parts[3])
                    pct = self._safe_float(parts[8] if len(parts) > 8 else 0)
                else:
                    name = fallback_name
                    price = self._safe_float(parts[1] if len(parts) > 1 else 0)
                    pct = self._safe_float(parts[2] if len(parts) > 2 else 0)

                result.append({
                    "name": name,
                    "price": f"{price:.2f}" if price else "-",
                    "change_pct": f"{'+' if pct >= 0 else ''}{pct:.2f}%",
                    "market": market,
                    "raw_change": pct
                })
            if result:
                return result
        except Exception:
            pass

        return [
            {"name": "上证指数", "price": "-", "change_pct": "0.00%", "market": "A股", "raw_change": 0.0},
            {"name": "深证成指", "price": "-", "change_pct": "0.00%", "market": "A股", "raw_change": 0.0},
            {"name": "创业板指", "price": "-", "change_pct": "0.00%", "market": "A股", "raw_change": 0.0},
            {"name": "中小100", "price": "-", "change_pct": "0.00%", "market": "A股", "raw_change": 0.0},
            {"name": "恒生指数", "price": "-", "change_pct": "0.00%", "market": "港股", "raw_change": 0.0},
            {"name": "国企指数", "price": "-", "change_pct": "0.00%", "market": "港股", "raw_change": 0.0},
            {"name": "纳斯达克100", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
            {"name": "道琼斯", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
            {"name": "标普500", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
            {"name": "日经225", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
            {"name": "韩国综合", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
            {"name": "英国富时100", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
            {"name": "德国DAX", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
            {"name": "法国CAC40", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
            {"name": "印度SENSEX", "price": "-", "change_pct": "0.00%", "market": "全球", "raw_change": 0.0},
        ]
    
    # ==================== 实时贵金属价格 ====================
    def get_gold_realtime(self) -> dict:
        """
        获取实时贵金属价格
        数据源：金投网/集金号
        
        Returns:
            dict: {'success': bool, 'data': list, 'update_time': str}
        """
        cache_key = 'gold_realtime'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        try:
            headers = {
                "accept": "*/*",
                "referer": "https://quote.cngold.org/gjs/gjhj.html",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
            }
            
            url = "https://api.jijinhao.com/quoteCenter/realTime.htm"
            params = {
                "codes": "JO_71,JO_92233,JO_92232,JO_75",
                "_": str(int(time.time() * 1000))
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
            raw = response.text.replace("var quote_json = ", "")
            data = json.loads(raw)
            
            result = []
            if data:
                code_map = {
                    "JO_71": "黄金T+D",
                    "JO_92233": "国际黄金",
                    "JO_92232": "国际白银",
                    "JO_75": "白银T+D"
                }
                
                for code in ["JO_71", "JO_92233", "JO_92232"]:
                    if code in data:
                        d = data[code]
                        update_time = ""
                        if d.get("time"):
                            update_time = datetime.datetime.fromtimestamp(
                                d["time"] / 1000
                            ).strftime("%Y-%m-%d %H:%M:%S")
                        
                        result.append({
                            "name": d.get("showName", code_map.get(code, code)),
                            "price": round(d.get("q63", 0), 2),
                            "change": round(d.get("q70", 0), 2),
                            "change_pct": f"{round(d.get('q80', 0), 2)}%",
                            "open": round(d.get("q1", 0), 2),
                            "high": round(d.get("q3", 0), 2),
                            "low": round(d.get("q4", 0), 2),
                            "prev_close": round(d.get("q2", 0), 2),
                            "update_time": update_time,
                            "unit": d.get("unit", "")
                        })
            
            data = {
                "success": True,
                "data": result,
                "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self._set_cache(cache_key, data, 'gold_realtime')
            return data
        
        except Exception as e:
            return {"success": False, "error": str(e), "data": []}
    
    # ==================== 黄金历史价格 ====================
    def get_gold_history(self, days: int = 10) -> dict:
        """
        获取黄金历史价格
        数据源：金投网/集金号
        
        Args:
            days: 获取天数，默认10天
            
        Returns:
            dict: {'success': bool, 'data': list, 'update_time': str}
        """
        cache_key = f'gold_history_{days}'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        try:
            headers = {
                "accept": "*/*",
                "referer": "https://quote.cngold.org/gjs/swhj_zghj.html",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            }
            
            # 中国黄金基础金价
            url = "https://api.jijinhao.com/quoteCenter/history.htm"
            params = {
                "code": "JO_52683",
                "style": "3",
                "pageSize": str(days),
                "needField": "128,129,70",
                "currentPage": "1",
                "_": int(time.time() * 1000)
            }
            response = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
            data1 = json.loads(response.text.replace("var quote_json = ", ""))["data"]
            
            # 周大福金价
            params["code"] = "JO_42660"
            response = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
            data2 = json.loads(response.text.replace("var quote_json = ", ""))["data"]
            
            result = []
            for i in range(len(data1)):
                gold = data1[i]
                t = gold.get("time", 0)
                date = datetime.datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d") if t else ""
                
                gold2 = data2[i] if i < len(data2) else {}
                
                result.append({
                    "date": date,
                    "china_gold_price": gold.get("q1", "N/A"),
                    "china_gold_change": str(gold.get("q70", "N/A")),
                    "zhoudafu_price": gold2.get("q1", "N/A"),
                    "zhoudafu_change": str(gold2.get("q70", "N/A"))
                })
            
            # 按日期倒序（最新的在前）
            result = result[::-1]
            
            data = {
                "success": True,
                "data": result,
                "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self._set_cache(cache_key, data, 'gold_history')
            return data
        
        except Exception as e:
            return {"success": False, "error": str(e), "data": []}
    
    # ==================== 近7日A股成交量 ====================
    def get_a_volume_7days(self) -> dict:
        """
        获取近7日A股成交量（沪深北三市）
        数据源：百度股市通
        
        Returns:
            dict: {'success': bool, 'data': list, 'update_time': str}
        """
        cache_key = 'a_volume_7days'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        try:
            url = "https://finance.pae.baidu.com/sapi/v1/metrictrend"
            params = {
                "financeType": "index",
                "market": "ab",
                "code": "000001",
                "targetType": "market",
                "metric": "amount",
                "finClientType": "pc"
            }
            
            response = self.baidu_session.get(url, params=params, timeout=10, verify=False)
            
            if str(response.json().get("ResultCode")) == "0":
                trend = response.json()["Result"]["trend"]
                result = []
                
                # 近8天的日期（包括今天）
                today = datetime.datetime.now()
                dates = [(today - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(8)]
                
                for date in dates:
                    total = trend[0]
                    sh = trend[1]
                    sz = trend[2]
                    bj = trend[3]
                    
                    total_data = [x for x in total["content"] if x["marketDate"] == date]
                    sh_data = [x for x in sh["content"] if x["marketDate"] == date]
                    sz_data = [x for x in sz["content"] if x["marketDate"] == date]
                    bj_data = [x for x in bj["content"] if x["marketDate"] == date]
                    
                    if total_data and sh_data and sz_data and bj_data:
                        result.append({
                            "date": date,
                            "total": total_data[0]["data"]["amount"] + "亿",
                            "shanghai": sh_data[0]["data"]["amount"] + "亿",
                            "shenzhen": sz_data[0]["data"]["amount"] + "亿",
                            "beijing": bj_data[0]["data"]["amount"] + "亿"
                        })
                
                data = {
                    "success": True,
                    "data": result,
                    "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                self._set_cache(cache_key, data, 'a_volume_7days')
                return data
            
            return {"success": False, "error": "获取成交量数据失败", "data": []}
        
        except Exception as e:
            return {"success": False, "error": str(e), "data": []}
    
    # ==================== 市场指数分时数据 ====================
    def _get_tencent_intraday(self, code: str) -> list:
        """
        获取腾讯财经分时数据
        code: sh000001 (上证), sz399001 (深证), sh000300 (沪深300)
        """
        try:
            url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
            params = {
                "code": code,
                "_": int(time.time() * 1000)
            }
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://gu.qq.com/"
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            data = response.json()
            
            # 解析数据结构: data -> code -> data -> data
            if data and data.get("code") == 0:
                stock_data = data.get("data", {}).get(code, {})
                minute_data = stock_data.get("data", {}).get("data", [])
                qt_data = stock_data.get("qt", {}).get(code, [])
                
                # 获取昨收价用于计算涨跌幅
                pre_close = 0
                if qt_data and len(qt_data) >= 5:
                    try:
                        pre_close = float(qt_data[4])
                    except:
                        pass
                
                if not pre_close and "prec" in stock_data.get("data", {}):
                     try:
                        pre_close = float(stock_data["data"]["prec"])
                     except:
                        pass
                
                result = []
                for point in minute_data:
                    # 格式: "0930 3350.12 12345 67890" (时间 价格 交易量 成交额)
                    parts = point.split(" ")
                    if len(parts) >= 2:
                        raw_time = parts[0]
                        time_str = f"{raw_time[:2]}:{raw_time[2:]}" # 0930 -> 09:30
                        price = float(parts[1])
                        
                        # 计算涨跌
                        change = 0
                        change_pct = "0.00%"
                        if pre_close:
                            change = round(price - pre_close, 2)
                            pct = (change / pre_close) * 100
                            change_pct = f"{round(pct, 2)}%"
                        
                        # 成交量处理 (腾讯返回的是手，不是金额或股数，这里简单处理)
                        volume = "-"
                        if len(parts) >= 3:
                            volume = parts[2]
                            
                        result.append({
                            "time": time_str,
                            "price": str(price),
                            "change": f"{'+' if change > 0 else ''}{change}",
                            "change_pct": change_pct,
                            "volume": volume
                        })
                return result
            return []
        except Exception as e:
            print(f"Error fetching tencent intraday for {code}: {e}")
            return []

    def get_indices_intraday(self) -> dict:
        """
        获取多指数分时数据（上证、深证、沪深300）
        使用腾讯财经作为数据源
        
        Returns:
            dict: {'sh': [], 'sz': [], 'hs300': [], 'update_time': str}
        """
        cache_key = 'indices_intraday'
        cached = self._get_cache(cache_key)
        if cached:
            return cached
            
        sh_data = self._get_tencent_intraday("sh000001")
        sz_data = self._get_tencent_intraday("sz399001")
        hs300_data = self._get_tencent_intraday("sh000300")
        
        data = {
            "success": True,
            "data": {
                "sh": sh_data,
                "sz": sz_data,
                "hs300": hs300_data
            },
            "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self._set_cache(cache_key, data, 'sse_30min') # 复用 sse_30min 的 TTL (1分钟)
        return data

    def get_sse_30min(self) -> dict:
        """
        获取上证指数分时数据（兼容旧接口，但提供全天数据）
        """
        # 直接复用新的分时数据获取逻辑，但只返回上证数据
        full_data = self.get_indices_intraday()
        if full_data["success"]:
            return {
                "success": True,
                "data": full_data["data"]["sh"],
                "update_time": full_data["update_time"]
            }
        return {"success": False, "error": "获取上证指数数据失败", "data": []}
    
    # ==================== 汇总数据接口 ====================
    def get_market_overview(self) -> dict:
        """
        获取市场概览（汇总所有关键数据）
        
        Returns:
            dict: 包含所有市场数据的汇总
        """
        return {
            "success": True,
            "market_index": self.get_market_index(),
            "gold_realtime": self.get_gold_realtime(),
            "sector_rank": self.get_sector_rank(limit=20),
            "a_volume_7days": self.get_a_volume_7days(),
            "sse_30min": self.get_sse_30min(),
            "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }


# 全局单例
_fund_master_service = None

def get_fund_master_service() -> FundMasterService:
    """获取 FundMasterService 单例"""
    global _fund_master_service
    if _fund_master_service is None:
        _fund_master_service = FundMasterService()
    return _fund_master_service
