# DEPRECATED:
# This module is kept as fallback during the DataService migration.
# New external financial data access should be implemented in DataService providers.
# Do not add new third-party data source calls here.
# Target replacement: DataService fundService (basic / nav / rank / holdings / managers / performance / detail).

import requests
import json
import re
from datetime import datetime
from typing import Dict, List, Any, Union
from stock_service import StockService, get_stock_info_ds_first

# --- 数据清洗器 (原 api_handler.py) ---

class FundDataCleaner:
    def __init__(self):
        self.cleaned_data = {}
        self.stock_service = StockService()

    def normalize_fund_code(self, value: Any) -> str:
        """Return fund codes as six-character strings so leading zeroes survive."""
        if value is None:
            return ''
        code = str(value).strip().strip('"').strip("'")
        if re.match(r'^\d{1,6}$', code):
            return code.zfill(6)
        return code
    
    def clean_js_variable(self, value: str) -> Any:
        """清洗JavaScript变量值"""
        if value is None:
            return None
            
        value_str = str(value).strip()
        
        # 处理布尔值
        if value_str.lower() in ['true', 'false']:
            return value_str.lower() == 'true'
        
        # 处理数字
        if re.match(r'^-?\d+\.?\d*$', value_str):
            try:
                return float(value_str) if '.' in value_str else int(value_str)
            except (ValueError, TypeError):
                return value_str
        
        # 处理字符串（去除引号）
        if (value_str.startswith('"') and value_str.endswith('"')) or \
           (value_str.startswith("'") and value_str.endswith("'")):
            return value_str[1:-1]
        
        return value_str
    
    def parse_timestamp(self, timestamp: int) -> str:
        """将时间戳转换为日期字符串"""
        try:
            return datetime.utcfromtimestamp(timestamp / 1000 + 8 * 60 * 60).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            return str(timestamp)
    
    def clean_rate(self, value: Any) -> Any:
        """清洗费率数据，统一返回数字或 None"""
        if value is None:
            return None
        value_str = str(value).strip()
        if not value_str or value_str in ['--', '-', 'null', 'undefined']:
            return None
        value_str = value_str.replace('%', '').strip()
        try:
            return float(value_str)
        except (ValueError, TypeError):
            return None

    def clean_array_data(self, data: Any, data_type: str = 'general') -> Any:
        """清洗数组数据"""
        if not data:
            return []
            
        if data_type == 'net_worth':
            # 处理单位净值走势数据
            cleaned = []
            for item in data:
                if isinstance(item, dict):
                    cleaned.append({
                        'date': self.parse_timestamp(item.get('x')),
                        'net_worth': item.get('y'),
                        'equity_return': item.get('equityReturn'),
                        'dividend': item.get('unitMoney')
                    })
            
            # 过滤首日异常数据（如面值1.0与实际净值100+差异巨大）
            # 新成立ETF常常第一天显示面值1.0，第二天显示实际参考净值(如100)，导致计算涨幅异常
            if len(cleaned) >= 2:
                try:
                    v0 = float(cleaned[0]['net_worth'])
                    v1 = float(cleaned[1]['net_worth'])
                    if v0 > 0 and abs((v1 - v0) / v0) > 0.5:
                        cleaned.pop(0)
                except (ValueError, TypeError):
                    pass
            
            return cleaned
            
        elif data_type == 'position':
            # 处理股票仓位数据
            cleaned = []
            for item in data:
                if isinstance(item, list) and len(item) >= 2:
                    cleaned.append({
                        'date': self.parse_timestamp(item[0]),
                        'position_percentage': item[1]
                    })
            return cleaned
            
        elif data_type == 'performance':
            # 处理业绩比较数据
            cleaned = []
            for item in data:
                if isinstance(item, dict):
                    series_data = []
                    for data_point in item.get('data', []):
                        if isinstance(data_point, list) and len(data_point) >= 2:
                            series_data.append({
                                'date': self.parse_timestamp(data_point[0]),
                                'value': data_point[1]
                            })
                    
                    cleaned.append({
                        'name': item.get('name'),
                        'data': series_data
                    })
            return cleaned
            
        elif data_type == 'ranking':
            # 处理排名数据
            cleaned = []
            for item in data:
                if isinstance(item, dict):
                    cleaned.append({
                        'date': self.parse_timestamp(item.get('x')),
                        'rank': item.get('y'),
                        'total_funds': item.get('sc')
                    })
            return cleaned
            
        else:
            # 通用数组处理
            return [self.clean_js_variable(item) for item in data]
    
    def clean_fund_info(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """清洗基金基本信息"""
        fund_code = self.normalize_fund_code(raw_data.get('fS_code'))
        
        # 尝试从本地缓存中获取基金类型
        fund_type = raw_data.get('fund_type_from_cache')
        if not fund_type:
            fund_type = '混合型'  # 默认值
        
        info = {
            'fund_name': self.clean_js_variable(raw_data.get('fS_name')),
            'fund_code': fund_code,
            'fund_type': fund_type,
            'original_rate': self.clean_rate(raw_data.get('fund_sourceRate')),
            'current_rate': self.clean_rate(raw_data.get('fund_Rate')),
            'min_subscription_amount': self.clean_js_variable(raw_data.get('fund_minsg')),
            'is_hb': self.clean_js_variable(raw_data.get('ishb'))
        }
        return info
    
    def clean_performance_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """清洗业绩数据"""
        performance = {
            '1_year_return': self.clean_js_variable(raw_data.get('syl_1n')),
            '6_month_return': self.clean_js_variable(raw_data.get('syl_6y')),
            '3_month_return': self.clean_js_variable(raw_data.get('syl_3y')),
            '1_month_return': self.clean_js_variable(raw_data.get('syl_1y'))
        }
        return performance

    @staticmethod
    def _normalize_market(market: str, code: str = '') -> str:
        """将各种来源的交易所标识统一为中文显示名。

        支持: sh/sz/bj/hk 短码 → 上交所/深交所/北交所/港交所
              中文名原样返回
              美股代码(字母组成) → 美股
        """
        if not market or str(market).strip() in ('', '--', 'None', 'null'):
            market = ''
        else:
            market = str(market).strip()

        # 已是标准中文名，直接返回
        if market in ('上交所', '深交所', '北交所', '港交所', '美股'):
            return market

        market_lower = market.lower()
        mapping = {
            'sh': '上交所', 'shanghai': '上交所',
            'sz': '深交所', 'shenzhen': '深交所',
            'bj': '北交所', 'beijing': '北交所',
            'hk': '港交所', 'hongkong': '港交所', 'hong kong': '港交所',
            'us': '美股', 'nasdaq': '美股', 'nyse': '美股', 'amex': '美股',
        }
        if market_lower in mapping:
            return mapping[market_lower]

        # 中文但不在已知列表 → 尝试匹配
        for keyword, label in [('上海', '上交所'), ('深圳', '深交所'), ('北京', '北交所'), ('香港', '港交所')]:
            if keyword in market:
                return label

        # DataService 可能返回空 market；尝试从代码推断
        if not market and code:
            code_str = str(code).strip()
            # 包含字母的代码（如 AAPL, NVDA10, TCEHY）→ 美股/境外
            if not code_str.isdigit():
                return '美股'

        # 无法识别但非空 → 保留原值
        return market if market else '--'

    def clean_portfolio_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """清洗投资组合数据"""
        # 获取原始代码列表
        stock_codes_raw = self.clean_array_data(raw_data.get('stockCodes'))
        
        # 转换为包含名称的对象列表
        enriched_stocks = []
        if stock_codes_raw:
            for code in stock_codes_raw:
                try:
                    stock_info = get_stock_info_ds_first(code)  # DataService-first adapter
                    display_code = self.stock_service.normalize_code(code)

                    enriched_stocks.append({
                        'code': display_code,
                        'original_code': code,
                        'name': stock_info.get('name', 'Unknown'),
                        'market': self._normalize_market(stock_info.get('market', '--'), display_code),
                        'ratio': 0  # 数据源缺失占比，设为0
                    })
                except Exception as e:
                    print(f"Error processing stock code {code}: {e}")
                    enriched_stocks.append({'code': str(code), 'name': 'Unknown', 'market': '--', 'ratio': 0})
        
        portfolio = {
            'stock_codes': enriched_stocks,
            'bond_codes': self.clean_array_data(raw_data.get('zqCodes')),
            # 为了让前端统一使用 enriched_stocks，我们将 stock_codes_new 也设为同样的数据
            # 或者是 None，让前端回退到 stock_codes。
            # 鉴于 stock_codes_new 格式复杂 (116.xxxx)，我们直接用处理好的数据覆盖它
            'stock_codes_new': enriched_stocks, 
            'bond_codes_new': self.clean_array_data(raw_data.get('zqCodesNew'))
        }
        return portfolio
    
    def clean_asset_allocation(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """清洗资产配置数据"""
        asset_data = raw_data.get('Data_assetAllocation', {})
        cleaned = {
            'categories': asset_data.get('categories', []),
            'series': []
        }
        
        for series in asset_data.get('series', []):
            cleaned_series = {
                'name': series.get('name'),
                'type': series.get('type'),
                'data': series.get('data', []),
                'yAxis': series.get('yAxis')
            }
            cleaned['series'].append(cleaned_series)
        
        return cleaned
    
    def clean_fund_manager(self, raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """清洗基金经理数据"""
        managers_data = raw_data.get('Data_currentFundManager', [])
        cleaned_managers = []
        
        for manager in managers_data:
            cleaned_manager = {
                'id': manager.get('id'),
                'name': manager.get('name'),
                'photo_url': manager.get('pic'),
                'star_rating': manager.get('star'),
                'work_experience': manager.get('workTime'),
                'managed_fund_size': manager.get('fundSize'),
                'ability_assessment': {
                    'average_score': manager.get('power', {}).get('avr'),
                    'categories': manager.get('power', {}).get('categories', []),
                    'scores': manager.get('power', {}).get('data', []),
                    'assessment_date': manager.get('power', {}).get('jzrq')
                },
                'performance': {
                    'categories': manager.get('profit', {}).get('categories', []),
                    'series': manager.get('profit', {}).get('series', []),
                    'assessment_date': manager.get('profit', {}).get('jzrq')
                }
            }
            cleaned_managers.append(cleaned_manager)
        
        return cleaned_managers
    
    def clean_holder_structure(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """清洗持有人结构数据"""
        holder_data = raw_data.get('Data_holderStructure', {})
        cleaned = {
            'categories': holder_data.get('categories', []),
            'series': []
        }
        
        for series in holder_data.get('series', []):
            cleaned_series = {
                'name': series.get('name'),
                'data': series.get('data', [])
            }
            cleaned['series'].append(cleaned_series)
        
        return cleaned
    
    def clean_same_type_funds(self, raw_data: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
        """清洗同类型基金数据"""
        same_type_data = raw_data.get('swithSameType', [])
        cleaned_categories = []
        
        for category in same_type_data:
            cleaned_funds = []
            for fund_str in category:
                parts = fund_str.split('_')
                if len(parts) >= 3:
                    fund_info = {
                        'code': parts[0],
                        'name': parts[1],
                        'return_rate': self.clean_js_variable(parts[2])
                    }
                    cleaned_funds.append(fund_info)
            cleaned_categories.append(cleaned_funds)
        
        return cleaned_categories
    
    def clean_all_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """清洗所有数据"""
        if raw_data.get('fundcode') is not None:
            raw_data['fundcode'] = self.normalize_fund_code(raw_data.get('fundcode'))
        if raw_data.get('fS_code') is not None:
            raw_data['fS_code'] = self.normalize_fund_code(raw_data.get('fS_code'))

        # 先清洗净值走势（图表数据源，来自 pingzhongdata，通常比 fundgz 更新更快）
        net_worth_trend = self.clean_array_data(
            raw_data.get('Data_netWorthTrend'), 'net_worth'
        )

        # 从走势中提取最新净值作为兜底（fundgz CDN 可能延迟）
        trend_latest_nav = None
        trend_latest_date = None
        if net_worth_trend:
            last_point = net_worth_trend[-1]
            trend_latest_nav = last_point.get('net_worth') if isinstance(last_point, dict) else None
            trend_latest_date = last_point.get('date') if isinstance(last_point, dict) else None

        # fundgz 提供的实时估值数据
        fundgz_nav = raw_data.get('dwjz')
        fundgz_date = raw_data.get('jzrq')

        # 兜底逻辑：如果 fundgz 的净值日期比走势最新日期旧，用走势数据覆盖
        net_worth = fundgz_nav
        net_worth_date = fundgz_date
        if trend_latest_nav is not None and trend_latest_date is not None:
            norm_trend_date = self._normalize_date_for_compare(trend_latest_date)
            norm_fundgz_date = self._normalize_date_for_compare(fundgz_date)
            if not norm_fundgz_date or norm_trend_date >= norm_fundgz_date:
                net_worth = trend_latest_nav
                net_worth_date = trend_latest_date

        cleaned_data = {
            'basic_info': self.clean_fund_info(raw_data),
            'performance': self.clean_performance_data(raw_data),
            'portfolio': self.clean_portfolio_data(raw_data),
            'realtime_estimate': {
                'name': raw_data.get('name'),
                'fund_code': raw_data.get('fundcode'),
                'net_worth': net_worth,
                'net_worth_date': net_worth_date,
                'estimate_value': raw_data.get('gsz'),
                'estimate_change': raw_data.get('gszzl'),
                'estimate_time': raw_data.get('gztime'),
            },
            'net_worth_trend': net_worth_trend,
            'accumulated_net_worth': self.clean_array_data(
                raw_data.get('Data_ACWorthTrend'), 'position'
            ),
            'position_trend': self.clean_array_data(
                raw_data.get('Data_fundSharesPositions'), 'position'
            ),
            'total_return_trend': self.clean_array_data(
                raw_data.get('Data_grandTotal'), 'performance'
            ),
            'ranking_trend': self.clean_array_data(
                raw_data.get('Data_rateInSimilarType'), 'ranking'
            ),
            'ranking_percentage': self.clean_array_data(
                raw_data.get('Data_rateInSimilarPersent'), 'position'
            ),
            'scale_fluctuation': raw_data.get('Data_fluctuationScale', {}),
            'holder_structure': self.clean_holder_structure(raw_data),
            'asset_allocation': self.clean_asset_allocation(raw_data),
            'performance_evaluation': raw_data.get('Data_performanceEvaluation', {}),
            'fund_managers': self.clean_fund_manager(raw_data),
            'subscription_redemption': raw_data.get('Data_buySedemption', {}),
            'same_type_funds': self.clean_same_type_funds(raw_data),
            'cleaning_timestamp': datetime.now().isoformat()
        }
        
        return cleaned_data

    @staticmethod
    def _normalize_date_for_compare(value: Any) -> str:
        if not value:
            return ''
        matched = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', str(value))
        if not matched:
            return str(value)
        return '{}-{:02d}-{:02d}'.format(
            matched.group(1),
            int(matched.group(2)),
            int(matched.group(3)),
        )

# --- 基金 API 客户端 ---

class FundAPI:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self.cleaner = FundDataCleaner()
        self._fund_type_cache = None  # 基金类型缓存
    
    def _load_fund_type_cache(self):
        """加载基金类型缓存"""
        if self._fund_type_cache is not None:
            return self._fund_type_cache
        
        try:
            import os
            base_dir = os.path.dirname(os.path.abspath(__file__))
            cache_path = os.path.join(base_dir, 'Data', 'fund_list_cache.json')
            
            if os.path.exists(cache_path):
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    funds = data.get('funds', [])
                    # 构建 fund_code -> fund_type 的映射
                    self._fund_type_cache = {f.get('CODE'): f.get('TYPE') for f in funds if f.get('CODE')}
            else:
                self._fund_type_cache = {}
        except Exception as e:
            print(f"Error loading fund type cache: {e}")
            self._fund_type_cache = {}
        
        return self._fund_type_cache

    def get_fund_data(self, fund_code: str) -> Union[Dict[str, Any], None]:
        """
        获取单只基金的完整清洗后数据。
        包括基本信息、业绩、持仓、净值走势等。
        """
        raw_data = self._fetch_raw_data(fund_code)
        if not raw_data:
            return None
        
        # 从本地缓存获取基金类型
        fund_type_cache = self._load_fund_type_cache()
        fund_type = fund_type_cache.get(fund_code, '')
        if fund_type:
            raw_data['fund_type_from_cache'] = fund_type
        
        # 使用 cleaner 清洗数据
        try:
            return self.cleaner.clean_all_data(raw_data)
        except Exception as e:
            print(f"Error cleaning data for {fund_code}: {e}")
            return None

    def search_funds(self, keyword: str) -> List[Dict[str, Any]]:
        """
        搜索基金（返回列表）
        """
        url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
        params = {
            'm': 1,
            'key': keyword
        }
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if 'Datas' in data:
                    # 过滤只保留基金类型的条目 (CATEGORYDESC == '基金')
                    funds = [item for item in data['Datas'] if item.get('CATEGORYDESC') == '基金']
                    return funds
            return []
        except Exception as e:
            print(f"Search error: {e}")
            return []

    def _parse_js_value(self, js_content: str, start_pos: int) -> tuple:
        """
        从指定位置解析 JS 值（数组、对象、字符串、数字等）
        返回 (解析后的值, 结束位置)
        """
        pos = start_pos
        while pos < len(js_content) and js_content[pos] in ' \t\n\r':
            pos += 1
        
        if pos >= len(js_content):
            return None, pos
        
        char = js_content[pos]
        
        # 数组
        if char == '[':
            depth = 1
            end_pos = pos + 1
            while end_pos < len(js_content) and depth > 0:
                c = js_content[end_pos]
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                elif c == '"' or c == "'":
                    # 跳过字符串内容
                    quote = c
                    end_pos += 1
                    while end_pos < len(js_content):
                        if js_content[end_pos] == quote and js_content[end_pos-1] != '\\':
                            break
                        end_pos += 1
                end_pos += 1
            return js_content[pos:end_pos], end_pos
        
        # 对象
        elif char == '{':
            depth = 1
            end_pos = pos + 1
            while end_pos < len(js_content) and depth > 0:
                c = js_content[end_pos]
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                elif c == '"' or c == "'":
                    quote = c
                    end_pos += 1
                    while end_pos < len(js_content):
                        if js_content[end_pos] == quote and js_content[end_pos-1] != '\\':
                            break
                        end_pos += 1
                end_pos += 1
            return js_content[pos:end_pos], end_pos
        
        # 字符串
        elif char == '"' or char == "'":
            quote = char
            end_pos = pos + 1
            while end_pos < len(js_content):
                if js_content[end_pos] == quote and js_content[end_pos-1] != '\\':
                    end_pos += 1
                    break
                end_pos += 1
            return js_content[pos:end_pos], end_pos
        
        # 其他（数字、布尔值等）- 读取到分号
        else:
            end_pos = pos
            while end_pos < len(js_content) and js_content[end_pos] != ';':
                end_pos += 1
            return js_content[pos:end_pos].strip(), end_pos
    
    def _fetch_raw_data(self, fund_code: str) -> Union[Dict[str, Any], None]:
        """
        获取原始基金数据（字典形式），包含所有JS变量。
        """
        data = {}
        
        # 1. 抓取 pingzhongdata 详细数据
        url = f"https://fund.eastmoney.com/pingzhongdata/{fund_code}.js"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                js_content = response.text
                
                # 查找所有 var xxx = 声明
                var_pattern = re.compile(r'var\s+(\w+)\s*=\s*')
                for match in var_pattern.finditer(js_content):
                    var_name = match.group(1)
                    value_start = match.end()
                    
                    # 解析值
                    raw_value, _ = self._parse_js_value(js_content, value_start)
                    
                    if raw_value:
                        try:
                            # 尝试 JSON 解析
                            if raw_value.startswith('[') or raw_value.startswith('{'):
                                # JS 中可能使用单引号，需要转换为双引号才能解析 JSON
                                json_value = raw_value.replace("'", '"')
                                data[var_name] = json.loads(json_value)
                            elif raw_value.startswith('"') and raw_value.endswith('"'):
                                data[var_name] = raw_value[1:-1]
                            elif raw_value.startswith("'") and raw_value.endswith("'"):
                                data[var_name] = raw_value[1:-1]
                            else:
                                data[var_name] = raw_value
                        except json.JSONDecodeError:
                            # 如果 JSON 解析失败，保留原始字符串
                            data[var_name] = raw_value
                        
        except Exception as e:
            print(f"Error fetching detail for {fund_code}: {e}")
            return None

        # 2. 抓取实时估值数据 (可选，用于补充实时信息)
        try:
            real_time_url = f"http://fundgz.1234567.com.cn/js/{fund_code}.js"
            response = requests.get(real_time_url, headers=self.headers, timeout=3)
            if response.status_code == 200:
                match = re.search(r"jsonpgz\((.*?)\);", response.text)
                if match:
                    rt_data = json.loads(match.group(1))
                    if rt_data:
                        # 这里的 key 可能和 pingzhongdata 不一样，如果需要合并，要注意 key 冲突
                        # 暂时作为一个子字段，或者直接合并
                        data.update(rt_data)
        except Exception:
            pass # 实时数据获取失败不影响整体
            
        if not data:
            return None

        # 确保 fS_code 存在
        if 'fS_code' not in data:
            data['fS_code'] = fund_code
            
        return data

if __name__ == "__main__":
    # 测试代码
    api = FundAPI()
    code = "019127" 
    print(f"Fetching data for {code}...")
    fund_data = api.get_fund_data(code)
    
    if fund_data:
        print("\n=== Data Fetch Success ===")
        print(f"Name: {fund_data['basic_info']['fund_name']}")
        print(f"Manager: {len(fund_data['fund_managers'])} managers recorded")
        print(f"Latest Net Worth: {fund_data['net_worth_trend'][-1] if fund_data['net_worth_trend'] else 'N/A'}")
        
        # 保存测试数据
        with open(f"fund_{code}_full.json", 'w', encoding='utf-8') as f:
            json.dump(fund_data, f, ensure_ascii=False, indent=2)
        print(f"Saved to fund_{code}_full.json")
    else:
        print("Failed to fetch data.")
