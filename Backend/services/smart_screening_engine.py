import json
import math
import re
from datetime import datetime
from statistics import mean, pstdev


def safe_float(value):
    """安全转换数值字段，无法转换时返回 None。"""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    text = str(value).strip().replace('%', '').replace(',', '')
    if text in ('--', '-', 'null', 'None'):
        return None
    try:
        result = float(text)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def safe_json_loads(value, default=None):
    """安全解析 JSON 字符串，解析失败时返回默认值。"""
    if default is None:
        default = {}
    if value is None or value == '':
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def build_smart_screening_result(rows, top_n, include_review=False, page=1, page_size=20):
    """构建智能筛选结果，完成排雷、精选、打分与分页。"""
    raw_items = [build_smart_fund_item(row, None) for row in rows]
    peer_stats = _build_peer_stats(raw_items)
    items = [build_smart_fund_item(row, peer_stats) for row in rows]

    selected = []
    excluded_count = 0
    review_count = 0

    for item in items:
        if item['risk_triggers']:
            excluded_count += 1
            continue
        if item['review_triggers']:
            review_count += 1
            if not include_review:
                continue
        selected.append(item)

    selected.sort(key=lambda item: item.get('composite_score') or 0, reverse=True)
    top_n = max(1, min(int(top_n or 50), 500))
    selected = selected[:top_n]

    for index, item in enumerate(selected, 1):
        item['rank_no'] = index

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 200))
    offset = (page - 1) * page_size
    page_items = selected[offset:offset + page_size]

    return {
        'total': len(selected),
        'page': page,
        'page_size': page_size,
        'total_pages': math.ceil(len(selected) / page_size) if selected else 0,
        'top_n': top_n,
        'summary': {
            'candidate_count': len(rows),
            'selected_count': len(selected),
            'excluded_count': excluded_count,
            'review_count': review_count,
        },
        'data': page_items,
    }


def build_smart_fund_item(row, peer_stats):
    """将数据库 JOIN 行转换为带评分的智能筛选基金项。"""
    basic, risk, rank, trend, extra, industry_tag = row
    perf = safe_json_loads(getattr(basic, 'performance_json', None), {})

    item = {
        'fund_code': getattr(basic, 'fund_code', None),
        'fund_name': getattr(basic, 'fund_name', None),
        'fund_type': getattr(basic, 'fund_type', None),
        'industry_tag_name': getattr(industry_tag, 'industry_tag', None) if industry_tag else None,
        'return_1m': safe_float(perf.get('1_month_return')),
        'return_3m': safe_float(perf.get('3_month_return')),
        'return_6m': safe_float(perf.get('6_month_return')),
        'return_1y': safe_float(perf.get('1_year_return')) or safe_float(getattr(basic, 'return_1y', None)),
        'return_3y': safe_float(perf.get('3_year_return')),
        'max_drawdown_1y': safe_float(getattr(risk, 'max_drawdown_1y', None)) if risk else None,
        'volatility_1y': safe_float(getattr(risk, 'volatility_1y', None)) if risk else None,
        'sharpe_ratio_1y': safe_float(getattr(risk, 'sharpe_ratio_1y', None)) if risk else None,
        'calmar_ratio_1y': safe_float(getattr(risk, 'calmar_ratio_1y', None)) if risk else None,
        'calmar_ratio_3y': safe_float(getattr(risk, 'calmar_ratio_3y', None)) if risk else None,
        'rank_pct_1m': safe_float(getattr(rank, 'rank_pct_1m', None)) if rank else None,
        'rank_pct_3m': safe_float(getattr(rank, 'rank_pct_3m', None)) if rank else None,
        'rank_pct_6m': safe_float(getattr(rank, 'rank_pct_6m', None)) if rank else None,
        'rank_pct_1y': safe_float(getattr(rank, 'rank_pct_1y', None)) if rank else None,
        'rank_pct_2y': safe_float(getattr(rank, 'rank_pct_2y', None)) if rank else None,
        'rank_pct_3y': safe_float(getattr(rank, 'rank_pct_3y', None)) if rank else None,
        'pass_4433': bool(getattr(rank, 'pass_4433', 0) == 1) if rank else False,
        'risk_triggers': [],
        'review_triggers': [],
        'data_flags': [],
    }

    item['manager_tenure_years'] = extract_manager_tenure_years(getattr(extra, 'fund_managers_json', None) if extra else None)
    item['rcs'], rcs_flags = calculate_rcs(getattr(trend, 'net_worth_trend_json', None) if trend else None)
    item['data_flags'].extend(rcs_flags)
    item['dcr'], dcr_flags = calculate_dcr(item)
    item['data_flags'].extend(dcr_flags)
    item['valuation_percentile'], valuation_flags = calculate_valuation_percentile(item)
    item['data_flags'].extend(valuation_flags)
    item['style_lambda'], style_flags = calculate_style_lambda(
        getattr(trend, 'position_trend_json', None) if trend else None,
        getattr(extra, 'asset_allocation_json', None) if extra else None,
    )
    item['data_flags'].extend(style_flags)
    item['calmar_norm'] = normalize_calmar(item, peer_stats)

    apply_risk_filter(item, peer_stats)
    _calculate_composite_score(item)
    item['label'] = make_fund_label(item)
    item['risk_trigger_text'] = '；'.join(item['risk_triggers']) if item['risk_triggers'] else ''
    item['review_trigger_text'] = '；'.join(item['review_triggers']) if item['review_triggers'] else ''
    item['data_flag_text'] = '；'.join(item['data_flags']) if item['data_flags'] else ''
    return item


def apply_risk_filter(item, peer_stats):
    """执行第一层排雷过滤并写入触发项。"""
    return_1m = item.get('return_1m')
    return_1y = item.get('return_1y')
    if return_1m is not None and return_1y and return_1y > 0:
        item['pulse_ratio'] = round(return_1m / return_1y, 4)
        if item['pulse_ratio'] > 0.15:
            item['risk_triggers'].append('短期脉冲拉升')
    else:
        item['pulse_ratio'] = None
        item['data_flags'].append('涨幅透支比数据不足')

    volatility = item.get('volatility_1y')
    if volatility is None:
        item['data_flags'].append('年化波动率缺失')
    elif volatility > 1000:
        item['risk_triggers'].append('风险指标异常')
    else:
        limit = _volatility_limit(item.get('fund_type'), peer_stats)
        if volatility > limit:
            item['risk_triggers'].append('高波动题材基')

    if item.get('sharpe_ratio_1y') is not None and item.get('return_3m') is not None:
        if item['sharpe_ratio_1y'] > 5 and item['return_3m'] > 50:
            item['review_triggers'].append('动量透支嫌疑')

    tenure = item.get('manager_tenure_years')
    if tenure is None:
        item['data_flags'].append('基金经理任期缺失')
    elif tenure < 2:
        item['risk_triggers'].append('业绩参考价值不足')

    if not item.get('pass_4433'):
        item['risk_triggers'].append('4433未达标')


def calculate_rcs(net_worth_trend_json):
    """基于净值序列计算滚动收益一致性分数。"""
    points = _extract_nav_points(safe_json_loads(net_worth_trend_json, []))
    if len(points) < 60:
        return 50.0, ['RCS净值窗口不足，采用中性值']

    window = 252 if len(points) >= 252 else max(30, len(points) // 2)
    returns = []
    for index in range(window, len(points)):
        prev = points[index - window][1]
        curr = points[index][1]
        if prev and prev > 0 and curr and curr > 0:
            returns.append((curr / prev - 1) * 100)

    if not returns:
        return 50.0, ['RCS滚动收益不足，采用中性值']

    positive_ratio = sum(1 for value in returns if value > 0) / len(returns)
    volatility = pstdev(returns) if len(returns) > 1 else 0
    raw = positive_ratio * 100 - min(volatility, 50)
    score = max(0, min(100, raw + 25))
    flags = []
    if len(points) < 252 * 5:
        flags.append('RCS数据窗口不足，已按可得年限计算')
    return round(score, 2), flags


def calculate_dcr(item):
    """计算 DCR；第一版缺少基准序列时返回中性值。"""
    return 50.0, ['DCR缺少适配基准，采用中性值']


def normalize_calmar(item, peer_stats):
    """按同类基金池将卡玛比率归一化到 0-100。"""
    calmar = item.get('calmar_ratio_3y')
    if calmar is None:
        calmar = item.get('calmar_ratio_1y')
    if calmar is None:
        item['data_flags'].append('卡玛比率缺失，采用中性值')
        return 50.0

    if not peer_stats:
        return 50.0

    fund_type = item.get('fund_type') or '未知'
    values = peer_stats.get('calmar_by_type', {}).get(fund_type) or peer_stats.get('calmar_all', [])
    if len(values) < 2:
        return 50.0
    lower_count = sum(1 for value in values if value <= calmar)
    return round(max(0, min(100, lower_count / len(values) * 100)), 2)


def calculate_style_lambda(position_trend_json, asset_allocation_json):
    """根据仓位或资产配置波动计算风格稳定惩罚系数。"""
    positions = _extract_position_values(safe_json_loads(position_trend_json, []))
    if len(positions) >= 4:
        avg = abs(mean(positions)) or 1
        ratio = min(1, pstdev(positions) / avg) if len(positions) > 1 else 0
        return round(max(0.8, min(1.0, 1 - ratio * 0.2)), 4), []

    allocation = safe_json_loads(asset_allocation_json, {})
    if allocation:
        return 1.0, ['风格稳定性缺少季度序列，暂不惩罚']
    return 1.0, ['风格稳定性数据缺失，暂不惩罚']


def extract_manager_tenure_years(fund_managers_json):
    """从基金经理 JSON 中提取可确认的任职年限。"""
    managers = safe_json_loads(fund_managers_json, [])
    if isinstance(managers, dict):
        managers = managers.get('items') or managers.get('managers') or managers.get('data') or []
    if not isinstance(managers, list) or not managers:
        return None

    tenures = []
    for manager in managers:
        if not isinstance(manager, dict):
            continue
        tenure = _parse_years_from_text(
            manager.get('tenure') or manager.get('任职时间') or manager.get('term') or manager.get('workTime')
        )
        if tenure is None:
            tenure = _years_since(manager.get('startDate') or manager.get('start_date') or manager.get('start'))
        if tenure is not None:
            tenures.append(tenure)
    return round(max(tenures), 2) if tenures else None


def calculate_valuation_percentile(item):
    """计算行业估值分位；第一版无 PE/PB 数据时返回中性值。"""
    return 50.0, ['行业PE/PB估值分位缺失，采用中性值']


def make_fund_label(item):
    """根据分项指标生成基金标签。"""
    if item.get('review_triggers'):
        return '高位风险型'
    if len(item.get('data_flags') or []) >= 3:
        return '数据待完善'
    if item.get('valuation_percentile', 50) < 30 and (item.get('return_1y') is None or item.get('return_1y') <= 5):
        return '复苏型'
    if item.get('rcs', 0) >= 70 and item.get('dcr', 100) <= 50 and item.get('valuation_percentile', 100) < 50:
        return '稳健增长型'
    return '优质候选型'


def _build_peer_stats(items):
    calmar_by_type = {}
    calmar_all = []
    volatility_by_type = {}
    volatility_all = []

    for item in items:
        fund_type = item.get('fund_type') or '未知'
        calmar = item.get('calmar_ratio_3y') if item.get('calmar_ratio_3y') is not None else item.get('calmar_ratio_1y')
        if calmar is not None:
            calmar_by_type.setdefault(fund_type, []).append(calmar)
            calmar_all.append(calmar)
        volatility = item.get('volatility_1y')
        if volatility is not None and volatility <= 1000:
            volatility_by_type.setdefault(fund_type, []).append(volatility)
            volatility_all.append(volatility)

    return {
        'calmar_by_type': calmar_by_type,
        'calmar_all': calmar_all,
        'volatility_by_type': volatility_by_type,
        'volatility_all': volatility_all,
    }


def _calculate_composite_score(item):
    insufficient_rcs = any('RCS数据窗口不足' in flag for flag in item.get('data_flags', []))
    if insufficient_rcs:
        score = item['style_lambda'] * (
            0.40 * item['calmar_norm']
            + 0.30 * (100 - item['dcr'])
            + 0.15 * item['rcs']
            + 0.15 * (100 - item['valuation_percentile'])
        )
    else:
        score = item['style_lambda'] * (
            0.30 * item['calmar_norm']
            + 0.30 * (100 - item['dcr'])
            + 0.25 * item['rcs']
            + 0.15 * (100 - item['valuation_percentile'])
        )
    item['composite_score'] = round(max(0, min(100, score)), 2)


def _volatility_limit(fund_type, peer_stats):
    fund_type = fund_type or ''
    if peer_stats and any(keyword in fund_type for keyword in ('货币', '债券', 'QDII')):
        values = peer_stats.get('volatility_by_type', {}).get(fund_type) or []
        if len(values) >= 10:
            return _percentile(values, 90)
    if '货币' in fund_type:
        return 5
    if '债券' in fund_type:
        return 15
    if 'QDII' in fund_type:
        return 55
    return 35


def _percentile(values, pct):
    sorted_values = sorted(value for value in values if value is not None)
    if not sorted_values:
        return None
    index = int(round((len(sorted_values) - 1) * pct / 100))
    return sorted_values[max(0, min(index, len(sorted_values) - 1))]


def _extract_nav_points(raw_points):
    points = []
    if not isinstance(raw_points, list):
        return points
    for index, point in enumerate(raw_points):
        if isinstance(point, dict):
            date_value = point.get('date') or point.get('x') or index
            nav = safe_float(point.get('net_worth') or point.get('nav') or point.get('y'))
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            date_value = point[0]
            nav = safe_float(point[1])
        else:
            continue
        if nav is not None and nav > 0:
            points.append((date_value, nav))
    return points


def _extract_position_values(raw_points):
    values = []
    if not isinstance(raw_points, list):
        return values
    for point in raw_points[-12:]:
        if isinstance(point, dict):
            value = safe_float(point.get('y') or point.get('stock_position') or point.get('position') or point.get('value'))
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            value = safe_float(point[1])
        else:
            value = None
        if value is not None:
            values.append(value)
    return values


def _parse_years_from_text(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 365 if value > 50 else float(value)
    text = str(value)
    year_match = re.search(r'(\d+(?:\.\d+)?)\s*年', text)
    day_match = re.search(r'(\d+)\s*天', text)
    if year_match:
        years = float(year_match.group(1))
        if day_match:
            years += int(day_match.group(1)) / 365
        return years
    day_only = re.search(r'(\d+)\s*(?:日|天|days?)', text, re.IGNORECASE)
    if day_only:
        return int(day_only.group(1)) / 365
    return None


def _years_since(date_text):
    if not date_text:
        return None
    text = str(date_text).strip()[:10].replace('/', '-').replace('.', '-')
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
        try:
            start = datetime.strptime(text, fmt)
            delta = datetime.now() - start
            if delta.days >= 0:
                return delta.days / 365
        except ValueError:
            continue
    return None
