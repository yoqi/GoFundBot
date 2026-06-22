from flask import Flask, request, jsonify, g
from flask_cors import CORS
from database import init_db, SessionLocal
from models import (FundBasicInfo, FundTrend, FundEstimate, FundPortfolio, 
                    FundExtraData, FundWatchlist, FundWatchlistGroup, 
                    FundRiskMetrics, FundScreeningRank, DataFetchTask, FundNavHistory,
                    StockIndustry, FundIndustryTag, FundIndustryPerformance, FundEtfTracking)
from fund_api import FundAPI
from fund_list_cache import get_fund_list_cache
from ai_service import get_ai_service
from fund_master_routes import fund_master_bp
from data_service_routes import data_service_bp
from services.data_service_client import DataServiceClient, DataServiceError, get_data_service_client
from services.data_service_legacy_mapper import map_data_service_detail_to_legacy
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, and_, or_, func
from datetime import datetime, timedelta
import json
import math
import os
import sys
import time
import threading
import time
import re
import requests

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)  # 允许跨域请求

# 注册市场数据 Blueprint
app.register_blueprint(fund_master_bp)
app.register_blueprint(data_service_bp)

# ---------------------------------------------------------------------------
# DataService detail helpers (gray-release)
# ---------------------------------------------------------------------------

def _validate_data_service_fund_quality(mapped_data: dict):
    """Quality gate for DataService-mapped fund detail.

    Returns (passed: bool, issues: list[str]).
    Checks that critical fields exist and have meaningful data.
    """
    issues = []

    # 1. basic_info
    bi = mapped_data.get('basic_info', {}) or {}
    if not bi.get('fund_code'):
        issues.append('basic_info.fund_code missing')
    if not bi.get('fund_name'):
        issues.append('basic_info.fund_name missing')

    # 2. realtime_estimate
    est = mapped_data.get('realtime_estimate', {}) or {}
    if not est or est.get('missing'):
        issues.append('realtime_estimate missing')

    # 3. net_worth_trend non-empty
    nw = mapped_data.get('net_worth_trend', [])
    if not isinstance(nw, list) or len(nw) == 0:
        issues.append('net_worth_trend empty')

    # 4. portfolio stock_codes non-empty
    pf = mapped_data.get('portfolio', {}) or {}
    sc = pf.get('stock_codes', []) if isinstance(pf, dict) else []
    if not isinstance(sc, list) or len(sc) == 0:
        issues.append('portfolio.stock_codes empty')

    # 5. risk_metrics has at least some values
    rm = mapped_data.get('risk_metrics', {}) or {}
    rm_values = sum(1 for v in rm.values() if v is not None) if isinstance(rm, dict) else 0
    if rm_values == 0:
        issues.append('risk_metrics has no valid values')

    # 6. performance has core return fields
    perf = mapped_data.get('performance', {}) or {}
    has_perf = any(
        perf.get(k) is not None
        for k in ('1_year_return', '1_month_return', '3_month_return', '6_month_return')
    )
    if not has_perf:
        issues.append('performance missing core return fields')

    # 7. asset_allocation present
    aa = mapped_data.get('asset_allocation', {}) or {}
    if not aa or aa.get('missing'):
        issues.append('asset_allocation missing')

    passed = len(issues) == 0
    return passed, issues


def _get_fund_detail_from_data_service(fund_code: str) -> dict:
    """Fetch fund detail from DataService and map to legacy structure."""
    client = get_data_service_client()
    ds_payload = client.get_fund_detail(fund_code)
    result = map_data_service_detail_to_legacy(ds_payload)
    result['_data_source'] = {
        "mode": "data_service",
        "mapped": True,
        "completeness_score": 70,
        "replacement_tier": "tier2_gray_validation"
    }
    return result


def _try_data_service_fund_detail(fund_code: str):
    """Try DataService detail; return (mapped_data, quality_passed, issues) or None."""
    try:
        mapped = _get_fund_detail_from_data_service(fund_code)
        passed, issues = _validate_data_service_fund_quality(mapped)
        return mapped, passed, issues
    except DataServiceError as e:
        print(f"auto mode: DataService failed for {fund_code}, fallback to legacy: {e}")
        return None
    except Exception as e:
        print(f"auto mode: unexpected error for {fund_code}, fallback to legacy: {e}")
        return None


def get_db():
    if 'db' not in g:
        g.db = SessionLocal()
    return g.db

@app.teardown_appcontext
def teardown_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# 初始化数据库
init_db()
fund_api = FundAPI()
fund_list_cache = get_fund_list_cache()

def _json_dumps(data):
    return json.dumps(data, ensure_ascii=False) if data is not None else None

def _json_loads(data, default):
    if not data:
        return default
    try:
        return json.loads(data)
    except Exception:
        return default

def _normalize_stock_code(code):
    text = str(code or '').strip()
    text = re.sub(r'^(sh|sz|hk)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\.(SH|SZ|HK)$', '', text, flags=re.IGNORECASE)
    if re.match(r'^[A-Za-z]{1,6}([.-][A-Za-z]{1,3})?$', text):
        return text.upper()
    return text if re.match(r'^\d{5,6}$', text) else text

def _is_us_stock_code(code):
    text = str(code or '').strip().upper()
    if not text or re.match(r'^\d{5,6}$', text):
        return False
    return bool(re.match(r'^[A-Z]{1,6}([.-][A-Z]{1,3})?$', text))

def _safe_ratio(value):
    if value is None:
        return 0.0
    text = str(value).strip().replace('%', '')
    try:
        return float(text)
    except Exception:
        return 0.0

def _portfolio_holding_items(raw_holdings):
    if not isinstance(raw_holdings, list):
        return []

    if raw_holdings and isinstance(raw_holdings[0], dict):
        items = []
        for item in raw_holdings:
            code = _normalize_stock_code(item.get('code') or item.get('stock_code') or item.get('gpdm'))
            if not code:
                continue
            items.append({
                **item,
                'code': code,
                'name': item.get('name') or item.get('stock_name') or item.get('gpmc') or '',
                'ratio': _safe_ratio(item.get('ratio') or item.get('position') or item.get('hold_ratio')),
            })
        return items

    items = []
    for index in range(0, len(raw_holdings), 3):
        if index + 2 >= len(raw_holdings):
            break
        code = _normalize_stock_code(raw_holdings[index])
        if not code:
            continue
        items.append({
            'code': code,
            'name': raw_holdings[index + 1],
            'ratio': _safe_ratio(raw_holdings[index + 2]),
        })
    return items

def _fund_name_or_type_text(portfolio: dict):
    if not isinstance(portfolio, dict):
        return ''
    parts = [
        portfolio.get('fund_name'),
        portfolio.get('name'),
        portfolio.get('fund_type'),
        portfolio.get('type'),
        portfolio.get('index_name'),
        portfolio.get('tracking_index'),
    ]
    return ' '.join(str(part) for part in parts if part)

def _is_broad_index_fund_text(text):
    text = str(text or '').upper()
    broad_keywords = [
        '沪深300', '中证500', '中证800', '中证1000', '中证2000',
        '上证50', '上证180', '深证100', '创业板指', '创业板50',
        '科创50', '科创100', 'A500', 'MSCI', '央视50',
        '宽基', '综合指数', '综指', '指数增强',
        'CSI 300', 'CSI300', 'CSI 500', 'CSI500', 'SSE 50',
    ]
    return any(keyword.upper() in text for keyword in broad_keywords)

def _is_etf_feeder_text(text):
    text = str(text or '').upper()
    keywords = ['ETF联接', 'ETF连接', 'ETF链接', '联接基金', '联接A', '联接C', 'ETF FEEDER']
    return any(keyword.upper() in text for keyword in keywords)

def _topic_from_fund_text(text):
    text = str(text or '').upper()
    topic_rules = [
        ('半导体', ['半导体', '芯片', '集成电路', 'CHIP', 'SEMICONDUCTOR']),
        ('证券', ['证券', '券商', '证券公司', '证券保险']),
        ('银行', ['银行', 'BANK']),
        ('保险', ['保险']),
        ('医药', ['医药', '医疗', '生物医药', '创新药', '医疗器械', 'HEALTH', 'PHARMA']),
        ('消费', ['消费', '食品饮料', '主要消费', '可选消费', '酒', '白酒', '家电']),
        ('新能源', ['新能源', '新能源车', '新能源汽车', '光伏', '太阳能', '储能', '电池', '锂电']),
        ('军工', ['军工', '国防', '航天', '航空']),
        ('人工智能', ['人工智能', 'AI', '机器人', '智能']),
        ('计算机', ['计算机', '软件', '云计算', '大数据', '互联网']),
        ('通信', ['通信', '5G', '通讯']),
        ('电子', ['电子', '消费电子']),
        ('传媒', ['传媒', '游戏', '动漫']),
        ('房地产', ['房地产', '地产']),
        ('有色金属', ['有色', '有色金属', '稀土', '黄金', '贵金属']),
        ('煤炭', ['煤炭']),
        ('钢铁', ['钢铁']),
        ('化工', ['化工', '化学']),
        ('农业', ['农业', '农牧', '畜牧', '养殖']),
        ('红利', ['红利', '股息', '高息']),
    ]
    for topic, keywords in topic_rules:
        if any(keyword.upper() in text for keyword in keywords):
            return topic
    return None

def _is_broad_index_fund_text(text):
    text = str(text or '').upper()
    broad_keywords = [
        '沪深300', '中证500', '中证800', '中证1000', '中证2000',
        '上证50', '上证180', '深证100', '创业板指', '创业板50',
        '科创50', '科创100', 'A500', 'MSCI', '央企50',
        '宽基', '综合指数', '综指', '指数增强',
        'CSI 300', 'CSI300', 'CSI 500', 'CSI500', 'SSE 50',
    ]
    return any(keyword.upper() in text for keyword in broad_keywords)

def _is_etf_feeder_text(text):
    text = str(text or '').upper()
    keywords = ['ETF联接', 'ETF连接', 'ETF链接', '联接基金', '联接A', '联接C', 'ETF FEEDER']
    return any(keyword.upper() in text for keyword in keywords)

def _topic_from_fund_text(text):
    text = str(text or '').upper()
    topic_rules = [
        ('半导体', ['半导体', '芯片', '集成电路', 'CHIP', 'SEMICONDUCTOR']),
        ('证券', ['证券', '券商', '证券公司', '证券保险']),
        ('银行', ['银行', 'BANK']),
        ('保险', ['保险']),
        ('医药', ['医药', '医疗', '生物医药', '创新药', '医疗器械', 'HEALTH', 'PHARMA']),
        ('消费', ['消费', '食品饮料', '主要消费', '可选消费', '酒', '白酒', '家电']),
        ('新能源', ['新能源', '新能源汽车', '新能源车', '光伏', '太阳能', '储能', '电池', '锂电']),
        ('军工', ['军工', '国防', '航天', '航空']),
        ('人工智能', ['人工智能', 'AI', '机器人', '智能']),
        ('计算机', ['计算机', '软件', '云计算', '大数据', '互联网']),
        ('通信', ['通信', '5G', '通讯']),
        ('电子', ['电子', '消费电子']),
        ('传媒', ['传媒', '游戏', '动漫']),
        ('房地产', ['房地产', '地产']),
        ('有色金属', ['有色', '有色金属', '稀土', '黄金', '贵金属']),
        ('煤炭', ['煤炭']),
        ('钢铁', ['钢铁']),
        ('化工', ['化工', '化学']),
        ('农业', ['农业', '农牧', '畜牧', '养殖']),
        ('红利', ['红利', '股息', '高息']),
    ]
    for topic, keywords in topic_rules:
        if any(keyword.upper() in text for keyword in keywords):
            return topic
    return None

def _fund_text_industry_fallback(text):
    if _is_broad_index_fund_text(text):
        return {
            'name': '宽基指数',
            'ratio': 0.0,
            'count': 0,
            'basis': 'broad_index_name',
            'source': 'fund_name_rule',
        }
    topic = _topic_from_fund_text(text)
    if topic:
        return {
            'name': topic,
            'ratio': 0.0,
            'count': 0,
            'basis': 'fund_name_topic',
            'source': 'fund_name_rule',
        }
    if _is_etf_feeder_text(text):
        return {
            'name': '指数联接',
            'ratio': 0.0,
            'count': 0,
            'basis': 'etf_feeder_name',
            'source': 'fund_name_rule',
        }
    return None

def _upsert_stock_industry(db: Session, stock_data: dict):
    code = _normalize_stock_code(stock_data.get('code'))
    if not code:
        return None

    record = db.query(StockIndustry).filter(StockIndustry.stock_code == code).first()
    if not record:
        record = StockIndustry(stock_code=code)
        db.add(record)

    concepts = stock_data.get('concepts')
    if not isinstance(concepts, list):
        concepts = []

    record.stock_name = stock_data.get('name') or record.stock_name
    record.industry = stock_data.get('industry') or record.industry
    record.region = stock_data.get('region') or record.region
    record.concepts_json = _json_dumps(concepts)
    record.source = stock_data.get('source') or 'data_service'
    record.updated_time = datetime.now()
    return record

def _stock_industry_payload(record):
    return {
        'stock_code': record.stock_code,
        'stock_name': record.stock_name,
        'industry': record.industry,
        'region': record.region,
        'concepts': _json_loads(record.concepts_json, []),
        'source': record.source,
        'updated_time': record.updated_time.isoformat() if record.updated_time else None,
    }

def _fetch_stock_industry_batch(db: Session, codes, force_refresh=False, timeout=None):
    normalized_codes = []
    for code in codes or []:
        normalized = _normalize_stock_code(code)
        if normalized and re.match(r'^\d{5,6}$', normalized) and normalized not in normalized_codes:
            normalized_codes.append(normalized)

    if not normalized_codes:
        return {}, []

    industry_map = {}
    failed_codes = []
    chunk_size = 100
    client = DataServiceClient(timeout=timeout if timeout is not None else (8.0 if force_refresh else 4.0))
    for index in range(0, len(normalized_codes), chunk_size):
        chunk = normalized_codes[index:index + chunk_size]
        try:
            payload = client.get_stock_references(chunk)
            ds_data = payload.get('data', {}) if isinstance(payload, dict) else {}
            ds_items = ds_data.get('items', []) if isinstance(ds_data, dict) else []
            success_codes = set()
            for result in ds_items:
                if not isinstance(result, dict) or not result.get('success'):
                    continue
                stock_data = result.get('data') if isinstance(result.get('data'), dict) else {}
                record = _upsert_stock_industry(db, stock_data)
                if record:
                    industry_map[record.stock_code] = _stock_industry_payload(record)
                    success_codes.add(record.stock_code)
            failed_codes.extend(code for code in chunk if code not in success_codes)
        except DataServiceError as exc:
            print(f"stock industry dictionary: DataService unavailable for {len(chunk)} codes: {exc}")
            failed_codes.extend(chunk)
        except Exception as exc:
            print(f"stock industry dictionary: unexpected error for {len(chunk)} codes: {exc}")
            failed_codes.extend(chunk)

    return industry_map, failed_codes

def _resolve_stock_industries(db: Session, holdings, force_refresh=False, allow_network=False):
    items = _portfolio_holding_items(holdings)
    codes = []
    for item in items:
        code = _normalize_stock_code(item.get('code'))
        if code and code not in codes:
            codes.append(code)

    if not codes:
        return {}, []

    records = db.query(StockIndustry).filter(StockIndustry.stock_code.in_(codes)).all()
    industry_map = {record.stock_code: _stock_industry_payload(record) for record in records}

    for code in codes:
        if code in industry_map and industry_map[code].get('industry'):
            continue
        if not _is_us_stock_code(code):
            continue
        record = db.query(StockIndustry).filter(StockIndustry.stock_code == code).first()
        if not record:
            record = StockIndustry(stock_code=code)
            db.add(record)
        record.stock_name = record.stock_name or code
        record.industry = '美股'
        record.region = 'US'
        record.source = 'rule.us_ticker'
        record.updated_time = datetime.now()
        industry_map[code] = _stock_industry_payload(record)

    missing_codes = codes if force_refresh else [
        code for code in codes
        if code not in industry_map or not industry_map[code].get('industry')
    ]

    if allow_network and missing_codes:
        fetched_map, _ = _fetch_stock_industry_batch(
            db,
            missing_codes,
            force_refresh=force_refresh,
        )
        industry_map.update(fetched_map)

    unresolved = [code for code in codes if not industry_map.get(code, {}).get('industry')]
    return industry_map, unresolved

def _enhance_portfolio_industries(db: Session, portfolio: dict, force_refresh=False):
    if not isinstance(portfolio, dict):
        return portfolio

    target_keys = ['stock_codes_new', 'stock_codes']
    all_holdings = []
    for key in target_keys:
        all_holdings.extend(_portfolio_holding_items(portfolio.get(key)))

    industry_map, unresolved = _resolve_stock_industries(
        db,
        all_holdings,
        force_refresh=force_refresh,
        allow_network=force_refresh,
    )

    for key in target_keys:
        items = _portfolio_holding_items(portfolio.get(key))
        if not items:
            continue
        enhanced = []
        for item in items:
            info = industry_map.get(item.get('code'), {})
            enhanced.append({
                **item,
                'industry': info.get('industry'),
                'region': info.get('region'),
                'concepts': info.get('concepts') or [],
            })
        portfolio[key] = enhanced

    portfolio['industry_unresolved_codes'] = unresolved
    portfolio['industry_tag'] = _build_portfolio_industry_tag(portfolio)
    return portfolio

def _build_portfolio_industry_tag(portfolio: dict):
    if not isinstance(portfolio, dict):
        return None

    fund_text = _fund_name_or_type_text(portfolio)
    if _is_broad_index_fund_text(fund_text):
        return {
            'name': '宽基指数',
            'ratio': 0.0,
            'count': 0,
            'basis': 'broad_index_name',
            'source': 'fund_name_rule',
        }
    topic = _topic_from_fund_text(fund_text)
    if topic:
        return {
            'name': topic,
            'ratio': 0.0,
            'count': 0,
            'basis': 'fund_name_topic',
            'source': 'fund_name_rule',
        }
    if _is_etf_feeder_text(fund_text):
        return {
            'name': '????',
            'ratio': 0.0,
            'count': 0,
            'basis': 'etf_feeder_name',
            'source': 'fund_name_rule',
        }

    holdings = _portfolio_holding_items(portfolio.get('stock_codes_new')) or _portfolio_holding_items(portfolio.get('stock_codes'))
    buckets = {}
    total_ratio = 0.0
    for item in holdings:
        industry = item.get('industry') or item.get('industry_name') or item.get('industryName') or item.get('sector') or item.get('sector_name')
        if not industry:
            continue
        ratio = _safe_ratio(item.get('ratio'))
        total_ratio += ratio
        bucket = buckets.setdefault(industry, {
            'name': industry,
            'ratio': 0.0,
            'count': 0,
            'stocks': [],
        })
        bucket['ratio'] += ratio
        bucket['count'] += 1
        bucket['stocks'].append({
            'code': item.get('code'),
            'name': item.get('name'),
            'ratio': ratio,
        })

    if not buckets:
        return {
            'name': '混合型',
            'ratio': 0.0,
            'count': 0,
            'basis': 'mixed',
            'source': 'top_stock_holdings',
        }

    top = sorted(
        buckets.values(),
        key=lambda item: (item['ratio'], item['count']),
        reverse=True,
    )[0]

    valid_count = sum(item['count'] for item in buckets.values())
    top_share = (top['ratio'] / total_ratio * 100) if total_ratio > 0 else 0.0

    if top['count'] < 3 or top_share < 45:
        return {
            'name': '混合型',
            'ratio': round(top['ratio'], 2),
            'count': top['count'],
            'basis': 'mixed',
            'source': 'top_stock_holdings',
            'top_share': round(top_share, 2),
            'valid_count': valid_count,
        }

    return {
        'name': top['name'],
        'ratio': round(top['ratio'], 2),
        'count': top['count'],
        'basis': 'concentration' if total_ratio > 0 else 'count',
        'source': 'top_stock_holdings',
        'top_share': round(top_share, 2),
        'valid_count': valid_count,
    }

def _build_portfolio_industry_tag(portfolio: dict):
    if not isinstance(portfolio, dict):
        return None

    fund_text = _fund_name_or_type_text(portfolio)
    fallback_tag = _fund_text_industry_fallback(fund_text)
    holdings = _portfolio_holding_items(portfolio.get('stock_codes_new')) or _portfolio_holding_items(portfolio.get('stock_codes'))

    if not holdings:
        return fallback_tag or {
            'name': '混合型',
            'ratio': 0.0,
            'count': 0,
            'basis': 'missing_holdings',
            'source': 'top_stock_holdings',
        }

    buckets = {}
    total_ratio = 0.0
    for item in holdings:
        industry = (
            item.get('industry')
            or item.get('industry_name')
            or item.get('industryName')
            or item.get('sector')
            or item.get('sector_name')
        )
        if not industry and _is_us_stock_code(item.get('code')):
            industry = '美股'
        if not industry:
            continue

        ratio = _safe_ratio(item.get('ratio'))
        total_ratio += ratio
        bucket = buckets.setdefault(industry, {
            'name': industry,
            'ratio': 0.0,
            'count': 0,
            'stocks': [],
        })
        bucket['ratio'] += ratio
        bucket['count'] += 1
        bucket['stocks'].append({
            'code': item.get('code'),
            'name': item.get('name'),
            'ratio': ratio,
        })

    if not buckets:
        return fallback_tag or {
            'name': '混合型',
            'ratio': 0.0,
            'count': 0,
            'basis': 'mixed',
            'source': 'top_stock_holdings',
        }

    top = sorted(
        buckets.values(),
        key=lambda item: (item['ratio'], item['count']),
        reverse=True,
    )[0]

    valid_count = sum(item['count'] for item in buckets.values())
    top_share = (
        (top['ratio'] / total_ratio * 100)
        if total_ratio > 0
        else (top['count'] / valid_count * 100 if valid_count else 0.0)
    )
    is_us_dominant = top['name'] == '美股' and (top_share >= 45 or top['count'] >= 3)
    is_industry_concentrated = top['count'] >= 3 and top_share >= 45

    if is_us_dominant or is_industry_concentrated:
        return {
            'name': top['name'],
            'ratio': round(top['ratio'], 2),
            'count': top['count'],
            'basis': 'us_holdings' if top['name'] == '美股' else 'concentration',
            'source': 'top_stock_holdings',
            'top_share': round(top_share, 2),
            'valid_count': valid_count,
        }

    if fallback_tag:
        fallback = dict(fallback_tag)
        fallback.update({
            'ratio': round(top['ratio'], 2),
            'count': top['count'],
            'top_share': round(top_share, 2),
            'valid_count': valid_count,
            'holding_top_industry': top['name'],
        })
        return fallback

    return {
        'name': '混合型',
        'ratio': round(top['ratio'], 2),
        'count': top['count'],
        'basis': 'mixed',
        'source': 'top_stock_holdings',
        'top_share': round(top_share, 2),
        'valid_count': valid_count,
    }

def _build_industry_tag_from_cached_holdings(raw_holdings, industry_lookup, fund_name=None, fund_type=None):
    holdings = _portfolio_holding_items(raw_holdings)
    enhanced = []
    for item in holdings:
        info = industry_lookup.get(item.get('code'), {})
        enhanced.append({
            **item,
            'industry': info.get('industry'),
        })
    return _build_portfolio_industry_tag({
        'stock_codes_new': enhanced,
        'fund_name': fund_name,
        'fund_type': fund_type,
    })

def _upsert_fund_industry_tag(db: Session, fund_code: str, tag: dict, detail=None, unresolved_count=0):
    fund_code = _normalize_fund_code(fund_code)
    if not fund_code or not tag:
        return None

    record = db.query(FundIndustryTag).filter(FundIndustryTag.fund_code == fund_code).first()
    if not record:
        record = FundIndustryTag(fund_code=fund_code)
        db.add(record)

    record.industry_tag = tag.get('name') or '混合型'
    record.industry_count = int(tag.get('count') or 0)
    record.industry_ratio = _safe_ratio(tag.get('ratio'))
    record.basis = tag.get('basis') or 'mixed'
    record.source = tag.get('source') or 'top_stock_holdings'
    record.detail_json = _json_dumps(detail or {})
    record.unresolved_count = int(unresolved_count or 0)
    record.updated_time = datetime.now()
    return record

def _upsert_fund_industry_tag(db: Session, fund_code: str, tag: dict, detail=None, unresolved_count=0):
    fund_code = _normalize_fund_code(fund_code)
    if not fund_code or not tag:
        return None

    record = db.query(FundIndustryTag).filter(FundIndustryTag.fund_code == fund_code).first()
    if not record:
        record = FundIndustryTag(fund_code=fund_code)
        db.add(record)

    record.industry_tag = tag.get('name') or '混合型'
    record.industry_count = int(tag.get('count') or 0)
    record.industry_ratio = _safe_ratio(tag.get('ratio'))
    record.basis = tag.get('basis') or 'mixed'
    record.source = tag.get('source') or 'top_stock_holdings'
    record.detail_json = _json_dumps(detail or {})
    record.unresolved_count = int(unresolved_count or 0)
    record.updated_time = datetime.now()
    return record

def _save_portfolio_from_holdings_payload(db: Session, fund_code: str, payload: dict):
    data = payload.get('data', {}) if isinstance(payload, dict) else {}
    items = data.get('items', []) if isinstance(data, dict) else []
    if not isinstance(items, list) or not items:
        return None

    stock_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = _normalize_stock_code(item.get('stockCode') or item.get('code'))
        if not code:
            continue
        ratio = item.get('ratio')
        if isinstance(ratio, (int, float)) and 0 < ratio <= 1:
            ratio = round(ratio * 100, 4)
        stock_items.append({
            'code': code,
            'name': item.get('stockName') or item.get('name') or code,
            'market': item.get('market'),
            'ratio': ratio,
        })

    if not stock_items:
        return None

    fund_code = _normalize_fund_code(fund_code)
    record = db.query(FundPortfolio).filter(FundPortfolio.fund_code == fund_code).first()
    if not record:
        record = FundPortfolio(fund_code=fund_code)
        db.add(record)

    record.stock_codes_json = _json_dumps(stock_items)
    record.stock_codes_new_json = _json_dumps(stock_items)
    record.bond_codes_json = _json_dumps(data.get('bondCodes', []) if isinstance(data, dict) else [])
    record.bond_codes_new_json = _json_dumps(data.get('bondCodesNew', []) if isinstance(data, dict) else [])
    record.updated_time = datetime.now()
    return record

def _refresh_fund_industry_tag(db: Session, fund_code: str, fetch_holdings_if_missing=False, force_stock_refresh=False, allow_stock_network=False):
    fund_code = _normalize_fund_code(fund_code)
    if not fund_code:
        return None

    portfolio = db.query(FundPortfolio).filter(FundPortfolio.fund_code == fund_code).first()
    if not portfolio and fetch_holdings_if_missing:
        try:
            payload = get_data_service_client().get_fund_holdings(fund_code)
            portfolio = _save_portfolio_from_holdings_payload(db, fund_code, payload)
        except Exception as exc:
            print(f"fund industry tag: holdings fetch failed for {fund_code}: {exc}")

    if not portfolio:
        tag = {
            'name': '混合型',
            'ratio': 0.0,
            'count': 0,
            'basis': 'missing_holdings',
            'source': 'top_stock_holdings',
        }
        return _upsert_fund_industry_tag(db, fund_code, tag, detail={'reason': 'missing_holdings'}, unresolved_count=0)

    raw_holdings = _json_loads(portfolio.stock_codes_new_json, []) or _json_loads(portfolio.stock_codes_json, [])
    holding_items = _portfolio_holding_items(raw_holdings)
    industry_map, unresolved = _resolve_stock_industries(
        db,
        holding_items,
        force_refresh=force_stock_refresh,
        allow_network=allow_stock_network or force_stock_refresh,
    )

    enhanced = []
    for item in holding_items:
        info = industry_map.get(item.get('code'), {})
        enhanced.append({
            **item,
            'industry': info.get('industry'),
            'region': info.get('region'),
            'concepts': info.get('concepts') or [],
        })

    basic = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
    tag = _build_portfolio_industry_tag({
        'stock_codes_new': enhanced,
        'fund_name': basic.fund_name if basic else None,
        'fund_type': basic.fund_type if basic else None,
    })
    industry_counts = {}
    for item in enhanced:
        industry = item.get('industry')
        if not industry:
            continue
        bucket = industry_counts.setdefault(industry, {'count': 0, 'ratio': 0.0})
        bucket['count'] += 1
        bucket['ratio'] += _safe_ratio(item.get('ratio'))

    return _upsert_fund_industry_tag(
        db,
        fund_code,
        tag,
        detail={'industries': industry_counts},
        unresolved_count=len(unresolved),
    )

def _stats_numbers(values):
    nums = []
    for value in values:
        num = _to_float(value)
        if num is not None:
            nums.append(num)
    nums.sort()
    if not nums:
        return {
            'avg': None,
            'median': None,
            'positive_rate': None,
            'count': 0,
        }
    mid = len(nums) // 2
    median = nums[mid] if len(nums) % 2 else (nums[mid - 1] + nums[mid]) / 2
    return {
        'avg': round(sum(nums) / len(nums), 2),
        'median': round(median, 2),
        'positive_rate': round(sum(1 for num in nums if num > 0) / len(nums) * 100, 2),
        'count': len(nums),
    }

def rebuild_industry_performance_stats(db: Session):
    rows = db.query(FundIndustryTag, FundBasicInfo).join(
        FundBasicInfo, FundIndustryTag.fund_code == FundBasicInfo.fund_code
    ).all()

    grouped = {}
    for tag, basic in rows:
        industry = tag.industry_tag or '混合型'
        perf = _json_loads(basic.performance_json, {}) if basic else {}
        bucket = grouped.setdefault(industry, {
            'funds': [],
            'return_3m': [],
            'return_6m': [],
            'return_1y': [],
            'return_3y': [],
        })
        bucket['funds'].append({
            'fund_code': basic.fund_code,
            'fund_name': basic.fund_name,
            'fund_type': basic.fund_type,
        })
        bucket['return_3m'].append(perf.get('3_month_return'))
        bucket['return_6m'].append(perf.get('6_month_return'))
        bucket['return_1y'].append(perf.get('1_year_return'))
        bucket['return_3y'].append(perf.get('3_year_return'))

    existing = {
        row.industry_tag: row
        for row in db.query(FundIndustryPerformance).all()
    }

    touched = set()
    for industry, bucket in grouped.items():
        stat_3m = _stats_numbers(bucket['return_3m'])
        stat_6m = _stats_numbers(bucket['return_6m'])
        stat_1y = _stats_numbers(bucket['return_1y'])
        stat_3y = _stats_numbers(bucket['return_3y'])

        record = existing.get(industry)
        if not record:
            record = FundIndustryPerformance(industry_tag=industry)
            db.add(record)

        record.fund_count = len(bucket['funds'])
        record.return_3m_avg = stat_3m['avg']
        record.return_3m_median = stat_3m['median']
        record.return_6m_avg = stat_6m['avg']
        record.return_6m_median = stat_6m['median']
        record.return_1y_avg = stat_1y['avg']
        record.return_1y_median = stat_1y['median']
        record.return_3y_avg = stat_3y['avg']
        record.return_3y_median = stat_3y['median']
        record.positive_3m_rate = stat_3m['positive_rate']
        record.positive_6m_rate = stat_6m['positive_rate']
        record.positive_1y_rate = stat_1y['positive_rate']
        record.positive_3y_rate = stat_3y['positive_rate']
        record.detail_json = _json_dumps({
            'period_counts': {
                '3m': stat_3m['count'],
                '6m': stat_6m['count'],
                '1y': stat_1y['count'],
                '3y': stat_3y['count'],
            },
            'funds': bucket['funds'][:50],
        })
        record.updated_time = datetime.now()
        touched.add(industry)

    for industry, record in existing.items():
        if industry not in touched:
            db.delete(record)

    return len(touched)

def _industry_performance_payload(db: Session):
    rows = db.query(FundIndustryPerformance).order_by(
        desc(FundIndustryPerformance.return_3m_median)
    ).all()
    items = []
    for row in rows:
        detail = _json_loads(row.detail_json, {})
        items.append({
            'industry': row.industry_tag,
            'fund_count': row.fund_count,
            'return_3m_avg': row.return_3m_avg,
            'return_3m_median': row.return_3m_median,
            'return_6m_avg': row.return_6m_avg,
            'return_6m_median': row.return_6m_median,
            'return_1y_avg': row.return_1y_avg,
            'return_1y_median': row.return_1y_median,
            'return_3y_avg': row.return_3y_avg,
            'return_3y_median': row.return_3y_median,
            'positive_3m_rate': row.positive_3m_rate,
            'positive_6m_rate': row.positive_6m_rate,
            'positive_1y_rate': row.positive_1y_rate,
            'positive_3y_rate': row.positive_3y_rate,
            'period_counts': detail.get('period_counts', {}) if isinstance(detail, dict) else {},
            'updated_time': row.updated_time.isoformat() if row.updated_time else None,
        })

    return {
        'items': items,
        'summary': {
            'total': len(items),
            'fund_count': sum(item.get('fund_count') or 0 for item in items),
            'updated_time': max((item.get('updated_time') for item in items if item.get('updated_time')), default=None),
        },
        'top_3m': sorted(items, key=lambda item: item.get('return_3m_median') if item.get('return_3m_median') is not None else -9999, reverse=True)[:8],
        'top_1y': sorted(items, key=lambda item: item.get('return_1y_median') if item.get('return_1y_median') is not None else -9999, reverse=True)[:8],
        'weak_3m': sorted(items, key=lambda item: item.get('return_3m_median') if item.get('return_3m_median') is not None else 9999)[:8],
    }

def _screening_industry_context(db: Session, fund_codes):
    codes = [str(code) for code in fund_codes if code]
    if not codes:
        return {}

    persisted = db.query(FundIndustryTag).filter(FundIndustryTag.fund_code.in_(codes)).all()
    tag_map = {
        item.fund_code: {
            'name': item.industry_tag,
            'ratio': item.industry_ratio,
            'count': item.industry_count,
            'basis': item.basis,
            'source': item.source,
        }
        for item in persisted
    }
    missing_codes = [code for code in codes if code not in tag_map]
    if not missing_codes:
        return tag_map

    portfolios = db.query(FundPortfolio).filter(FundPortfolio.fund_code.in_(missing_codes)).all()
    portfolio_map = {item.fund_code: item for item in portfolios}

    stock_codes = set()
    for portfolio in portfolios:
        raw_holdings = _json_loads(portfolio.stock_codes_new_json, []) or _json_loads(portfolio.stock_codes_json, [])
        for item in _portfolio_holding_items(raw_holdings):
            code = item.get('code')
            if code:
                stock_codes.add(code)

    industry_lookup = {}
    if stock_codes:
        records = db.query(StockIndustry).filter(StockIndustry.stock_code.in_(stock_codes)).all()
        industry_lookup = {
            record.stock_code: {
                'industry': record.industry,
                'stock_name': record.stock_name,
            }
            for record in records
        }

    for fund_code, portfolio in portfolio_map.items():
        raw_holdings = _json_loads(portfolio.stock_codes_new_json, []) or _json_loads(portfolio.stock_codes_json, [])
        basic = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
        tag = _build_industry_tag_from_cached_holdings(
            raw_holdings,
            industry_lookup,
            fund_name=basic.fund_name if basic else None,
            fund_type=basic.fund_type if basic else None,
        )
        tag_map[fund_code] = tag
        _upsert_fund_industry_tag(db, fund_code, tag, detail={'source': 'screening_cache_backfill'})
    return tag_map

def _build_fund_industry_exposure(db: Session, fund_code: str, force_refresh=False):
    fund_code = _normalize_fund_code(fund_code)
    portfolio = db.query(FundPortfolio).filter(FundPortfolio.fund_code == fund_code).first()
    if not portfolio:
        return None

    holdings = _json_loads(portfolio.stock_codes_new_json, []) or _json_loads(portfolio.stock_codes_json, [])
    holding_items = _portfolio_holding_items(holdings)
    industry_map, unresolved = _resolve_stock_industries(
        db,
        holding_items,
        force_refresh=force_refresh,
        allow_network=force_refresh,
    )

    exposure = {}
    enriched_holdings = []
    for item in holding_items:
        code = item.get('code')
        info = industry_map.get(code, {})
        industry = info.get('industry') or '未识别'
        ratio = _safe_ratio(item.get('ratio'))
        enriched_item = {
            **item,
            'industry': info.get('industry'),
            'region': info.get('region'),
            'concepts': info.get('concepts') or [],
        }
        enriched_holdings.append(enriched_item)
        if not info.get('industry'):
            industry = '未识别'

        bucket = exposure.setdefault(industry, {
            'industry': industry,
            'ratio': 0.0,
            'count': 0,
            'stocks': [],
        })
        bucket['ratio'] += ratio
        bucket['count'] += 1
        bucket['stocks'].append({
            'code': code,
            'name': item.get('name'),
            'ratio': ratio,
        })

    exposure_items = sorted(exposure.values(), key=lambda item: (item['ratio'], item['count']), reverse=True)
    for item in exposure_items:
        item['ratio'] = round(item['ratio'], 2)

    basic = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
    result = {
        'fund_code': fund_code,
        'holdings': enriched_holdings,
        'industries': exposure_items,
        'industry_tag': _build_portfolio_industry_tag({
            'stock_codes_new': enriched_holdings,
            'fund_name': basic.fund_name if basic else fund_code,
            'fund_type': basic.fund_type if basic else None,
        }),
        'unresolved_codes': unresolved,
        'updated_time': datetime.now().isoformat(),
    }
    _upsert_fund_industry_tag(
        db,
        fund_code,
        result['industry_tag'],
        detail={'industries': exposure_items},
        unresolved_count=len(unresolved),
    )
    return result

def _normalize_fund_code(code):
    code = str(code or '').strip()
    return code.zfill(6) if re.match(r'^\d{1,6}$', code) else code

def _build_cached_response(db: Session, fund_code: str):
    basic = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
    trend = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
    estimate = db.query(FundEstimate).filter(FundEstimate.fund_code == fund_code).first()
    portfolio = db.query(FundPortfolio).filter(FundPortfolio.fund_code == fund_code).first()
    extra = db.query(FundExtraData).filter(FundExtraData.fund_code == fund_code).first()

    if not any([basic, trend, estimate, portfolio, extra]):
        return None

    data = {}

    if basic:
        data['basic_info'] = _json_loads(basic.basic_json, {})
        data['performance'] = _json_loads(basic.performance_json, {})

    if trend:
        data['net_worth_trend'] = _json_loads(trend.net_worth_trend_json, [])
        data['accumulated_net_worth'] = _json_loads(trend.accumulated_net_worth_json, [])
        data['position_trend'] = _json_loads(trend.position_trend_json, [])
        data['total_return_trend'] = _json_loads(trend.total_return_trend_json, [])
        data['ranking_trend'] = _json_loads(trend.ranking_trend_json, [])
        data['ranking_percentage'] = _json_loads(trend.ranking_percentage_json, [])
        data['scale_fluctuation'] = _json_loads(trend.scale_fluctuation_json, {})

    if estimate:
        data['realtime_estimate'] = {
            'name': estimate.name,
            'fund_code': fund_code,
            'net_worth': estimate.net_worth,
            'net_worth_date': estimate.net_worth_date,
            'estimate_value': estimate.estimate_value,
            'estimate_change': estimate.estimate_change,
            'estimate_time': estimate.estimate_time
        }

    if portfolio:
        data['portfolio'] = _enhance_portfolio_industries(db, {
            'stock_codes': _json_loads(portfolio.stock_codes_json, []),
            'bond_codes': _json_loads(portfolio.bond_codes_json, []),
            'stock_codes_new': _json_loads(portfolio.stock_codes_new_json, []),
            'bond_codes_new': _json_loads(portfolio.bond_codes_new_json, [])
        })
        data['fund_industry_tag'] = data['portfolio'].get('industry_tag')
        _upsert_fund_industry_tag(
            db,
            fund_code,
            data['fund_industry_tag'],
            detail={'source': 'cached_response'},
            unresolved_count=len(data['portfolio'].get('industry_unresolved_codes') or []),
        )

    if extra:
        data['holder_structure'] = _json_loads(extra.holder_structure_json, {})
        data['asset_allocation'] = _json_loads(extra.asset_allocation_json, {})
        data['performance_evaluation'] = _json_loads(extra.performance_evaluation_json, {})
        data['fund_managers'] = _json_loads(extra.fund_managers_json, [])
        data['subscription_redemption'] = _json_loads(extra.subscription_redemption_json, {})
        data['same_type_funds'] = _json_loads(extra.same_type_funds_json, [])

    return data

@app.route('/api/eastmoney/<path:subpath>', methods=['GET'])
def proxy_eastmoney(subpath):
    """
    代理东方财富行情 API 请求

    用于生产环境：前端通过本后端转发请求到东方财富，
    避免跨域和 JSONP 依赖问题。
    开发环境中由 Vite 代理处理，此端点作为兜底。
    """
    import requests as req
    import urllib3
    urllib3.disable_warnings()

    target_url = f"https://push2.eastmoney.com/{subpath}"
    query_string = request.query_string.decode('utf-8')
    if query_string:
        target_url = f"{target_url}?{query_string}"

    base_headers = {
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    errors = []

    # 方案1：使用系统代理（与浏览器行为一致）
    try:
        s1 = req.Session()
        s1.trust_env = True
        resp = s1.get(target_url, headers=base_headers, timeout=10, verify=False)
        if resp.status_code == 200:
            return jsonify(resp.json())
    except Exception as exc:
        errors.append(f"env-proxy: {str(exc)[:80]}")

    # 方案2：不使用系统代理（直连）
    try:
        s2 = req.Session()
        s2.trust_env = False
        resp = s2.get(target_url, headers=base_headers, timeout=10, verify=False)
        if resp.status_code == 200:
            return jsonify(resp.json())
    except Exception as exc:
        errors.append(f"direct: {str(exc)[:80]}")

    # 方案3：curl_cffi 模拟 Chrome（绕过 TLS 指纹检测）
    try:
        from curl_cffi import requests as curl_requests
        last_imp_error = None
        for impersonate_target in ("chrome124", "chrome120", "chrome110", "chrome101", "edge101"):
            try:
                resp = curl_requests.get(
                    target_url,
                    headers=base_headers,
                    timeout=10,
                    verify=False,
                    impersonate=impersonate_target
                )
                if resp.status_code == 200:
                    return jsonify(resp.json())
            except Exception as imp_exc:
                last_imp_error = str(imp_exc)[:60]
                continue
        errors.append(f"curl_cffi(all): {last_imp_error or 'unknown'}")
    except Exception as exc:
        errors.append(f"curl_cffi: {str(exc)[:80]}")

    # 方案4：普通 requests（默认配置，verify=True）
    try:
        resp = req.get(target_url, headers=base_headers, timeout=10)
        if resp.status_code == 200:
            return jsonify(resp.json())
    except Exception as exc:
        errors.append(f"default: {str(exc)[:80]}")

    import logging
    logging.getLogger(__name__).warning(f"东方财富代理全部失败: {'; '.join(errors)}")
    return jsonify({"error": "东方财富代理请求失败", "details": errors}), 502


@app.route('/')
def hello():
    """测试接口是否可用"""
    return jsonify({"message": "Fund Analysis API is running!"})

@app.route('/api/fund/search', methods=['GET'])
def search_funds():
    """Search funds by keyword – DataService first, local cache fallback."""
    keyword = request.args.get('q', '')
    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400

    # 1) Try DataService first
    try:
        ds_payload = get_data_service_client().search_funds(keyword)
        ds_data = ds_payload.get('data', {}) if isinstance(ds_payload, dict) else {}
        ds_items = ds_data.get('items', []) if isinstance(ds_data, dict) else []

        if ds_items:
            # Map DataService DTO back to Frontend-expected format
            funds = []
            for item in ds_items:
                if not isinstance(item, dict):
                    continue
                funds.append({
                    'CODE': item.get('code', ''),
                    'NAME': item.get('name', ''),
                    'TYPE': item.get('type', ''),
                    'PINYIN': item.get('pinyin', ''),
                })
            print(f"fund search: DataService success, {len(funds)} results for '{keyword}'")
            return jsonify({"data": funds})
    except DataServiceError as e:
        print(f"fund search: DataService unavailable, fallback to local cache: {e}")

    # 2) Fallback to local cache
    funds = fund_list_cache.search(keyword, limit=20)
    return jsonify({"data": funds})

@app.route('/api/fund/search/status', methods=['GET'])
def get_search_status():
    """获取搜索数据库状态"""
    status = fund_list_cache.get_status()
    return jsonify(status)

@app.route('/api/fund/search/update', methods=['POST'])
def update_search_database():
    """更新本地基金搜索数据库"""
    result = fund_list_cache.update_from_api()
    if result['success']:
        return jsonify(result)
    else:
        return jsonify(result), 500

@app.route('/api/fund/<fund_code>', methods=['GET'])
def get_fund_detail(fund_code):
    """
    获取基金详细信息
    
    数据一致性策略：
    - 每次访问都从API获取最新数据
    - 同时更新所有相关表（FundBasicInfo, FundTrend, FundRiskMetrics等）
    - 确保详情、对比、筛选三个模块的数据源统一
    """
    fund_code = _normalize_fund_code(fund_code)
    if not fund_code:
        return jsonify({"error": "Fund code is required"}), 400

    # --- Gray-release source parameter ---
    # Priority: URL param > FUND_DEFAULT_SOURCE env > 'legacy'
    source = request.args.get('source', '').strip().lower()
    if not source:
        source = os.environ.get('FUND_DEFAULT_SOURCE', 'legacy').strip().lower()

    if source not in ('legacy', 'data_service', 'auto'):
        return jsonify({
            "success": False,
            "error": "Invalid source. Use legacy, data_service, or auto."
        }), 400

    # source=data_service: DataService only, no fallback
    if source == 'data_service':
        result = _get_fund_detail_from_data_service(fund_code)
        return jsonify(result)

    # source=auto: DataService first with quality gate, fallback to legacy
    auto_fallback = False
    if source == 'auto':
        ds_try = _try_data_service_fund_detail(fund_code)
        if ds_try is not None:
            ds_result, quality_passed, quality_issues = ds_try
            if quality_passed:
                ds_result['_data_source'] = {
                    "mode": "auto",
                    "used": "data_service",
                    "fallback": False,
                    "quality_passed": True,
                    "quality_issues": []
                }
                return jsonify(ds_result)
            else:
                # Quality gate failed – fallback to legacy
                print(f"auto mode: quality gate failed for {fund_code}: {quality_issues}")
                auto_fallback = True
        else:
            # DataService request/mapping failed
            auto_fallback = True

    db = get_db() # 获取数据库会话

    # 使用新的 get_fund_data 方法获取清洗后的完整数据
    fund_data = fund_api.get_fund_data(fund_code)
    
    if fund_data:
        basic_info = fund_data.get('basic_info', {})
        performance = fund_data.get('performance', {})
        trend = {
            'net_worth_trend': fund_data.get('net_worth_trend', []),
            'accumulated_net_worth': fund_data.get('accumulated_net_worth', []),
            'position_trend': fund_data.get('position_trend', []),
            'total_return_trend': fund_data.get('total_return_trend', []),
            'ranking_trend': fund_data.get('ranking_trend', []),
            'ranking_percentage': fund_data.get('ranking_percentage', []),
            'scale_fluctuation': fund_data.get('scale_fluctuation', {})
        }
        estimate = fund_data.get('realtime_estimate', {})
        portfolio = fund_data.get('portfolio', {})
        portfolio = _enhance_portfolio_industries(db, portfolio)
        fund_data['portfolio'] = portfolio
        fund_data['fund_industry_tag'] = portfolio.get('industry_tag')
        _upsert_fund_industry_tag(
            db,
            fund_code,
            fund_data['fund_industry_tag'],
            detail={'source': 'fund_detail'},
            unresolved_count=len(portfolio.get('industry_unresolved_codes') or []),
        )
        extra = {
            'holder_structure': fund_data.get('holder_structure', {}),
            'asset_allocation': fund_data.get('asset_allocation', {}),
            'performance_evaluation': fund_data.get('performance_evaluation', {}),
            'fund_managers': fund_data.get('fund_managers', []),
            'subscription_redemption': fund_data.get('subscription_redemption', {}),
            'same_type_funds': fund_data.get('same_type_funds', [])
        }

        basic_record = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
        if basic_record:
            basic_record.fund_name = basic_info.get('fund_name')
            basic_record.fund_type = basic_info.get('fund_type')
            basic_record.original_rate = basic_info.get('original_rate')
            basic_record.current_rate = basic_info.get('current_rate')
            basic_record.min_subscription_amount = basic_info.get('min_subscription_amount')
            basic_record.is_hb = basic_info.get('is_hb')
            basic_record.basic_json = _json_dumps(basic_info)
            basic_record.performance_json = _json_dumps(performance)
            # 设置可排序的收益率字段
            try:
                basic_record.return_1y = float(performance.get('1_year_return')) if performance.get('1_year_return') else None
            except (ValueError, TypeError):
                basic_record.return_1y = None
        else:
            # 解析收益率用于排序
            return_1y_val = None
            try:
                return_1y_val = float(performance.get('1_year_return')) if performance.get('1_year_return') else None
            except (ValueError, TypeError):
                pass
            basic_record = FundBasicInfo(
                fund_code=fund_code,
                fund_name=basic_info.get('fund_name') or fund_code,
                fund_type=basic_info.get('fund_type'),
                original_rate=basic_info.get('original_rate'),
                current_rate=basic_info.get('current_rate'),
                min_subscription_amount=basic_info.get('min_subscription_amount'),
                is_hb=basic_info.get('is_hb'),
                return_1y=return_1y_val,
                basic_json=_json_dumps(basic_info),
                performance_json=_json_dumps(performance)
            )
            db.add(basic_record)

        trend_record = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
        # Only overwrite cached net_worth_trend if fresh data is non-empty
        # Prevents API glitches from wiping valid cached data
        fresh_nwt = trend['net_worth_trend']
        fresh_acw = trend['accumulated_net_worth']
        fresh_pos = trend['position_trend']
        fresh_total_ret = trend['total_return_trend']
        fresh_rank = trend['ranking_trend']
        fresh_rank_pct = trend['ranking_percentage']
        fresh_scale = trend['scale_fluctuation']

        if trend_record:
            if fresh_nwt:
                trend_record.net_worth_trend_json = _json_dumps(fresh_nwt)
            if fresh_acw:
                trend_record.accumulated_net_worth_json = _json_dumps(fresh_acw)
            if fresh_pos:
                trend_record.position_trend_json = _json_dumps(fresh_pos)
            if fresh_total_ret:
                trend_record.total_return_trend_json = _json_dumps(fresh_total_ret)
            if fresh_rank:
                trend_record.ranking_trend_json = _json_dumps(fresh_rank)
            if fresh_rank_pct:
                trend_record.ranking_percentage_json = _json_dumps(fresh_rank_pct)
            if fresh_scale:
                trend_record.scale_fluctuation_json = _json_dumps(fresh_scale)
        else:
            trend_record = FundTrend(
                fund_code=fund_code,
                net_worth_trend_json=_json_dumps(fresh_nwt),
                accumulated_net_worth_json=_json_dumps(fresh_acw),
                position_trend_json=_json_dumps(fresh_pos),
                total_return_trend_json=_json_dumps(fresh_total_ret),
                ranking_trend_json=_json_dumps(fresh_rank),
                ranking_percentage_json=_json_dumps(fresh_rank_pct),
                scale_fluctuation_json=_json_dumps(fresh_scale)
            )
            db.add(trend_record)

        estimate_record = db.query(FundEstimate).filter(FundEstimate.fund_code == fund_code).first()
        if estimate_record:
            estimate_record.name = estimate.get('name')
            estimate_record.net_worth = estimate.get('net_worth')
            estimate_record.net_worth_date = estimate.get('net_worth_date')
            estimate_record.estimate_value = estimate.get('estimate_value')
            estimate_record.estimate_change = estimate.get('estimate_change')
            estimate_record.estimate_time = estimate.get('estimate_time')
        else:
            estimate_record = FundEstimate(
                fund_code=fund_code,
                name=estimate.get('name'),
                net_worth=estimate.get('net_worth'),
                net_worth_date=estimate.get('net_worth_date'),
                estimate_value=estimate.get('estimate_value'),
                estimate_change=estimate.get('estimate_change'),
                estimate_time=estimate.get('estimate_time')
            )
            db.add(estimate_record)

        portfolio_record = db.query(FundPortfolio).filter(FundPortfolio.fund_code == fund_code).first()
        if portfolio_record:
            portfolio_record.stock_codes_json = _json_dumps(portfolio.get('stock_codes', []))
            portfolio_record.bond_codes_json = _json_dumps(portfolio.get('bond_codes', []))
            portfolio_record.stock_codes_new_json = _json_dumps(portfolio.get('stock_codes_new', []))
            portfolio_record.bond_codes_new_json = _json_dumps(portfolio.get('bond_codes_new', []))
        else:
            portfolio_record = FundPortfolio(
                fund_code=fund_code,
                stock_codes_json=_json_dumps(portfolio.get('stock_codes', [])),
                bond_codes_json=_json_dumps(portfolio.get('bond_codes', [])),
                stock_codes_new_json=_json_dumps(portfolio.get('stock_codes_new', [])),
                bond_codes_new_json=_json_dumps(portfolio.get('bond_codes_new', []))
            )
            db.add(portfolio_record)

        extra_record = db.query(FundExtraData).filter(FundExtraData.fund_code == fund_code).first()
        if extra_record:
            extra_record.holder_structure_json = _json_dumps(extra['holder_structure'])
            extra_record.asset_allocation_json = _json_dumps(extra['asset_allocation'])
            extra_record.performance_evaluation_json = _json_dumps(extra['performance_evaluation'])
            extra_record.fund_managers_json = _json_dumps(extra['fund_managers'])
            extra_record.subscription_redemption_json = _json_dumps(extra['subscription_redemption'])
            extra_record.same_type_funds_json = _json_dumps(extra['same_type_funds'])
        else:
            extra_record = FundExtraData(
                fund_code=fund_code,
                holder_structure_json=_json_dumps(extra['holder_structure']),
                asset_allocation_json=_json_dumps(extra['asset_allocation']),
                performance_evaluation_json=_json_dumps(extra['performance_evaluation']),
                fund_managers_json=_json_dumps(extra['fund_managers']),
                subscription_redemption_json=_json_dumps(extra['subscription_redemption']),
                same_type_funds_json=_json_dumps(extra['same_type_funds'])
            )
            db.add(extra_record)

        # 【数据一致性】同时更新风险指标，确保详情/对比/筛选数据统一
        net_worth_trend = fund_data.get('net_worth_trend', [])
        if net_worth_trend and len(net_worth_trend) >= 30:
            risk_metrics = calculate_risk_metrics(net_worth_trend)
            if risk_metrics:
                _save_risk_metrics(db, fund_code, risk_metrics)
                # 将风险指标也附加到返回数据中
                fund_data['risk_metrics'] = risk_metrics

        try:
            db.commit()
        except Exception as e:
            print(f"Error saving to database: {e}")
            db.rollback()

        # Add debug meta for auto-fallback mode
        if auto_fallback:
            fund_data["_data_source"] = {
                "mode": "auto",
                "used": "legacy",
                "fallback": True,
                "quality_passed": False,
                "quality_issues": ["quality gate not passed — using legacy fallback"]
            }

        return jsonify(fund_data)
    
    # 如果API获取失败，尝试从数据库获取缓存数据作为兜底
    cached_data = _build_cached_response(db, fund_code)
    if cached_data:
        try:
            db.commit()
        except Exception as exc:
            print(f"cached fund industry commit failed: {exc}")
            db.rollback()
        return jsonify(cached_data)

    return jsonify({"error": "Fund not found"}), 404

@app.route('/api/fund/<fund_code>/industry-exposure', methods=['GET'])
def get_fund_industry_exposure(fund_code):
    """Return industry exposure inferred from a fund's top stock holdings."""
    db = get_db()
    force_refresh = request.args.get('refresh', '').lower() in ('1', 'true', 'yes')
    result = _build_fund_industry_exposure(db, fund_code, force_refresh=force_refresh)
    if result is None:
        return jsonify({
            "success": False,
            "error": "No portfolio data found for this fund. Open the fund detail once to fetch holdings first.",
        }), 404

    try:
        db.commit()
    except Exception as exc:
        print(f"industry exposure commit failed: {exc}")
        db.rollback()

    return jsonify({
        "success": True,
        "data": result,
    })

@app.route('/api/stock/<code>/quote', methods=['GET'])
def get_stock_quote(code):
    """
    获取个股实时行情数据

    调用腾讯财经接口获取实时行情，包括：
    - 基础：名称、代码、交易所
    - 行情：当前价、涨跌额、涨跌幅、今开、昨收、最高、最低
    - 交易：成交量、成交额、换手率、振幅
    - 估值：市盈率、总市值

    Returns:
        - 成功: {success: true, data: {...}}
        - 失败: {success: false, error: "..."}
    """
    from services.market_data import get_market_data_service as get_mds
    try:
        mds = get_mds()
        quote = mds.get_realtime_quote(code, use_cache=True)
        if not quote:
            return jsonify({"success": False, "error": f"未找到股票 {code} 的行情数据"}), 404
        return jsonify({"success": True, "data": quote})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/stock/<code>/kline', methods=['GET'])
def get_stock_kline(code):
    """
    获取个股历史 K 线数据

    数据来源：东方财富 A 股 K 线接口（前复权）

    Query params:
        - period: daily / weekly / monthly（默认 daily）
        - adjust: qfq（前复权）/ hfq（后复权）/ none（默认 qfq）
        - startDate: 起始日期 YYYYMMDD（可选）
        - endDate: 结束日期 YYYYMMDD（可选）

    Returns:
        - 成功: {success: true, data: [{date, open, close, high, low, volume, amount, ...}], total_count: N}
        - 失败: {success: false, error: "..."}
    """
    from services.market_data import get_market_data_service as get_mds
    period = request.args.get('period', 'daily')
    adjust = request.args.get('adjust', 'qfq')
    start_date = request.args.get('startDate', '')
    end_date = request.args.get('endDate', '')
    try:
        # 规范化股票代码：兼容 sh600519 / 600519.SH / 600519 等格式
        normalized_code = re.sub(r'^(sh|sz|bj|SH|SZ|BJ)|\.(SH|SZ|BJ)$', '', code.strip())
        mds = get_mds()
        result = mds.get_a_stock_kline(
            normalized_code,
            klt=period,
            fqt=adjust,
            start_date=start_date,
            end_date=end_date,
        )
        if not result.get('success'):
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/market/daily', methods=['GET'])
def get_daily_market():
    """
    获取每日市场行情摘要（AI 驱动）
    
    优化后的接口特性：
    1. 使用硅基流动 API 进行 AI 分析
    2. 整合市场数据后生成摘要
    3. 支持 ?refresh=true 强制刷新
    
    Returns:
        - 成功: {market_sentiment, summary, key_points, ...}
        - 错误: {error: "..."}
    """
    try:
        ai_service = get_ai_service()
        if not ai_service.is_available():
            return jsonify({"error": "AI service not configured. Please set LLM_API_KEY in .env"}), 503
        
        # 从 fund_master_service 获取市场数据
        from fund_master_service import FundMasterService
        from services.market_data import get_market_data_service as get_mds

        service = FundMasterService()
        mds = get_mds()

        market_data = {
            'indices': service.get_market_overview(),
            'sectors': mds.get_industry_boards(page_size=500),
            'news': service.get_flash_news()[:10]
        }
        
        result = ai_service.generate_market_summary(market_data)
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/fund/<fund_code>/analyze', methods=['GET'])
def analyze_fund(fund_code):
    """
    使用 AI 分析基金（基于硅基流动 API）
    
    分析内容包括：
    - 基金业绩评价
    - 基金经理能力
    - 持仓结构分析
    - 后市展望
    - 亮点与风险提示
    """
    fund_code = _normalize_fund_code(fund_code)
    if not fund_code:
        return jsonify({"error": "Fund code is required"}), 400
    
    # 1. 获取基金数据
    fund_data = fund_api.get_fund_data(fund_code)
    if not fund_data:
        return jsonify({"error": "Fund data not found"}), 404
        
    # 2. 调用 AI 服务进行分析
    try:
        ai_service = get_ai_service()
        if not ai_service.is_available():
            return jsonify({
                "error": "AI service not configured. Please set LLM_API_KEY in .env file."
            }), 503
            
        result = ai_service.analyze_fund(fund_data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/fund/<fund_code>/basic', methods=['GET'])
def get_fund_basic(fund_code):
    """获取基金基础信息 实时调用API"""
    fund_code = _normalize_fund_code(fund_code)
    if not fund_code:
        return jsonify({"error": "Fund code is required"}), 400
    fund_data = fund_api.get_fund_data(fund_code)
    if fund_data and fund_data.get('basic_info'):
        result = {
            **fund_data.get('basic_info', {}),
            **fund_data.get('performance', {})
        }
        return jsonify(result)

    db = get_db()
    basic = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
    if basic:
        basic_info = _json_loads(basic.basic_json, {})
        performance = _json_loads(basic.performance_json, {})
        return jsonify({**basic_info, **performance})

    return jsonify({"error": "Fund basic info not found"}), 404

@app.route('/api/fund/<fund_code>/trend', methods=['GET'])
def get_fund_trend(fund_code):
    """获取基金走势数据 实时调用API"""
    fund_code = _normalize_fund_code(fund_code)
    if not fund_code:
        return jsonify({"error": "Fund code is required"}), 400
    
    fund_data = fund_api.get_fund_data(fund_code)
    if fund_data and 'net_worth_trend' in fund_data:
        return jsonify({
            "net_worth_trend": fund_data['net_worth_trend'],
            "accumulated_net_worth": fund_data.get('accumulated_net_worth', [])
        })

    db = get_db()
    trend = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
    if trend:
        return jsonify({
            "net_worth_trend": _json_loads(trend.net_worth_trend_json, []),
            "accumulated_net_worth": _json_loads(trend.accumulated_net_worth_json, [])
        })

    return jsonify({"error": "Fund trend data not found"}), 404


# ==================== 自选基金 API ====================

@app.route('/api/watchlist', methods=['GET'])
def get_watchlist():
    """获取自选基金列表（按分组和排序顺序）"""
    db = get_db()
    
    # 获取所有分组
    groups = db.query(FundWatchlistGroup).order_by(FundWatchlistGroup.sort_order).all()
    
    # 获取所有基金
    watchlist = db.query(FundWatchlist).order_by(FundWatchlist.sort_order).all()
    
    # 构建分组数据
    groups_data = []
    for group in groups:
        groups_data.append({
            'id': group.id,
            'name': group.name,
            'sort_order': group.sort_order
        })
    
    # 构建基金数据
    funds_data = []
    for item in watchlist:
        estimate = db.query(FundEstimate).filter(FundEstimate.fund_code == item.fund_code).first()

        net_worth = estimate.net_worth if estimate else None
        net_worth_date = estimate.net_worth_date if estimate else None
        display_change = estimate.estimate_change if estimate else None

        # 兜底：FundTrend 存储 pingzhongdata 的净值走势 JSON，
        # 解析取最新净值，如果比 FundEstimate (fundgz) 更新就覆盖
        try:
            trend = db.query(FundTrend).filter(FundTrend.fund_code == item.fund_code).first()
            if trend and trend.net_worth_trend_json:
                trend_data = json.loads(trend.net_worth_trend_json)
                if isinstance(trend_data, list) and trend_data:
                    last = trend_data[-1]
                    if isinstance(last, dict):
                        t_nw = last.get('net_worth')
                        t_date = last.get('date')
                        if t_nw is not None and t_date is not None:
                            trend_change = _trend_daily_return(trend_data)
                            if not net_worth_date or str(t_date) >= str(net_worth_date):
                                net_worth = str(t_nw)
                                net_worth_date = str(t_date)
                                if trend_change is not None:
                                    display_change = _value_to_string(trend_change)
        except Exception:
            pass

        fund_data = {
            'fund_code': item.fund_code,
            'fund_name': item.fund_name,
            'fund_type': item.fund_type,
            'group_id': item.group_id,
            'sort_order': item.sort_order,
            'created_time': item.created_time.isoformat() if item.created_time else None,
            'net_worth': net_worth,
            'net_worth_date': net_worth_date,
            'estimate_value': estimate.estimate_value if estimate else None,
            'estimate_change': display_change,
            'estimate_time': estimate.estimate_time if estimate else None
        }
        funds_data.append(fund_data)
    
    return jsonify({
        'groups': groups_data,
        'data': funds_data
    })


@app.route('/api/watchlist/<fund_code>', methods=['GET'])
def check_watchlist(fund_code):
    fund_code = _normalize_fund_code(fund_code)
    """检查基金是否在自选列表中"""
    db = get_db()
    exists = db.query(FundWatchlist).filter(FundWatchlist.fund_code == fund_code).first() is not None
    return jsonify({'in_watchlist': exists})


@app.route('/api/watchlist', methods=['POST'])
def add_to_watchlist():
    """添加基金到自选列表"""
    data = request.get_json()
    fund_code = _normalize_fund_code(data.get('fund_code'))
    fund_name = data.get('fund_name', '')
    fund_type = data.get('fund_type', '')
    group_id = data.get('group_id')  # 可选的分组ID
    
    if not fund_code:
        return jsonify({'error': 'Fund code is required'}), 400
    
    db = get_db()
    
    # 检查是否已存在
    existing = db.query(FundWatchlist).filter(FundWatchlist.fund_code == fund_code).first()
    if existing:
        return jsonify({'error': 'Fund already in watchlist', 'fund_code': fund_code}), 409
    
    # 获取当前最大排序值（在同一分组内）
    query = db.query(FundWatchlist)
    if group_id:
        query = query.filter(FundWatchlist.group_id == group_id)
    max_order = query.order_by(FundWatchlist.sort_order.desc()).first()
    new_order = (max_order.sort_order + 1) if max_order else 0
    
    # 创建新记录
    new_item = FundWatchlist(
        fund_code=fund_code,
        fund_name=fund_name,
        fund_type=fund_type,
        group_id=group_id,
        sort_order=new_order
    )
    
    try:
        db.add(new_item)
        db.commit()
        return jsonify({
            'message': 'Fund added to watchlist',
            'fund_code': fund_code,
            'sort_order': new_order
        }), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/<fund_code>', methods=['DELETE'])
def remove_from_watchlist(fund_code):
    fund_code = _normalize_fund_code(fund_code)
    """从自选列表移除基金"""
    db = get_db()
    
    item = db.query(FundWatchlist).filter(FundWatchlist.fund_code == fund_code).first()
    if not item:
        return jsonify({'error': 'Fund not in watchlist'}), 404
    
    try:
        db.delete(item)
        db.commit()
        return jsonify({'message': 'Fund removed from watchlist', 'fund_code': fund_code})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/batch-delete', methods=['POST'])
def batch_delete_from_watchlist():
    """批量删除自选基金"""
    data = request.get_json()
    fund_codes = [_normalize_fund_code(code) for code in data.get('fund_codes', [])]
    
    if not fund_codes:
        return jsonify({'error': 'Fund codes are required'}), 400
    
    db = get_db()
    
    try:
        deleted_count = db.query(FundWatchlist).filter(
            FundWatchlist.fund_code.in_(fund_codes)
        ).delete(synchronize_session=False)
        db.commit()
        return jsonify({
            'message': f'Deleted {deleted_count} funds from watchlist',
            'deleted_count': deleted_count
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/reorder', methods=['PUT'])
def reorder_watchlist():
    """
    更新自选基金排序
    请求体格式: { "order": ["000001", "000002", "000003"], "group_id": 1 }
    数组顺序即为排序顺序，索引值作为 sort_order
    group_id 可选，用于同时更新基金的分组
    """
    data = request.get_json()
    order = data.get('order', [])
    group_id = data.get('group_id')  # 可选，移动到某个分组
    
    if not order:
        return jsonify({'error': 'Order array is required'}), 400
    
    db = get_db()
    
    try:
        for index, fund_code in enumerate(order):
            fund_code = _normalize_fund_code(fund_code)
            update_data = {'sort_order': index}
            if group_id is not None:
                update_data['group_id'] = group_id if group_id > 0 else None
            db.query(FundWatchlist).filter(
                FundWatchlist.fund_code == fund_code
            ).update(update_data)
        db.commit()
        return jsonify({'message': 'Watchlist reordered successfully'})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


# ==================== 分组管理 API ====================

@app.route('/api/watchlist/groups', methods=['GET'])
def get_groups():
    """获取所有分组"""
    db = get_db()
    groups = db.query(FundWatchlistGroup).order_by(FundWatchlistGroup.sort_order).all()
    
    result = [{
        'id': g.id,
        'name': g.name,
        'sort_order': g.sort_order
    } for g in groups]
    
    return jsonify({'data': result})


@app.route('/api/watchlist/groups', methods=['POST'])
def create_group():
    """创建新分组"""
    data = request.get_json()
    name = data.get('name', '').strip()
    
    if not name:
        return jsonify({'error': 'Group name is required'}), 400
    
    db = get_db()
    
    # 获取最大排序值
    max_order = db.query(FundWatchlistGroup).order_by(FundWatchlistGroup.sort_order.desc()).first()
    new_order = (max_order.sort_order + 1) if max_order else 0
    
    new_group = FundWatchlistGroup(name=name, sort_order=new_order)
    
    try:
        db.add(new_group)
        db.commit()
        return jsonify({
            'message': 'Group created',
            'group': {
                'id': new_group.id,
                'name': new_group.name,
                'sort_order': new_group.sort_order
            }
        }), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/groups/<int:group_id>', methods=['PUT'])
def update_group(group_id):
    """更新分组（重命名）"""
    data = request.get_json()
    name = data.get('name', '').strip()
    
    if not name:
        return jsonify({'error': 'Group name is required'}), 400
    
    db = get_db()
    group = db.query(FundWatchlistGroup).filter(FundWatchlistGroup.id == group_id).first()
    
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    
    try:
        group.name = name
        db.commit()
        return jsonify({'message': 'Group updated', 'group': {'id': group.id, 'name': group.name}})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/groups/<int:group_id>', methods=['DELETE'])
def delete_group(group_id):
    """删除分组（分组内的基金会变为未分组）"""
    db = get_db()
    group = db.query(FundWatchlistGroup).filter(FundWatchlistGroup.id == group_id).first()
    
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    
    try:
        # 将该分组的基金设为未分组
        db.query(FundWatchlist).filter(FundWatchlist.group_id == group_id).update({'group_id': None})
        db.delete(group)
        db.commit()
        return jsonify({'message': 'Group deleted'})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/groups/reorder', methods=['PUT'])
def reorder_groups():
    """更新分组排序"""
    data = request.get_json()
    order = data.get('order', [])  # [group_id1, group_id2, ...]
    
    if not order:
        return jsonify({'error': 'Order array is required'}), 400
    
    db = get_db()
    
    try:
        for index, group_id in enumerate(order):
            db.query(FundWatchlistGroup).filter(
                FundWatchlistGroup.id == group_id
            ).update({'sort_order': index})
        db.commit()
        return jsonify({'message': 'Groups reordered successfully'})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/move', methods=['PUT'])
def move_fund_to_group():
    """移动基金到指定分组"""
    data = request.get_json()
    fund_code = _normalize_fund_code(data.get('fund_code'))
    group_id = data.get('group_id')  # None 或 0 表示移到未分组
    
    if not fund_code:
        return jsonify({'error': 'Fund code is required'}), 400
    
    db = get_db()
    fund = db.query(FundWatchlist).filter(FundWatchlist.fund_code == fund_code).first()
    
    if not fund:
        return jsonify({'error': 'Fund not in watchlist'}), 404
    
    try:
        fund.group_id = group_id if group_id and group_id > 0 else None
        db.commit()
        return jsonify({'message': 'Fund moved successfully'})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500


def _value_to_string(value):
    if value is None:
        return None
    return str(value)


def _upsert_fund_estimate(
    db,
    fund_code,
    name=None,
    net_worth=None,
    net_worth_date=None,
    estimate_value=None,
    estimate_change=None,
    estimate_time=None,
):
    estimate_record = db.query(FundEstimate).filter(
        FundEstimate.fund_code == fund_code
    ).first()

    if estimate_record:
        estimate_record.name = name
        # 只有净值日期更新时才覆盖（防止 fundgz CDN 返回旧数据覆盖新数据）
        if net_worth_date and (not estimate_record.net_worth_date or str(net_worth_date) >= str(estimate_record.net_worth_date)):
            estimate_record.net_worth = net_worth
            estimate_record.net_worth_date = net_worth_date
        estimate_record.estimate_value = estimate_value
        estimate_record.estimate_change = estimate_change
        estimate_record.estimate_time = estimate_time
    else:
        estimate_record = FundEstimate(
            fund_code=fund_code,
            name=name,
            net_worth=net_worth,
            net_worth_date=net_worth_date,
            estimate_value=estimate_value,
            estimate_change=estimate_change,
            estimate_time=estimate_time
        )
        db.add(estimate_record)

    return {
        'fund_code': fund_code,
        'estimate_value': estimate_value,
        'estimate_change': estimate_change,
        'estimate_time': estimate_time,
        'net_worth': net_worth,
        'net_worth_date': net_worth_date
    }


def _upsert_data_service_estimate(db, fund_code, estimate_data):
    return _upsert_fund_estimate(
        db,
        fund_code,
        name=estimate_data.get('name'),
        net_worth=_value_to_string(estimate_data.get('nav')),
        net_worth_date=estimate_data.get('navDate'),
        estimate_value=_value_to_string(estimate_data.get('estimatedNav')),
        estimate_change=_value_to_string(estimate_data.get('estimatedChangePercent')),
        estimate_time=estimate_data.get('estimateTime')
    )


def _latest_nav_from_data_service(fund_code):
    payload = get_data_service_client().get_fund_nav_history(fund_code)
    items = _nav_history_payload_items(payload)
    latest = None
    for item in items:
        if not isinstance(item, dict):
            continue
        date = item.get('date')
        nav = item.get('nav')
        if not date or nav is None:
            continue
        if latest is None or str(date) > str(latest.get('date')):
            latest = item
    if not latest:
        return None
    return {
        'date': str(latest.get('date')),
        'nav': latest.get('nav'),
        'daily_return': latest.get('dailyReturn'),
    }


def _first_present(mapping, keys):
    for key in keys:
        if isinstance(mapping, dict) and key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return None


def _trend_daily_return(rows):
    if not isinstance(rows, list) or not rows:
        return None

    normalized = [
        row for row in rows
        if isinstance(row, dict) and row.get('date') is not None and row.get('net_worth') is not None
    ]
    if not normalized:
        return None

    normalized.sort(key=lambda item: str(item.get('date') or ''))
    latest = normalized[-1]
    daily_return = _first_present(latest, ('dailyReturn', 'equityReturn', 'growth_rate'))
    if daily_return is not None:
        return daily_return

    if len(normalized) >= 2:
        try:
            prev_nav = float(normalized[-2].get('net_worth'))
            curr_nav = float(latest.get('net_worth'))
            if prev_nav:
                return round((curr_nav - prev_nav) / prev_nav * 100, 4)
        except Exception:
            return None
    return None


def _latest_nav_from_fund_trend(db, fund_code):
    trend = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
    rows = _json_loads(trend.net_worth_trend_json, []) if trend else []
    if not isinstance(rows, list) or not rows:
        return None

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = row.get('date')
        nav = row.get('net_worth')
        if date is None or nav is None:
            continue
        normalized.append(row)
    if not normalized:
        return None

    normalized.sort(key=lambda item: str(item.get('date') or ''))
    latest = normalized[-1]
    daily_return = _trend_daily_return(normalized)

    return {
        'date': str(latest.get('date')),
        'nav': latest.get('net_worth'),
        'daily_return': daily_return,
    }


def _sync_latest_official_nav(db, fund_code, result=None):
    latest = None
    try:
        latest = _latest_nav_from_data_service(fund_code)
    except Exception as exc:
        print(f"sync official nav: DataService unavailable for {fund_code}: {exc}")

    if not latest:
        latest = _latest_nav_from_fund_trend(db, fund_code)
    if not latest:
        return result

    estimate = db.query(FundEstimate).filter(FundEstimate.fund_code == fund_code).first()
    if not estimate:
        estimate = FundEstimate(fund_code=fund_code)
        db.add(estimate)

    latest_date = latest.get('date')
    current_date = estimate.net_worth_date
    if latest_date and (not current_date or str(latest_date) >= str(current_date)):
        estimate.net_worth = _value_to_string(latest.get('nav'))
        estimate.net_worth_date = latest_date
        if latest.get('daily_return') is not None:
            estimate.estimate_change = _value_to_string(latest.get('daily_return'))

        if isinstance(result, dict):
            result['net_worth'] = estimate.net_worth
            result['net_worth_date'] = estimate.net_worth_date
            result['estimate_change'] = estimate.estimate_change
            result['official_nav_synced'] = True

    return result


def _refresh_fundgz_estimate(db, fund_code):
    real_time_url = f"http://fundgz.1234567.com.cn/js/{fund_code}.js"
    response = requests.get(real_time_url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }, timeout=3)

    if response.status_code != 200:
        return None

    match = re.search(r"jsonpgz\((.*?)\);", response.text)
    if not match:
        return None

    rt_data = json.loads(match.group(1))
    if not rt_data:
        return None

    net_worth = rt_data.get('dwjz')
    net_worth_date = rt_data.get('jzrq')

    # 兜底：如果 fundgz CDN 延迟，从 FundTrend JSON 取最新的净值
    # FundTrend 在查看详情页时由 pingzhongdata 写入，更新更及时
    try:
        trend = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
        if trend and trend.net_worth_trend_json:
            trend_data = json.loads(trend.net_worth_trend_json)
            if isinstance(trend_data, list) and trend_data:
                last = trend_data[-1]
                if isinstance(last, dict):
                    t_nw = last.get('net_worth')
                    t_date = last.get('date')
                    if t_nw is not None and t_date is not None:
                        if not net_worth_date or str(t_date) > str(net_worth_date):
                            net_worth = str(t_nw)
                            net_worth_date = str(t_date)
    except Exception:
        pass  # FundTrend 可能没有数据，不影响

    return _upsert_fund_estimate(
        db,
        fund_code,
        name=rt_data.get('name'),
        net_worth=net_worth,
        net_worth_date=net_worth_date,
        estimate_value=rt_data.get('gsz'),
        estimate_change=rt_data.get('gszzl'),
        estimate_time=rt_data.get('gztime')
    )


@app.route('/api/watchlist/refresh-estimates', methods=['GET', 'POST'])
def refresh_watchlist_estimates():
    """
    批量刷新自选基金的实时估值数据
    此接口专门用于快速获取实时估值，不涉及完整基金数据更新
    """
    db = get_db()
    
    # 获取所有自选基金代码
    watchlist = db.query(FundWatchlist).all()
    if not watchlist:
        return jsonify({'message': 'Watchlist is empty', 'updated': 0})
    
    fund_codes = [item.fund_code for item in watchlist]
    updated_count = 0
    results = []
    data_service_success_count = 0
    data_service_failed_count = 0
    fallback_success_count = 0
    fallback_failed_count = 0
    fallback_codes = list(fund_codes)

    try:
        data_service_payload = get_data_service_client().get_fund_estimates(fund_codes)
        ds_data = data_service_payload.get('data', {}) if isinstance(data_service_payload, dict) else {}

        # New format: data.items (successes) + data.failed (failures)
        ds_items = ds_data.get('items', []) if isinstance(ds_data, dict) else []
        ds_failed = ds_data.get('failed', []) if isinstance(ds_data, dict) else []

        successful_codes = set()

        # Process successful items
        for item in ds_items:
            if not isinstance(item, dict):
                continue
            fund_code = item.get('code')
            if fund_code not in fund_codes:
                continue
            if isinstance(item.get('data'), dict):
                result = _upsert_data_service_estimate(db, fund_code, item['data'])
                results.append(_sync_latest_official_nav(db, fund_code, result))
                successful_codes.add(fund_code)
                data_service_success_count += 1
                updated_count += 1

        # Track explicitly failed items from DataService
        for item in ds_failed:
            if not isinstance(item, dict):
                continue
            fund_code = item.get('code')
            if fund_code and fund_code in fund_codes:
                data_service_failed_count += 1

        fallback_codes = [code for code in fund_codes if code not in successful_codes]
    except DataServiceError as e:
        data_service_failed_count = len(fund_codes)
        fallback_codes = list(fund_codes)
        print(f"DataService batch estimate unavailable, fallback to fundgz: {e}")
    
    for fund_code in fallback_codes:
        try:
            # 只获取实时估值数据（轻量级请求）
            fallback_result = _refresh_fundgz_estimate(db, fund_code)
            
            if fallback_result:
                fallback_result = _sync_latest_official_nav(db, fund_code, fallback_result)
                updated_count += 1
                fallback_success_count += 1
                results.append(fallback_result)
        except Exception as e:
            # 单个基金失败不影响其他
            print(f"刷新 {fund_code} 估值失败: {e}")
            continue
    fallback_failed_count = max(0, len(fallback_codes) - fallback_success_count)
    print(
        "refresh_watchlist_estimates: "
        f"data_service_success={data_service_success_count}, "
        f"data_service_failed={data_service_failed_count}, "
        f"fallback_success={fallback_success_count}, "
        f"fallback_failed={fallback_failed_count}"
    )

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    
    return jsonify({
        'message': f'Updated {updated_count} funds',
        'updated': updated_count,
        'total': len(fund_codes),
        'data_service_success': data_service_success_count,
        'data_service_failed': data_service_failed_count,
        'fallback_success': fallback_success_count,
        'fallback_failed': fallback_failed_count,
        'data': results
    })


# ==================== 风险指标计算 ====================

def calculate_risk_metrics(net_worth_trend):
    """
    计算基金风险指标：最大回撤、夏普比率、年化波动率、年化收益率
    net_worth_trend: [{'date': '2024-01-01', 'net_worth': 1.0}, ...]
    """
    if not net_worth_trend or len(net_worth_trend) < 30:
        return None
    
    # 按日期排序
    sorted_data = sorted(net_worth_trend, key=lambda x: x.get('date', ''))
    
    # 转换为净值数组和日期数组
    dates = []
    values = []
    for item in sorted_data:
        if item.get('net_worth') is not None:
            dates.append(item.get('date'))
            values.append(float(item.get('net_worth')))
    
    # 过滤首日异常数据（如面值1.0与实际净值100+差异巨大）
    # 这种情况会导致波动率和回撤计算极其离谱
    if len(values) >= 2:
        v0 = values[0]
        v1 = values[1]
        if v0 > 0 and abs((v1 - v0) / v0) > 0.5:
            values.pop(0)
            dates.pop(0)
    
    if len(values) < 30:
        return None
    
    now = datetime.now()
    
    def get_period_data(months):
        """获取指定时间段的数据"""
        if months == 'all':
            return values, dates
        
        cutoff_date = (now - timedelta(days=months * 30)).strftime('%Y-%m-%d')
        period_values = []
        period_dates = []
        for i, d in enumerate(dates):
            if d >= cutoff_date:
                period_values.append(values[i])
                period_dates.append(d)
        return period_values, period_dates
    
    def calc_max_drawdown(period_values):
        """计算最大回撤"""
        if len(period_values) < 2:
            return None
        
        peak = period_values[0]
        max_dd = 0
        
        for value in period_values:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak * 100
            if drawdown > max_dd:
                max_dd = drawdown
        
        return round(max_dd, 2)
    
    def calc_daily_returns(period_values):
        """计算日收益率序列"""
        if len(period_values) < 2:
            return []
        returns = []
        for i in range(1, len(period_values)):
            if period_values[i-1] != 0:
                ret = (period_values[i] - period_values[i-1]) / period_values[i-1]
                returns.append(ret)
        return returns
    
    def calc_annual_return(period_values, trading_days):
        """计算年化收益率"""
        if len(period_values) < 2 or period_values[0] == 0:
            return None
        total_return = (period_values[-1] - period_values[0]) / period_values[0]
        if trading_days <= 0:
            return None
        annual_return = ((1 + total_return) ** (252 / trading_days) - 1) * 100
        return round(annual_return, 2)
    
    def calc_volatility(daily_returns):
        """计算年化波动率"""
        if len(daily_returns) < 10:
            return None
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
        daily_vol = math.sqrt(variance)
        annual_vol = daily_vol * math.sqrt(252) * 100
        return round(annual_vol, 2)
    
    def calc_sharpe_ratio(annual_return, volatility, risk_free_rate=2.0):
        """计算夏普比率，假设无风险利率为2%"""
        if volatility is None or volatility == 0 or annual_return is None:
            return None
        sharpe = (annual_return - risk_free_rate) / volatility
        return round(sharpe, 2)
    
    result = {}
    
    # 计算不同时间段的最大回撤
    for period, months in [('3m', 3), ('6m', 6), ('1y', 12), ('3y', 36), ('all', 'all')]:
        period_values, _ = get_period_data(months)
        result[f'max_drawdown_{period}'] = calc_max_drawdown(period_values)
    
    # 计算1年和3年的年化收益率、波动率、夏普比率
    # 重要：对于数据不足的周期，不计算指标（返回None），避免年化放大产生误导性数据
    min_trading_days = {
        '1y': 200,  # 至少200个交易日才计算1年期指标（约10个月）
        '3y': 600,  # 至少600个交易日才计算3年期指标（约2.5年）
    }
    
    for period, months in [('1y', 12), ('3y', 36)]:
        period_values, period_dates = get_period_data(months)
        trading_days = len(period_values)
        
        # 检查数据是否充足
        min_days = min_trading_days.get(period, 30)
        if trading_days < min_days:
            # 数据不足，不计算该周期的指标
            result[f'annual_return_{period}'] = None
            result[f'volatility_{period}'] = None
            result[f'sharpe_ratio_{period}'] = None
            result[f'calmar_ratio_{period}'] = None
            continue
        
        daily_returns = calc_daily_returns(period_values)
        annual_return = calc_annual_return(period_values, trading_days)
        volatility = calc_volatility(daily_returns)
        sharpe = calc_sharpe_ratio(annual_return, volatility)
        
        # 额外检查：如果波动率异常大（>500%），说明数据有问题，放弃该计算结果
        if volatility is not None and volatility > 500:
            result[f'annual_return_{period}'] = None
            result[f'volatility_{period}'] = None
            result[f'sharpe_ratio_{period}'] = None
            result[f'calmar_ratio_{period}'] = None
            continue
        
        result[f'annual_return_{period}'] = annual_return
        result[f'volatility_{period}'] = volatility
        result[f'sharpe_ratio_{period}'] = sharpe
        
        # 计算卡玛比率
        max_dd = result.get(f'max_drawdown_{period}')
        if annual_return is not None and max_dd is not None and max_dd > 0:
            result[f'calmar_ratio_{period}'] = round(annual_return / max_dd, 2)
        else:
            result[f'calmar_ratio_{period}'] = None
    
    return result


def is_data_fresh(updated_time, days=7):
    """检查数据是否在指定天数内"""
    if not updated_time:
        return False
    return (datetime.now() - updated_time).days < days


@app.route('/api/fund/<fund_code>/compare-data', methods=['GET'])
def get_fund_compare_data(fund_code):
    fund_code = _normalize_fund_code(fund_code)
    """
    获取基金对比数据，优先使用数据库缓存（1周内）
    返回完整的基金详情数据和风险指标
    
    数据来源（统一）：
    - 基本信息、业绩: FundBasicInfo
    - 走势数据: FundTrend
    - 扩展数据: FundExtraData
    - 风险指标: FundRiskMetrics
    """
    db = get_db()
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    
    try:
        # 检查缓存数据是否新鲜（1周内）
        trend_record = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
        risk_record = db.query(FundRiskMetrics).filter(FundRiskMetrics.fund_code == fund_code).first()
        
        use_cache = (
            not force_refresh and 
            trend_record and 
            is_data_fresh(trend_record.updated_time, days=7)
        )
        
        if use_cache:
            # 使用缓存数据
            data = _build_cached_response(db, fund_code)
            if data:
                # 检查风险指标是否存在且新鲜
                risk_data_valid = (
                    risk_record and 
                    is_data_fresh(risk_record.updated_time, days=7) and
                    risk_record.sharpe_ratio_1y is not None
                )

                # 额外检查：如果波动率异常大（>1000%），说明之前计算时受到了脏数据影响，需要重算
                if risk_data_valid and risk_record.volatility_1y and risk_record.volatility_1y > 1000:
                    risk_data_valid = False
                
                if risk_data_valid:
                    data['risk_metrics'] = {
                        'max_drawdown_3m': risk_record.max_drawdown_3m,
                        'max_drawdown_6m': risk_record.max_drawdown_6m,
                        'max_drawdown_1y': risk_record.max_drawdown_1y,
                        'max_drawdown_3y': risk_record.max_drawdown_3y,
                        'max_drawdown_all': risk_record.max_drawdown_all,
                        'sharpe_ratio_1y': risk_record.sharpe_ratio_1y,
                        'sharpe_ratio_3y': risk_record.sharpe_ratio_3y,
                        'volatility_1y': risk_record.volatility_1y,
                        'volatility_3y': risk_record.volatility_3y,
                        'annual_return_1y': risk_record.annual_return_1y,
                        'annual_return_3y': risk_record.annual_return_3y,
                        'calmar_ratio_1y': risk_record.calmar_ratio_1y,
                        'calmar_ratio_3y': risk_record.calmar_ratio_3y,
                    }
                else:
                    # 风险指标缺失，从缓存的净值数据计算
                    net_worth_trend = data.get('net_worth_trend', [])
                    risk_metrics = calculate_risk_metrics(net_worth_trend)
                    
                    if risk_metrics:
                        # 保存到 FundRiskMetrics
                        _save_risk_metrics(db, fund_code, risk_metrics)
                        db.commit()
                        data['risk_metrics'] = risk_metrics
                    else:
                        data['risk_metrics'] = {}
                
                data['data_source'] = 'cache'
                data['cache_time'] = trend_record.updated_time.isoformat() if trend_record.updated_time else None
                return jsonify(data)
        
        # 从API获取新数据
        api_data = fund_api.get_fund_data(fund_code)
        if not api_data:
            # 如果API失败，尝试返回缓存数据
            if trend_record:
                data = _build_cached_response(db, fund_code)
                if data:
                    if risk_record and risk_record.sharpe_ratio_1y is not None:
                        data['risk_metrics'] = {
                            'max_drawdown_3m': risk_record.max_drawdown_3m,
                            'max_drawdown_6m': risk_record.max_drawdown_6m,
                            'max_drawdown_1y': risk_record.max_drawdown_1y,
                            'max_drawdown_3y': risk_record.max_drawdown_3y,
                            'max_drawdown_all': risk_record.max_drawdown_all,
                            'sharpe_ratio_1y': risk_record.sharpe_ratio_1y,
                            'sharpe_ratio_3y': risk_record.sharpe_ratio_3y,
                            'volatility_1y': risk_record.volatility_1y,
                            'volatility_3y': risk_record.volatility_3y,
                            'annual_return_1y': risk_record.annual_return_1y,
                            'annual_return_3y': risk_record.annual_return_3y,
                            'calmar_ratio_1y': risk_record.calmar_ratio_1y,
                            'calmar_ratio_3y': risk_record.calmar_ratio_3y,
                        }
                    else:
                        net_worth_trend = data.get('net_worth_trend', [])
                        risk_metrics = calculate_risk_metrics(net_worth_trend)
                        if risk_metrics:
                            _save_risk_metrics(db, fund_code, risk_metrics)
                            db.commit()
                        data['risk_metrics'] = risk_metrics or {}
                    data['data_source'] = 'stale_cache'
                    return jsonify(data)
            return jsonify({'error': 'Failed to fetch fund data'}), 500
        
        # 计算风险指标
        net_worth_trend = api_data.get('net_worth_trend', [])
        risk_metrics = calculate_risk_metrics(net_worth_trend)
        
        # 保存到数据库（所有相关表）
        _save_fund_data_to_db(db, fund_code, api_data)
        if risk_metrics:
            _save_risk_metrics(db, fund_code, risk_metrics)
        db.commit()
        
        # 返回数据
        api_data['risk_metrics'] = risk_metrics or {}
        api_data['data_source'] = 'api'
        return jsonify(api_data)
        
    except Exception as e:
        print(f"Error fetching fund compare data: {e}")
        db.rollback()
        return jsonify({'error': str(e)}), 500


def _save_risk_metrics(db: Session, fund_code: str, risk_metrics: dict):
    """保存风险指标到 FundRiskMetrics 表"""
    if not risk_metrics:
        return
    
    risk_record = db.query(FundRiskMetrics).filter(FundRiskMetrics.fund_code == fund_code).first()
    
    if risk_record:
        for key, value in risk_metrics.items():
            if hasattr(risk_record, key):
                setattr(risk_record, key, value)
        risk_record.updated_time = datetime.now()
    else:
        risk_record = FundRiskMetrics(
            fund_code=fund_code,
            **{k: v for k, v in risk_metrics.items() if hasattr(FundRiskMetrics, k)}
        )
        db.add(risk_record)


def _save_fund_data_to_db(db: Session, fund_code: str, data: dict):
    """保存基金数据到数据库"""
    try:
        # 保存基本信息
        basic_info = data.get('basic_info', {})
        performance = data.get('performance', {})
        # 解析收益率用于排序
        return_1y_val = None
        try:
            return_1y_val = float(performance.get('1_year_return')) if performance.get('1_year_return') else None
        except (ValueError, TypeError):
            pass
        
        basic_record = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
        if basic_record:
            basic_record.fund_name = basic_info.get('fund_name', '')
            basic_record.fund_type = basic_info.get('fund_type', '')
            basic_record.return_1y = return_1y_val
            basic_record.basic_json = _json_dumps(basic_info)
            basic_record.performance_json = _json_dumps(performance)
            basic_record.updated_time = datetime.now()
        else:
            basic_record = FundBasicInfo(
                fund_code=fund_code,
                fund_name=basic_info.get('fund_name', ''),
                fund_type=basic_info.get('fund_type', ''),
                return_1y=return_1y_val,
                basic_json=_json_dumps(basic_info),
                performance_json=_json_dumps(performance)
            )
            db.add(basic_record)
        
        # 保存走势数据
        trend_record = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
        if trend_record:
            trend_record.net_worth_trend_json = _json_dumps(data.get('net_worth_trend', []))
            trend_record.accumulated_net_worth_json = _json_dumps(data.get('accumulated_net_worth', []))
            trend_record.position_trend_json = _json_dumps(data.get('position_trend', []))
            trend_record.total_return_trend_json = _json_dumps(data.get('total_return_trend', []))
            trend_record.ranking_trend_json = _json_dumps(data.get('ranking_trend', []))
            trend_record.ranking_percentage_json = _json_dumps(data.get('ranking_percentage', []))
            trend_record.scale_fluctuation_json = _json_dumps(data.get('scale_fluctuation', {}))
            trend_record.updated_time = datetime.now()
        else:
            trend_record = FundTrend(
                fund_code=fund_code,
                net_worth_trend_json=_json_dumps(data.get('net_worth_trend', [])),
                accumulated_net_worth_json=_json_dumps(data.get('accumulated_net_worth', [])),
                position_trend_json=_json_dumps(data.get('position_trend', [])),
                total_return_trend_json=_json_dumps(data.get('total_return_trend', [])),
                ranking_trend_json=_json_dumps(data.get('ranking_trend', [])),
                ranking_percentage_json=_json_dumps(data.get('ranking_percentage', [])),
                scale_fluctuation_json=_json_dumps(data.get('scale_fluctuation', {}))
            )
            db.add(trend_record)
        
        # 保存额外数据
        extra_record = db.query(FundExtraData).filter(FundExtraData.fund_code == fund_code).first()
        if extra_record:
            extra_record.holder_structure_json = _json_dumps(data.get('holder_structure', {}))
            extra_record.asset_allocation_json = _json_dumps(data.get('asset_allocation', {}))
            extra_record.performance_evaluation_json = _json_dumps(data.get('performance_evaluation', {}))
            extra_record.fund_managers_json = _json_dumps(data.get('fund_managers', []))
            extra_record.subscription_redemption_json = _json_dumps(data.get('subscription_redemption', {}))
            extra_record.same_type_funds_json = _json_dumps(data.get('same_type_funds', []))
            extra_record.updated_time = datetime.now()
        else:
            extra_record = FundExtraData(
                fund_code=fund_code,
                holder_structure_json=_json_dumps(data.get('holder_structure', {})),
                asset_allocation_json=_json_dumps(data.get('asset_allocation', {})),
                performance_evaluation_json=_json_dumps(data.get('performance_evaluation', {})),
                fund_managers_json=_json_dumps(data.get('fund_managers', [])),
                subscription_redemption_json=_json_dumps(data.get('subscription_redemption', {})),
                same_type_funds_json=_json_dumps(data.get('same_type_funds', []))
            )
            db.add(extra_record)
        
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error saving fund data to db: {e}")


# ==================== 基金筛选功能 ====================

# 全局变量：批量更新状态
SCREENING_SNAPSHOT_TYPES = ['gp', 'hh', 'zq', 'zs', 'qdii', 'fof']


def _fund_type_lookup():
    lookup = {}
    for item in fund_list_cache.fund_list:
        code = _normalize_fund_code(item.get('CODE', ''))
        if code:
            lookup[code] = {
                'name': item.get('NAME') or '',
                'type': item.get('TYPE') or '',
            }
    return lookup


def _matches_fund_type(fund_type, selected_types):
    if not selected_types:
        return True
    text = str(fund_type or '')
    return any(str(selected or '') in text for selected in selected_types)


def _snapshot_performance(item):
    return {
        '1_month_return': item.get('return1m'),
        '3_month_return': item.get('return3m'),
        '6_month_return': item.get('return6m'),
        '1_year_return': item.get('return1y'),
        '2_year_return': item.get('return2y'),
        '3_year_return': item.get('return3y'),
        'ytd_return': item.get('ytd'),
        'since_inception_return': item.get('sinceInception'),
    }


def _save_screening_snapshot_item(db, item, type_lookup):
    code = _normalize_fund_code(item.get('code', ''))
    if not code:
        return False

    cached = type_lookup.get(code, {})
    fund_name = item.get('name') or cached.get('name') or code
    fund_type = item.get('type') or cached.get('type') or ''
    performance = _snapshot_performance(item)
    basic_info = {
        'fund_code': code,
        'fund_name': fund_name,
        'fund_type': fund_type,
        'net_worth': item.get('nav'),
        'net_worth_date': item.get('navDate'),
        'fee': item.get('fee'),
        'source': item.get('source'),
    }

    record = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == code).first()
    if record:
        record.fund_name = fund_name
        record.fund_type = fund_type
        record.return_1y = item.get('return1y')
        record.basic_json = _json_dumps(basic_info)
        record.performance_json = _json_dumps(performance)
        record.updated_time = datetime.now()
    else:
        db.add(FundBasicInfo(
            fund_code=code,
            fund_name=fund_name,
            fund_type=fund_type,
            return_1y=item.get('return1y'),
            basic_json=_json_dumps(basic_info),
            performance_json=_json_dumps(performance),
        ))

    _refresh_fund_industry_tag(db, code, fetch_holdings_if_missing=False)
    return True


def _fetch_screening_snapshot_items(limit=None, db=None, task_id=None):
    items = []
    max_count = int(limit) if limit else None
    client = get_data_service_client()
    total_types = len(SCREENING_SNAPSHOT_TYPES)
    t0 = time.time()

    print(f"[筛查更新] 开始获取批量排行 (共{total_types}类)...", flush=True)

    for idx, type_code in enumerate(SCREENING_SNAPSHOT_TYPES, 1):
        if max_count and len(items) >= max_count:
            break

        t1 = time.time()
        _set_screening_progress(
            db, task_id,
            message=f"获取排行 ({idx}/{total_types}): {type_code}",
            current_count=len(items),
        )

        try:
            payload = client.get_fund_screening_snapshot([type_code], page_size=500, sort='1nzf')
            elapsed = time.time() - t1
        except DataServiceError as e:
            print(f"[筛查更新] {type_code}: DataService错误({time.time()-t1:.1f}s): {e}", flush=True)
            _set_screening_progress(
                db, task_id,
                message=f"获取排行 ({idx}/{total_types}): {type_code} 失败, 跳过",
                current_count=len(items),
            )
            continue
        except Exception as e:
            print(f"[筛查更新] {type_code}: 未知错误({time.time()-t1:.1f}s): {e}", flush=True)
            _set_screening_progress(
                db, task_id,
                message=f"获取排行 ({idx}/{total_types}): {type_code} 异常, 跳过",
                current_count=len(items),
            )
            continue

        data = payload.get('data', {}) if isinstance(payload, dict) else {}
        page_items = data.get('items', []) if isinstance(data, dict) else []
        failed_pages = data.get('failedPages', []) if isinstance(data, dict) else []
        if failed_pages:
            print(f"[筛查更新] {type_code}: {len(failed_pages)}个失败页: {failed_pages[:3]}", flush=True)

        remaining = None if max_count is None else max_count - len(items)
        new_items = page_items if remaining is None else page_items[:remaining]
        items.extend(new_items)

        print(f"[筛查更新] {type_code}: +{len(new_items)}只 ({elapsed:.1f}s), 累计{len(items)}只", flush=True)

        _set_screening_progress(
            db, task_id,
            message=f"获取排行 ({idx}/{total_types}): {type_code} ✓ (+{len(new_items)}, 累计 {len(items)})",
            current_count=len(items),
        )

    print(f"[筛查更新] 批量排行获取完成: {len(items)}只, 耗时{time.time()-t0:.1f}s", flush=True)
    return items


def _nav_history_to_risk_input(payload):
    data = payload.get('data', {}) if isinstance(payload, dict) else {}
    items = data.get('items', []) if isinstance(data, dict) else []
    trend = []
    for item in items:
        if not isinstance(item, dict):
            continue
        nav = item.get('nav')
        date = item.get('date')
        if nav is not None and date:
            trend.append({'date': str(date), 'net_worth': nav})
    return trend


def _nav_history_payload_items(payload):
    data = payload.get('data', {}) if isinstance(payload, dict) else {}
    items = data.get('items', []) if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def _latest_cached_nav_date(db, fund_code):
    row = db.query(FundNavHistory.trade_date).filter(
        FundNavHistory.fund_code == fund_code
    ).order_by(desc(FundNavHistory.trade_date)).first()
    return row[0] if row else None


def _load_nav_history_rows(db, fund_code):
    rows = db.query(FundNavHistory).filter(
        FundNavHistory.fund_code == fund_code,
        FundNavHistory.nav.isnot(None),
    ).order_by(FundNavHistory.trade_date.asc()).all()
    return [
        {
            'date': row.trade_date,
            'net_worth': row.nav,
        }
        for row in rows
    ]


def _upsert_nav_history_rows(db, fund_code, payload):
    # Historical NAV is now used only transiently for risk calculation.
    # Keep this compatibility helper non-persistent to prevent DB growth.
    return len(_nav_history_payload_items(payload))


def _snapshot_nav_date(db, fund_code):
    basic = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
    if not basic:
        return None
    basic_info = _json_loads(basic.basic_json, {})
    return basic_info.get('net_worth_date') or basic_info.get('navDate')


def _nav_cache_is_fresh(db, fund_code):
    latest_cached = _latest_cached_nav_date(db, fund_code)
    latest_snapshot = _snapshot_nav_date(db, fund_code)
    if not latest_cached:
        return False
    if latest_snapshot and latest_cached < str(latest_snapshot):
        return False
    return True


def _sync_nav_history_ifund_style(db, fund_code, force=False):
    fund_code = _normalize_fund_code(fund_code)
    if not fund_code:
        return 0
    if not force and _nav_cache_is_fresh(db, fund_code):
        return 0
    latest_cached = _latest_cached_nav_date(db, fund_code)
    # force=True 时从头全量拉取（用于初始填充），否则增量拉取
    if force:
        start_date = None
    elif latest_cached:
        try:
            start_date = (datetime.strptime(latest_cached, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        except ValueError:
            start_date = None
    else:
        start_date = None
    payload = get_data_service_client().get_fund_nav_history(fund_code, start_date=start_date)
    inserted = _upsert_nav_history_rows(db, fund_code, payload)
    db.commit()
    return inserted


def update_single_fund_risk_metrics(fund_code, db):
    fund_code = _normalize_fund_code(fund_code)
    try:
        payload = get_data_service_client().get_fund_nav_history(fund_code)
        net_worth_trend = _nav_history_to_risk_input(payload)
        if len(net_worth_trend) < 30:
            print(f"[补充风险] 跳过 {fund_code}: 净值不足({len(net_worth_trend)}条)", flush=True)
            return False
        risk_metrics = calculate_risk_metrics(net_worth_trend)
        if not risk_metrics:
            print(f"[补充风险] 跳过 {fund_code}: 风险计算失败(可能数据异常或基金太新)", flush=True)
            return False
        _save_risk_metrics(db, fund_code, risk_metrics)
        _refresh_fund_industry_tag(db, fund_code, fetch_holdings_if_missing=True)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"[补充风险] 异常 {fund_code}: {e}", flush=True)
        return False


def _select_nav_candidates(db, nav_limit):
    rows = db.query(FundBasicInfo, FundScreeningRank).outerjoin(
        FundScreeningRank, FundBasicInfo.fund_code == FundScreeningRank.fund_code
    ).all()
    candidates = []
    for basic, rank in rows:
        if not basic:
            continue
        rank_1y = rank.rank_pct_1y if rank else None
        pass_4433 = bool(rank and rank.pass_4433 == 1)
        if pass_4433 or (rank_1y is not None and rank_1y <= 30):
            candidates.append({
                'code': basic.fund_code,
                'name': basic.fund_name,
                'type': basic.fund_type or '',
                'rank_1y': rank_1y if rank_1y is not None else 9999,
                'pass_4433': pass_4433,
            })

    candidates.sort(key=lambda item: (0 if item['pass_4433'] else 1, item['rank_1y'], item['code']))
    if nav_limit is None or len(candidates) <= nav_limit:
        return candidates

    by_type = {}
    for item in candidates:
        by_type.setdefault(item['type'], []).append(item)

    selected = []
    per_type = max(1, math.ceil(nav_limit / max(len(by_type), 1)))
    for items in by_type.values():
        selected.extend(items[:per_type])
    if len(selected) < nav_limit:
        selected_codes = {item['code'] for item in selected}
        selected.extend(item for item in candidates if item['code'] not in selected_codes)
    return selected[:nav_limit]


def _fund_codes_needing_industry_refresh(db, candidate_codes=None, force=False):
    query = db.query(FundBasicInfo.fund_code)
    if candidate_codes is not None:
        codes = [_normalize_fund_code(code) for code in candidate_codes if _normalize_fund_code(code)]
        if not codes:
            return []
        query = query.filter(FundBasicInfo.fund_code.in_(codes))

    basic_codes = [row[0] for row in query.all() if row[0]]
    if force:
        return basic_codes

    tag_rows = db.query(FundIndustryTag).filter(
        FundIndustryTag.fund_code.in_(basic_codes)
    ).all() if basic_codes else []
    tag_map = {row.fund_code: row for row in tag_rows}

    result = []
    for code in basic_codes:
        tag = tag_map.get(code)
        if not tag:
            result.append(code)
            continue
        if tag.basis == 'missing_holdings' or (tag.industry_count or 0) <= 0:
            result.append(code)
            continue
        if not tag.industry_tag:
            result.append(code)
    return result


def _collect_stock_codes_from_portfolios(db, fund_codes=None):
    query = db.query(FundPortfolio)
    if fund_codes is not None:
        codes = [_normalize_fund_code(code) for code in fund_codes if _normalize_fund_code(code)]
        if not codes:
            return []
        query = query.filter(FundPortfolio.fund_code.in_(codes))

    stock_codes = []
    for portfolio in query.all():
        raw_holdings = _json_loads(portfolio.stock_codes_new_json, []) or _json_loads(portfolio.stock_codes_json, [])
        for item in _portfolio_holding_items(raw_holdings):
            code = _normalize_stock_code(item.get('code'))
            if code and code not in stock_codes:
                stock_codes.append(code)
    return stock_codes


def warm_stock_industry_dictionary(db, fund_codes=None, force=False, limit=None):
    stock_codes = _collect_stock_codes_from_portfolios(db, fund_codes)
    if limit is not None:
        try:
            limit = max(1, int(limit))
            stock_codes = stock_codes[:limit]
        except (TypeError, ValueError):
            pass

    if not stock_codes:
        return {'total': 0, 'cached': 0, 'fetched': 0, 'failed': 0}

    records = db.query(StockIndustry).filter(StockIndustry.stock_code.in_(stock_codes)).all()
    cached_codes = {
        record.stock_code
        for record in records
        if record.industry and not force
    }
    missing_codes = [code for code in stock_codes if force or code not in cached_codes]
    if not missing_codes:
        return {'total': len(stock_codes), 'cached': len(cached_codes), 'fetched': 0, 'failed': 0}

    fetched_map, failed_codes = _fetch_stock_industry_batch(
        db,
        missing_codes,
        force_refresh=force,
        timeout=8.0 if force else 5.0,
    )
    db.commit()
    return {
        'total': len(stock_codes),
        'cached': len(cached_codes),
        'fetched': len(fetched_map),
        'failed': len(failed_codes),
    }


def _akshare_row_value(row, fallback_index, *names):
    for name in names:
        try:
            value = row.get(name)
            if value is not None:
                return value
        except Exception:
            pass
    try:
        return row.iloc[fallback_index]
    except Exception:
        return None


def build_stock_industry_dictionary_from_akshare(db, force=False, board_limit=None, stock_limit=None):
    try:
        import akshare as ak
    except Exception as exc:
        raise RuntimeError(f"akshare not available: {exc}")

    try:
        board_limit = int(board_limit) if board_limit else None
    except (TypeError, ValueError):
        board_limit = None
    try:
        stock_limit = int(stock_limit) if stock_limit else None
    except (TypeError, ValueError):
        stock_limit = None

    try:
        result = _build_stock_industry_dictionary_from_sina(
            db,
            ak,
            force=force,
            board_limit=board_limit,
            stock_limit=stock_limit,
        )
        result['source'] = 'akshare.sina.sector'
        return result
    except Exception as exc:
        print(f"akshare sina industry dictionary failed, fallback to eastmoney: {exc}", flush=True)
        board_df = ak.stock_board_industry_name_em()
        result = _build_stock_industry_dictionary_from_em_boards(
            db,
            ak,
            board_df,
            force=force,
            board_limit=board_limit,
            stock_limit=stock_limit,
        )
        result['source'] = 'akshare.eastmoney.industry_board'
        return result


def _build_stock_industry_dictionary_from_em_boards(db, ak, board_df, force=False, board_limit=None, stock_limit=None):
    if board_df is None or getattr(board_df, 'empty', True):
        return {'boards': 0, 'stocks': 0, 'created_or_updated': 0, 'skipped': 0, 'failed_boards': 0}

    boards = []
    for _, row in board_df.iterrows():
        name = _akshare_row_value(row, 1, '板块名称', '行业名称', '名称', 'name')
        code = _akshare_row_value(row, 0, '板块代码', '代码', 'code')
        if name:
            boards.append({'name': str(name), 'code': str(code or '')})
    if board_limit:
        boards = boards[:board_limit]

    updated = 0
    skipped = 0
    seen_stocks = set()
    failed_boards = 0
    for index, board in enumerate(boards, 1):
        if stock_limit and len(seen_stocks) >= stock_limit:
            break
        try:
            cons_df = ak.stock_board_industry_cons_em(symbol=board['name'])
        except Exception as exc:
            failed_boards += 1
            print(f"akshare stock industry dictionary: failed board {board['name']}: {exc}", flush=True)
            continue
        if cons_df is None or getattr(cons_df, 'empty', True):
            continue

        for _, stock_row in cons_df.iterrows():
            if stock_limit and len(seen_stocks) >= stock_limit:
                break
            stock_code = _normalize_stock_code(_akshare_row_value(stock_row, 1, '代码', '股票代码', 'code'))
            if not stock_code or not re.match(r'^\d{6}$', stock_code):
                continue
            stock_name = _akshare_row_value(stock_row, 2, '名称', '股票名称', 'name')
            if stock_code in seen_stocks:
                continue
            seen_stocks.add(stock_code)

            record = db.query(StockIndustry).filter(StockIndustry.stock_code == stock_code).first()
            if not record:
                record = StockIndustry(stock_code=stock_code)
                db.add(record)
            elif record.industry and not force:
                skipped += 1
                continue

            record.stock_name = str(stock_name or record.stock_name or '')
            record.industry = board['name']
            record.region = record.region or ''
            concepts = _json_loads(record.concepts_json, [])
            if board['name'] not in concepts:
                concepts.append(board['name'])
            record.concepts_json = _json_dumps(concepts[:20])
            record.source = 'akshare.eastmoney.industry_board'
            record.updated_time = datetime.now()
            updated += 1

        if index % 10 == 0:
            db.commit()

    db.commit()
    return {
        'boards': len(boards),
        'stocks': len(seen_stocks),
        'created_or_updated': updated,
        'skipped': skipped,
        'failed_boards': failed_boards,
    }


def _build_stock_industry_dictionary_from_sina(db, ak, force=False, board_limit=None, stock_limit=None):
    board_df = ak.stock_sector_spot()
    if board_df is None or getattr(board_df, 'empty', True):
        return {'boards': 0, 'stocks': 0, 'created_or_updated': 0, 'skipped': 0, 'failed_boards': 0}

    boards = []
    for _, row in board_df.iterrows():
        label = row.get('label') if hasattr(row, 'get') else None
        name = _akshare_row_value(row, 1, '行业', '板块', 'name')
        if label and name:
            boards.append({'label': str(label), 'name': str(name)})
    if board_limit:
        boards = boards[:board_limit]

    updated = 0
    skipped = 0
    seen_stocks = set()
    failed_boards = 0
    for index, board in enumerate(boards, 1):
        if stock_limit and len(seen_stocks) >= stock_limit:
            break
        try:
            cons_df = ak.stock_sector_detail(sector=board['label'])
        except Exception as exc:
            failed_boards += 1
            print(f"akshare sina industry dictionary: failed board {board['name']}: {exc}", flush=True)
            continue
        if cons_df is None or getattr(cons_df, 'empty', True):
            continue

        for _, stock_row in cons_df.iterrows():
            if stock_limit and len(seen_stocks) >= stock_limit:
                break
            stock_code = _normalize_stock_code(stock_row.get('code') if hasattr(stock_row, 'get') else None)
            if not stock_code or not re.match(r'^\d{6}$', stock_code):
                continue
            stock_name = stock_row.get('name') if hasattr(stock_row, 'get') else None
            if stock_code in seen_stocks:
                continue
            seen_stocks.add(stock_code)

            record = db.query(StockIndustry).filter(StockIndustry.stock_code == stock_code).first()
            if not record:
                record = StockIndustry(stock_code=stock_code)
                db.add(record)
            elif record.industry and not force:
                skipped += 1
                continue

            record.stock_name = str(stock_name or record.stock_name or '')
            record.industry = board['name']
            record.region = record.region or ''
            concepts = _json_loads(record.concepts_json, [])
            if board['name'] not in concepts:
                concepts.append(board['name'])
            record.concepts_json = _json_dumps(concepts[:20])
            record.source = 'akshare.sina.sector'
            record.updated_time = datetime.now()
            updated += 1

        if index % 10 == 0:
            db.commit()

    db.commit()
    return {
        'boards': len(boards),
        'stocks': len(seen_stocks),
        'created_or_updated': updated,
        'skipped': skipped,
        'failed_boards': failed_boards,
    }


def batch_refresh_fund_industry_tags(
    db,
    fund_codes=None,
    task_id=None,
    force=False,
    limit=None,
    build_full_dictionary=False,
    allow_missing_stock_network=False,
):
    codes = _fund_codes_needing_industry_refresh(db, fund_codes, force=force)
    if limit is not None:
        try:
            limit = max(1, int(limit))
            codes = codes[:limit]
        except (TypeError, ValueError):
            pass

    total = len(codes)
    if total == 0:
        return {'success_count': 0, 'fail_count': 0, 'total': 0}

    full_dictionary_result = None
    full_dictionary_ok = False
    if build_full_dictionary:
        _set_screening_progress(
            db,
            task_id,
            message='构建全市场股票行业字典...',
            target_count=total,
            current_count=0,
            success_count=0,
            fail_count=0,
            current_item='',
        )
        try:
            full_dictionary_result = build_stock_industry_dictionary_from_akshare(db)
            full_dictionary_ok = True
            print(f"[stock industry dictionary] akshare full build: {full_dictionary_result}", flush=True)
        except Exception as exc:
            print(f"[stock industry dictionary] akshare full build failed: {exc}", flush=True)

    _set_screening_progress(
        db,
        task_id,
        message='读取本地股票行业字典...',
        target_count=total,
        current_count=0,
        success_count=0,
        fail_count=0,
        current_item='',
    )
    dictionary_result = {
        'portfolio_stock_total': len(_collect_stock_codes_from_portfolios(db, codes)),
        'mapped_stock_total': db.query(StockIndustry).filter(
            StockIndustry.industry.isnot(None),
            StockIndustry.industry != '',
        ).count(),
    }
    print(f"[industry dictionary] local lookup: {dictionary_result}", flush=True)

    if allow_missing_stock_network and (not build_full_dictionary or full_dictionary_ok):
        portfolio_stock_codes = _collect_stock_codes_from_portfolios(db, codes)
        mapped_rows = db.query(StockIndustry).filter(
            StockIndustry.stock_code.in_(portfolio_stock_codes),
            StockIndustry.industry.isnot(None),
            StockIndustry.industry != '',
        ).all() if portfolio_stock_codes else []
        mapped_codes = {row.stock_code for row in mapped_rows}
        missing_stock_codes = [code for code in portfolio_stock_codes if code not in mapped_codes]
        if missing_stock_codes:
            _set_screening_progress(
                db,
                task_id,
                message=f'批量补充缺失股票行业({len(missing_stock_codes)}只)...',
                target_count=total,
                current_count=0,
                success_count=0,
                fail_count=0,
                current_item='',
            )
            fetched_map, failed_codes = _fetch_stock_industry_batch(
                db,
                missing_stock_codes,
                force_refresh=False,
                timeout=5.0,
            )
            db.commit()
            print(
                f"[industry dictionary] missing stocks fetched: "
                f"{len(fetched_map)}/{len(missing_stock_codes)} "
                f"(failed {len(failed_codes)})",
                flush=True,
            )
    elif allow_missing_stock_network and build_full_dictionary and not full_dictionary_ok:
        print(
            "[industry dictionary] skip missing stock network fetch because akshare full build failed; "
            "using local stock_industry only",
            flush=True,
        )

    success = 0
    fail = 0
    _set_screening_progress(
        db,
        task_id,
        message='刷新基金行业标签...',
        target_count=total,
        current_count=0,
        success_count=0,
        fail_count=0,
        current_item='',
    )

    for index, code in enumerate(codes, 1):
        if screening_stop_flag:
            break

        _set_screening_progress(
            db,
            task_id,
            current_count=index,
            current_item=code,
        )
        try:
            tag = _refresh_fund_industry_tag(
                db,
                code,
                fetch_holdings_if_missing=True,
                force_stock_refresh=False,
                allow_stock_network=False,
            )
            if tag:
                success += 1
            else:
                fail += 1
        except Exception as exc:
            print(f"batch industry tag refresh failed for {code}: {exc}", flush=True)
            fail += 1

        if index % 20 == 0:
            db.commit()
            _set_screening_progress(
                db,
                task_id,
                success_count=success,
                fail_count=fail,
            )

    db.commit()
    _set_screening_progress(
        db,
        task_id,
        success_count=success,
        fail_count=fail,
        current_item='',
    )
    return {'success_count': success, 'fail_count': fail, 'total': total}


def _create_data_fetch_task(db, task_type, options=None, message=''):
    task = DataFetchTask(
        task_type=task_type,
        status='running',
        target_count=0,
        current_count=0,
        success_count=0,
        fail_count=0,
        current_item='',
        message=message,
        options_json=_json_dumps(options or {}),
        started_time=datetime.now(),
        updated_time=datetime.now(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _latest_active_task(db):
    """返回最新的运行中或最近完成的任务（不限类型）"""
    return db.query(DataFetchTask).filter(
        DataFetchTask.task_type.in_(['screening_update', 'fill_risk'])
    ).order_by(desc(DataFetchTask.started_time), desc(DataFetchTask.id)).first()


def _is_active_task(task):
    """检查任务是否仍在活跃运行（超过1小时未更新视为僵死）"""
    if not task or task.status != 'running':
        return False
    reference_time = task.updated_time or task.started_time
    if reference_time and datetime.now() - reference_time > timedelta(hours=1):
        task.status = 'failed'
        task.error_message = '任务超过 6 小时未更新，已标记为陈旧任务'
        task.message = task.error_message
        task.finished_time = datetime.now()
        task.updated_time = datetime.now()
        return False
    return True


def _task_to_update_status(task):
    if not task:
        return {
            'running': screening_update_status.get('running', False),
            'progress': screening_update_status.get('progress', 0),
            'total': screening_update_status.get('total', 0),
            'current_fund': screening_update_status.get('current_fund', ''),
            'success_count': screening_update_status.get('success_count', 0),
            'fail_count': screening_update_status.get('fail_count', 0),
            'message': screening_update_status.get('message', ''),
        }
    result = {
        'task_id': task.id,
        'running': task.status == 'running',
        'progress': task.current_count or 0,
        'total': task.target_count or 0,
        'current_fund': task.current_item or '',
        'success_count': task.success_count or 0,
        'fail_count': task.fail_count or 0,
        'message': task.message or '',
        'status': task.status,
        'started_time': task.started_time.isoformat() if task.started_time else None,
        'finished_time': task.finished_time.isoformat() if task.finished_time else None,
    }
    # 对于运行中的任务，用内存状态补充 DB 中可能滞后的字段
    if task.status == 'running':
        mem_mapping = [
            ('progress', 'progress'),
            ('total', 'total'),
            ('current_fund', 'current_fund'),
            ('success_count', 'success_count'),
            ('fail_count', 'fail_count'),
            ('message', 'message'),
        ]
        for mem_key, result_key in mem_mapping:
            mem_val = screening_update_status.get(mem_key)
            if mem_val is not None and mem_val != '':
                result[result_key] = mem_val
    return result


def _set_screening_progress(db=None, task_id=None, **fields):
    key_map = {
        'current_count': 'progress',
        'target_count': 'total',
        'current_item': 'current_fund',
    }
    for key, value in fields.items():
        memory_key = key_map.get(key, key)
        if memory_key in screening_update_status:
            screening_update_status[memory_key] = value

    if not db or not task_id:
        return

    task = db.query(DataFetchTask).filter(DataFetchTask.id == task_id).first()
    if not task:
        return

    for key, value in fields.items():
        if hasattr(task, key):
            setattr(task, key, value)
    if fields.get('status') in ('finished', 'failed', 'stopped'):
        task.finished_time = datetime.now()
    task.updated_time = datetime.now()
    db.commit()


screening_update_status = {
    'running': False,
    'progress': 0,
    'total': 0,
    'current_fund': '',
    'success_count': 0,
    'fail_count': 0,
    'start_time': None,
    'message': ''
}


def calculate_calmar_ratio(annual_return, max_drawdown):
    """计算卡玛比率"""
    if max_drawdown is None or max_drawdown == 0 or annual_return is None:
        return None
    return round(annual_return / max_drawdown, 2)


def check_4433_rule(rank_1y, rank_2y, rank_3y, rank_5y, rank_6m, rank_3m):
    """
    检查是否符合4433法则
    4433法则：
    - 近1年、2年、3年、5年排名在同类前1/4 (25%)
    - 近6个月、3个月排名在前1/3 (33.33%)
    注意：这里的排名应该是同类型基金中的排名百分位
    """
    # 长期排名：1年必须有，2年3年至少有一个
    if rank_1y is None or rank_1y > 25:
        return False
    
    # 检查2年和3年排名（如果有数据）
    long_term_available = [r for r in [rank_2y, rank_3y] if r is not None]
    if long_term_available:
        for rank in long_term_available:
            if rank > 25:
                return False
    
    # 如果有5年数据，也需要在前25%
    if rank_5y is not None and rank_5y > 25:
        return False
    
    # 短期排名：6个月和3个月都必须在前33.33%
    if rank_6m is None or rank_6m > 33.33:
        return False
    if rank_3m is None or rank_3m > 33.33:
        return False
    
    return True


# ==================== 基金筛选功能 ====================

def check_4433_rule(rank_1y, rank_2y, rank_3y, rank_5y, rank_6m, rank_3m):
    """
    检查是否符合4433法则
    4433法则：
    - 近1年、2年、3年排名在同类前1/4 (25%)
    - 近6个月、3个月排名在前1/3 (33.33%)
    """
    # 长期排名：1年必须有，2年3年至少有一个
    if rank_1y is None or rank_1y > 25:
        return False
    
    # 检查2年和3年排名（如果有数据）
    long_term_available = [r for r in [rank_2y, rank_3y] if r is not None]
    if long_term_available:
        for rank in long_term_available:
            if rank > 25:
                return False
    
    # 如果有5年数据，也需要在前25%
    if rank_5y is not None and rank_5y > 25:
        return False
    
    # 短期排名：6个月和3个月都必须在前33.33%
    if rank_6m is None or rank_6m > 33.33:
        return False
    if rank_3m is None or rank_3m > 33.33:
        return False
    
    return True


def calculate_same_type_rankings(db):
    """
    计算同类型基金的排名百分位
    基于 FundBasicInfo 中的收益数据计算排名，保存到 FundScreeningRank
    """
    # 获取所有有效的基金类型
    fund_types = db.query(FundBasicInfo.fund_type).filter(
        FundBasicInfo.fund_type.isnot(None),
        FundBasicInfo.fund_type != ''
    ).distinct().all()
    
    fund_types = [ft[0] for ft in fund_types]
    print(f"[同类排名] 发现 {len(fund_types)} 种基金类型")
    
    for fund_type in fund_types:
        # 获取该类型的所有基金（包含业绩数据）
        funds = db.query(FundBasicInfo).filter(
            FundBasicInfo.fund_type == fund_type,
            FundBasicInfo.performance_json.isnot(None)
        ).all()
        
        if len(funds) < 2:
            continue
        
        print(f"[同类排名] 处理 {fund_type}: {len(funds)} 只基金")
        
        # 解析业绩数据
        fund_performances = []
        for fund in funds:
            perf = _json_loads(fund.performance_json, {})
            fund_performances.append({
                'fund_code': fund.fund_code,
                'return_1m': perf.get('1_month_return'),
                'return_3m': perf.get('3_month_return'),
                'return_6m': perf.get('6_month_return'),
                'return_1y': perf.get('1_year_return'),
                'return_2y': perf.get('2_year_return'),
                'return_3y': perf.get('3_year_return'),
            })
        
        # 为每个时间段计算排名
        periods = [
            ('return_1m', 'rank_pct_1m'),
            ('return_3m', 'rank_pct_3m'),
            ('return_6m', 'rank_pct_6m'),
            ('return_1y', 'rank_pct_1y'),
            ('return_2y', 'rank_pct_2y'),
            ('return_3y', 'rank_pct_3y'),
        ]
        
        # 为每只基金创建或更新排名记录
        fund_ranks = {}  # fund_code -> rank data
        for fp in fund_performances:
            fund_ranks[fp['fund_code']] = {}
        
        for return_field, rank_field in periods:
            # 获取有该时段收益数据的基金
            # 重要：排除收益率为 0、"0.00"、None 的基金
            # 这些通常是成立时间不足导致的缺失数据，而非真实的0%收益
            def is_valid_return(val):
                if val is None:
                    return False
                try:
                    num_val = float(val)
                    # 真实0%收益极其罕见，0值通常表示数据缺失
                    # 允许一个很小的误差范围（如 -0.01% ~ 0.01%）视为可能的真实数据
                    if abs(num_val) < 0.01:
                        return False
                    return True
                except (ValueError, TypeError):
                    return False
            
            funds_with_data = [(fp['fund_code'], float(fp[return_field])) for fp in fund_performances 
                               if is_valid_return(fp[return_field])]
            
            if len(funds_with_data) < 2:
                continue
            
            # 按收益率降序排序（收益高排名靠前）
            funds_with_data.sort(key=lambda x: x[1], reverse=True)
            total = len(funds_with_data)
            
            # 计算每只基金的排名百分位
            for rank_idx, (fund_code, _) in enumerate(funds_with_data, 1):
                rank_pct = round((rank_idx / total) * 100, 2)
                fund_ranks[fund_code][rank_field] = rank_pct
        
        # 更新数据库
        for fund_code, ranks in fund_ranks.items():
            rank_record = db.query(FundScreeningRank).filter(
                FundScreeningRank.fund_code == fund_code
            ).first()
            
            if not rank_record:
                rank_record = FundScreeningRank(fund_code=fund_code)
                db.add(rank_record)
            
            for field, value in ranks.items():
                setattr(rank_record, field, value)
            
            # 计算4433法则
            pass_4433 = check_4433_rule(
                ranks.get('rank_pct_1y'),
                ranks.get('rank_pct_2y'),
                ranks.get('rank_pct_3y'),
                None,
                ranks.get('rank_pct_6m'),
                ranks.get('rank_pct_3m')
            )
            rank_record.pass_4433 = 1 if pass_4433 else 0
            rank_record.updated_time = datetime.now()
    
    db.commit()
    print("[同类排名] 同类型排名计算完成")


# 全局变量：批量更新状态
screening_update_status = {
    'running': False,
    'progress': 0,
    'total': 0,
    'current_fund': '',
    'success_count': 0,
    'fail_count': 0,
    'start_time': None,
    'message': ''
}
screening_stop_flag = False


def batch_update_fund_data(fund_types=None, limit=None, mode='sync_nav', precise_limit=500, task_id=None, industry_limit=None, tasks=None):
    """Run the ifund-style screening refresh: local snapshot first, optional candidate NAV sync."""
    global screening_update_status, screening_stop_flag

    tasks = tasks or {}
    update_basic = bool(tasks.get('basic', True))
    calculate_rankings_task = bool(tasks.get('rankings', update_basic))
    calculate_risk_task = bool(tasks.get('risk', mode != 'sync_only'))
    refresh_industry_task = bool(tasks.get('industry', True))
    rebuild_industry_performance_task = bool(tasks.get('industry_performance', refresh_industry_task))

    db = SessionLocal()
    t0 = time.time()
    print(f"[筛查更新] ========== 开始批量更新 ({mode}) ==========", flush=True)
    try:
        if not task_id:
            task = _create_data_fetch_task(
                db,
                'screening_update',
                {
                    'fund_types': fund_types or [],
                    'limit': limit,
                    'mode': mode,
                    'nav_limit': precise_limit,
                    'industry_limit': industry_limit,
                    'tasks': tasks,
                },
                message='获取批量排行...',
            )
            task_id = task.id

        if mode == 'sync_only':
            mode = 'sync_only'
        else:
            mode = 'sync_nav'
        if precise_limit is not None:
            try:
                precise_limit = max(1, int(precise_limit))
            except (TypeError, ValueError):
                precise_limit = 500

        screening_stop_flag = False
        screening_update_status.update({
            'running': True,
            'progress': 0,
            'total': 0,
            'current_fund': '',
            'success_count': 0,
            'fail_count': 0,
            'start_time': datetime.now(),
            'message': '获取批量排行...',
        })
        _set_screening_progress(db, task_id, status='running', message='获取批量排行...')

        if update_basic:
            fund_list = _fetch_screening_snapshot_items(limit=limit, db=db, task_id=task_id)
        else:
            query = db.query(FundBasicInfo)
            if limit:
                try:
                    query = query.limit(max(1, int(limit)))
                except (TypeError, ValueError):
                    pass
            fund_list = [
                {'code': item.fund_code, 'name': item.fund_name, 'type': item.fund_type}
                for item in query.all()
            ]
        t_lookup_start = time.time()
        type_lookup = _fund_type_lookup()
        print(f"[筛查更新] 类型查找表构建: {len(type_lookup)}条 ({time.time()-t_lookup_start:.1f}s)", flush=True)

        if fund_types:
            before_filter = len(fund_list)
            fund_list = [
                item for item in fund_list
                if _matches_fund_type(
                    item.get('type') or type_lookup.get(_normalize_fund_code(item.get('code')), {}).get('type'),
                    fund_types,
                )
            ]
            print(f"[筛查更新] 类型筛选: {before_filter} → {len(fund_list)}只 (筛选条件: {fund_types})", flush=True)

        success_count = 0
        fail_count = 0
        total_to_save = len(fund_list)
        if update_basic:
            print(f"[筛查更新] 开始写入基础数据: {total_to_save}只...", flush=True)
            _set_screening_progress(
                db,
                task_id,
                message='写入基础数据...',
                target_count=total_to_save,
                current_count=0,
                success_count=0,
                fail_count=0,
                current_item='',
            )

            for index, fund in enumerate(fund_list, 1):
                if screening_stop_flag:
                    _set_screening_progress(
                        db,
                        task_id,
                        status='stopped',
                        message=f"已手动停止。成功: {success_count}, 失败: {fail_count}",
                        current_count=index - 1,
                        success_count=success_count,
                        fail_count=fail_count,
                    )
                    return {'success': False, 'stopped': True}

                fund_code = _normalize_fund_code(fund.get('code', ''))
                if _save_screening_snapshot_item(db, fund, type_lookup):
                    success_count += 1
                else:
                    fail_count += 1

                screening_update_status.update({
                    'progress': index,
                    'current_fund': f"{fund_code} - {fund.get('name', '')}",
                    'success_count': success_count,
                    'fail_count': fail_count,
                })

                db.commit()
                _set_screening_progress(
                    db,
                    task_id,
                    current_count=index,
                    current_item=f"{fund_code} - {fund.get('name', '')}",
                    success_count=success_count,
                    fail_count=fail_count,
                )

                if index % 500 == 0:
                    print(f"[筛查更新] 写入进度: {index}/{total_to_save} (成功{success_count}, 失败{fail_count})", flush=True)

            db.commit()
            print(f"[筛查更新] 基础数据写入完成: 成功{success_count}, 失败{fail_count}, 耗时{time.time()-t0:.1f}s", flush=True)
        else:
            _set_screening_progress(db, task_id, message='跳过基础数据更新...', target_count=total_to_save, current_count=0)

        if not screening_stop_flag and calculate_rankings_task:
            print(f"[筛查更新] 开始计算同类排名...", flush=True)
            _set_screening_progress(db, task_id, message='计算同类排名...', current_item='')
            calculate_same_type_rankings(db)
            print(f"[筛查更新] 同类排名计算完成", flush=True)

        if not screening_stop_flag and calculate_risk_task:
            candidates = _select_nav_candidates(db, precise_limit)
            nav_success = 0
            nav_fail = 0
            _set_screening_progress(
                db,
                task_id,
                message='补齐候选净值并计算风险指标...',
                target_count=len(candidates),
                current_count=0,
                success_count=0,
                fail_count=0,
                current_item='',
            )

            for index, fund in enumerate(candidates, 1):
                if screening_stop_flag:
                    _set_screening_progress(
                        db,
                        task_id,
                        status='stopped',
                        message=f"已手动停止。净值同步成功: {nav_success}, 失败: {nav_fail}",
                        current_count=index - 1,
                        success_count=nav_success,
                        fail_count=nav_fail,
                    )
                    return {'success': False, 'stopped': True}

                current_item = f"{fund['code']} - {fund.get('name', '')}"
                _set_screening_progress(
                    db,
                    task_id,
                    current_count=index,
                    current_item=current_item,
                )
                if update_single_fund_risk_metrics(fund['code'], db):
                    nav_success += 1
                else:
                    nav_fail += 1
                _set_screening_progress(
                    db,
                    task_id,
                    success_count=nav_success,
                    fail_count=nav_fail,
                )

            success_count = nav_success
            fail_count = nav_fail

        if not screening_stop_flag and refresh_industry_task:
            industry_codes = [
                _normalize_fund_code(item.get('code'))
                for item in fund_list
                if _normalize_fund_code(item.get('code'))
            ]
            industry_result = batch_refresh_fund_industry_tags(
                db,
                fund_codes=industry_codes,
                task_id=task_id,
                force=False,
                limit=industry_limit,
                build_full_dictionary=True,
                allow_missing_stock_network=True,
            )
            print(
                f"[screening update] industry tags refreshed: "
                f"{industry_result['success_count']}/{industry_result['total']} "
                f"(fail {industry_result['fail_count']})",
                flush=True,
            )

        if not screening_stop_flag and rebuild_industry_performance_task:
            _set_screening_progress(db, task_id, message='汇总行业表现...', current_item='')
            rebuild_industry_performance_stats(db)
            db.commit()

        message = f"完成。模式: {mode}, 成功: {success_count}, 失败: {fail_count}"
        _set_screening_progress(
            db,
            task_id,
            status='finished',
            message=message,
            success_count=success_count,
            fail_count=fail_count,
            current_item='',
        )
        screening_update_status['running'] = False
        return {
            'success': True,
            'total': screening_update_status['total'],
            'success_count': success_count,
            'fail_count': fail_count,
        }
    except Exception as exc:
        db.rollback()
        message = f"更新失败: {exc}"
        screening_update_status['message'] = message
        screening_update_status['running'] = False
        _set_screening_progress(db, task_id, status='failed', message=message, error_message=str(exc))
        return {'success': False, 'error': str(exc)}
    finally:
        screening_update_status['running'] = False
        db.close()


def batch_fill_risk_metrics(db=None, task_id=None):
    """批量补全缺失的风险指标（回撤/波动率/夏普/卡玛）。
    处理那些已有足够净值历史(>=30条)但尚未计算风险指标的基金。
    作为后台任务运行，支持停止信号。"""
    global screening_update_status, screening_stop_flag

    own_db = None
    if db is None:
        own_db = SessionLocal()
        db = own_db

    try:
        # 查找所有有基本信息但无风险指标的基金（不限净值条数，后续会先同步净值再计算）
        all_basic_codes = set()
        for row in db.query(FundBasicInfo.fund_code).all():
            all_basic_codes.add(row[0])

        risk_codes = set()
        for row in db.query(FundRiskMetrics.fund_code).filter(
            FundRiskMetrics.sharpe_ratio_1y.isnot(None)
        ).all():
            risk_codes.add(row[0])

        missing_risk_codes = all_basic_codes - risk_codes
        missing_industry_codes = set(_fund_codes_needing_industry_refresh(db, all_basic_codes, force=False))
        missing_codes = sorted(missing_risk_codes | missing_industry_codes)
        industry_tag_count = db.query(FundIndustryTag.fund_code).count()

        print(f"[补充风险] 待处理: {len(missing_codes)}只 (缺风险:{len(missing_risk_codes)}, 缺行业:{len(missing_industry_codes)}, 总计:{len(all_basic_codes)}, 有风险:{len(risk_codes)}, 行业标签:{industry_tag_count})", flush=True)

        if not missing_codes:
            print("[补充风险] 无需补充，所有基金已有风险指标", flush=True)
            rebuild_industry_performance_stats(db)
            db.commit()
            screening_update_status['running'] = False
            return {'success': True, 'total': 0, 'message': '所有基金已有风险指标'}

        screening_update_status.update({
            'running': True,
            'progress': 0,
            'total': len(missing_codes),
            'current_fund': '',
            'success_count': 0,
            'fail_count': 0,
            'message': '补充风险指标...',
        })
        _set_screening_progress(
            db, task_id,
            status='running',
            message='补充风险指标...',
            target_count=len(missing_codes),
            current_count=0,
            success_count=0,
            fail_count=0,
        )

        success = 0
        fail = 0
        for idx, code in enumerate(missing_codes, 1):
            if screening_stop_flag:
                _set_screening_progress(db, task_id, status='stopped',
                    message=f"已停止。成功{success}, 失败{fail}")
                return {'success': False, 'stopped': True}

            current_item = f"{code}"
            screening_update_status.update({
                'progress': idx,
                'current_fund': current_item,
                'success_count': success,
                'fail_count': fail,
            })

            if code in missing_risk_codes:
                ok = update_single_fund_risk_metrics(code, db)
            else:
                ok = _refresh_fund_industry_tag(db, code, fetch_holdings_if_missing=True) is not None

            if ok:
                success += 1
            else:
                fail += 1

            db.commit()
            _set_screening_progress(db, task_id,
                current_count=idx, current_item=current_item,
                success_count=success, fail_count=fail)

            if idx % 100 == 0:
                print(f"[补充风险] 进度: {idx}/{len(missing_codes)} (成功{success}, 失败{fail})", flush=True)

        rebuild_industry_performance_stats(db)
        db.commit()

        print(f"[补充风险] 完成: 成功{success}, 失败{fail}", flush=True)
        _set_screening_progress(db, task_id, status='finished',
            message=f"补充完成。成功{success}, 失败{fail}",
            success_count=success, fail_count=fail)
        screening_update_status['running'] = False
        return {'success': True, 'success_count': success, 'fail_count': fail}

    except Exception as e:
        print(f"[补充风险] 失败: {e}", flush=True)
        screening_update_status['running'] = False
        _set_screening_progress(db, task_id, status='failed', message=str(e))
        return {'success': False, 'error': str(e)}
    finally:
        if own_db:
            own_db.close()


@app.route('/api/screening/fill-risk', methods=['POST'])
def start_fill_risk():
    """启动后台补全风险指标任务"""
    global screening_update_status
    if screening_update_status.get('running'):
        return jsonify({'error': '已有更新任务在进行中'}), 409

    def _run():
        db = SessionLocal()
        try:
            task = _create_data_fetch_task(db, 'fill_risk', message='补充风险指标...')
            batch_fill_risk_metrics(db=db, task_id=task.id)
        except Exception as e:
            print(f"[补充风险] 线程异常: {e}", flush=True)
            screening_update_status['running'] = False
        finally:
            db.close()

    import threading
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({'message': '补充风险指标任务已启动'})



@app.route('/api/screening/status', methods=['GET'])
def get_screening_status():
    """获取筛选数据库状态"""
    db = get_db()
    
    # 统计各表数量
    basic_count = db.query(FundBasicInfo).count()
    risk_count = db.query(FundRiskMetrics).filter(
        FundRiskMetrics.sharpe_ratio_1y.isnot(None)
    ).count()
    rank_count = db.query(FundScreeningRank).count()
    industry_tag_count = db.query(FundIndustryTag).count()
    nav_history_count = db.query(FundNavHistory).count()
    pass_4433_count = db.query(FundScreeningRank).filter(
        FundScreeningRank.pass_4433 == 1
    ).count()
    
    # 获取最新更新时间
    latest = db.query(FundBasicInfo).order_by(
        desc(FundBasicInfo.updated_time)
    ).first()
    latest_update = latest.updated_time.isoformat() if latest and latest.updated_time else None
    
    # 获取各类型数量
    type_counts = {}
    types = db.query(FundBasicInfo.fund_type, func.count(FundBasicInfo.fund_code)).group_by(
        FundBasicInfo.fund_type
    ).all()
    for t, c in types:
        if t:
            type_counts[t] = c
    
    latest_task = _latest_active_task(db)
    if latest_task and latest_task.status == 'running' and not _is_active_task(latest_task):
        db.commit()

    return jsonify({
        'basic_count': basic_count,
        'risk_metrics_count': risk_count,
        'ranking_count': rank_count,
        'industry_tag_count': industry_tag_count,
        'nav_history_count': nav_history_count,
        'pass_4433_count': pass_4433_count,
        'latest_update': latest_update,
        'type_counts': type_counts,
        'update_status': _task_to_update_status(latest_task)
    })


@app.route('/api/screening/progress', methods=['GET'])
def get_screening_progress():
    """获取更新进度 — 以DB DataFetchTask 为准，内存仅作补充"""
    try:
        db = get_db()
        task = _latest_active_task(db)
    except Exception:
        task = None

    # 先从内存读取
    result = {
        'running': screening_update_status.get('running', False),
        'progress': screening_update_status.get('progress', 0),
        'total': screening_update_status.get('total', 0),
        'current_fund': screening_update_status.get('current_fund', ''),
        'success_count': screening_update_status.get('success_count', 0),
        'fail_count': screening_update_status.get('fail_count', 0),
        'message': screening_update_status.get('message', ''),
    }

    # DB中有运行中任务时，以DB为准覆盖（后台线程每只基金commit一次，DB数据最可靠）
    if task:
        if task.status == 'running':
            result['running'] = True
            # DB值优先（只要DB有数据就用DB的）
            if task.target_count:
                result['total'] = task.target_count
            if task.current_count is not None:
                result['progress'] = task.current_count
            if task.current_item:
                result['current_fund'] = task.current_item
            if task.success_count is not None:
                result['success_count'] = task.success_count
            if task.fail_count is not None:
                result['fail_count'] = task.fail_count
            if task.message:
                result['message'] = task.message
            # 同步内存状态（修复线程异常导致running=false的问题）
            if not screening_update_status.get('running'):
                screening_update_status['running'] = True
        elif task.status in ('finished', 'stopped', 'failed'):
            result['running'] = False
            result['message'] = task.message or result['message']

    return jsonify(result)


@app.route('/api/screening/update', methods=['POST'])
def start_screening_update():
    """启动基金数据批量更新"""
    data = request.get_json() or {}
    fund_types = data.get('fund_types') or []
    limit = data.get('limit')
    mode = data.get('mode') or 'sync_nav'
    precise_limit = data.get('precise_limit', 500)
    industry_limit = data.get('industry_limit')
    tasks = data.get('tasks') or {}
    db = get_db()
    latest_task = _latest_active_task(db)
    
    active_task_running = _is_active_task(latest_task)
    if latest_task and latest_task.status != 'running':
        db.commit()

    if screening_update_status['running'] or active_task_running:
        return jsonify({
            'error': '更新任务正在进行中',
            'status': _task_to_update_status(latest_task)
        }), 409
    
    # 在后台线程执行更新
    task = _create_data_fetch_task(
        db,
        'screening_update',
        {
            'fund_types': fund_types,
            'limit': limit,
            'mode': mode,
            'nav_limit': precise_limit,
            'industry_limit': industry_limit,
            'tasks': tasks,
        },
        message='更新任务已启动',
    )

    thread = threading.Thread(
        target=batch_update_fund_data, 
        args=(fund_types, limit, mode, precise_limit, task.id, industry_limit, tasks)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'message': '更新任务已启动',
        'fund_types': fund_types,
        'limit': limit,
        'mode': mode,
        'nav_limit': precise_limit,
        'industry_limit': industry_limit,
        'tasks': tasks,
        'task_id': task.id
    })


@app.route('/api/screening/stop', methods=['POST'])
def stop_screening_update():
    """停止基金数据更新"""
    global screening_stop_flag, screening_update_status
    screening_stop_flag = True
    # 同时强制重置状态，防止卡死
    screening_update_status['running'] = False
    screening_update_status['message'] = '已手动停止'
    # 更新DB中的最新任务状态
    db = get_db()
    try:
        task = _latest_active_task(db)
        if task and task.status == 'running':
            task.status = 'stopped'
            task.message = '已手动停止'
            task.finished_time = datetime.now()
            task.updated_time = datetime.now()
            db.commit()
    except Exception:
        pass
    return jsonify({'message': '已停止并重置状态'})


@app.route('/api/screening/recalculate-rankings', methods=['POST'])
def recalculate_rankings():
    """重新计算同类型排名和4433法则标记"""
    db = get_db()
    try:
        calculate_same_type_rankings(db)
        
        # 统计结果
        stats = db.query(
            FundBasicInfo.fund_type,
            func.count(FundScreeningRank.fund_code).label('total'),
            func.sum(FundScreeningRank.pass_4433).label('pass_count')
        ).join(
            FundScreeningRank, FundBasicInfo.fund_code == FundScreeningRank.fund_code
        ).group_by(FundBasicInfo.fund_type).all()
        
        type_stats = {}
        for fund_type, total, pass_count in stats:
            if fund_type:
                type_stats[fund_type] = {
                    'total': total,
                    'pass_4433': pass_count or 0,
                    'pass_rate': round((pass_count or 0) / total * 100, 2) if total > 0 else 0
                }
        
        return jsonify({
            'success': True,
            'message': '同类型排名计算完成',
            'stats': type_stats
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/screening/available-types', methods=['POST'])
def get_available_fund_types():
    """获取符合当前筛选条件的所有基金类型（用于快速筛选）"""
    data = request.get_json() or {}
    strategy = data.get('strategy')
    filters = data.get('filters', {})
    
    db = get_db()
    
    # 基础查询
    query = db.query(FundBasicInfo.fund_type).distinct()
    
    # 关联排名表（用于4433筛选）
    if strategy == '4433':
        query = query.join(
            FundScreeningRank, FundBasicInfo.fund_code == FundScreeningRank.fund_code
        ).filter(FundScreeningRank.pass_4433 == 1)
    
    # 关联风险指标表（用于其他策略）
    if strategy in ['high_sharpe', 'low_volatility', 'anti_fragile']:
        query = query.join(
            FundRiskMetrics, FundBasicInfo.fund_code == FundRiskMetrics.fund_code
        )
        
        if strategy == 'high_sharpe':
            query = query.filter(
                FundRiskMetrics.sharpe_ratio_1y > 2,
                FundRiskMetrics.volatility_1y < 25
            )
        elif strategy == 'low_volatility':
            query = query.filter(
                FundRiskMetrics.volatility_1y < 15,
                FundRiskMetrics.max_drawdown_1y < 15
            )
        elif strategy == 'anti_fragile':
            query = query.filter(
                FundRiskMetrics.max_drawdown_1y < 20,
                FundRiskMetrics.annual_return_1y > 0
            )
    
    # 应用自定义类型筛选
    if filters.get('fund_types'):
        type_conditions = []
        for t in filters['fund_types']:
            type_conditions.append(FundBasicInfo.fund_type.like(f'%{t}%'))
        if type_conditions:
            query = query.filter(or_(*type_conditions))
    
    # 获取所有符合条件的类型
    result = query.filter(FundBasicInfo.fund_type != None).all()
    types = sorted([r[0] for r in result if r[0]])
    
    return jsonify({'types': types})


@app.route('/api/screening/industry-tags', methods=['GET'])
def get_screening_industry_tags():
    """Return available fund industry tags inferred from cached top holdings."""
    db = get_db()
    if db.query(FundIndustryTag).count() == 0:
        fund_codes = [
            row[0]
            for row in db.query(FundPortfolio.fund_code).all()
            if row[0]
        ]
        _screening_industry_context(db, fund_codes)
        rebuild_industry_performance_stats(db)
        try:
            db.commit()
        except Exception:
            db.rollback()

    stats = {}
    rows = db.query(FundIndustryTag.industry_tag, func.count(FundIndustryTag.fund_code)).group_by(
        FundIndustryTag.industry_tag
    ).all()
    for name, count in rows:
        name = name or '混合型'
        item = stats.setdefault(name, {
            'name': name,
            'count': 0,
        })
        item['count'] += count or 0

    items = sorted(stats.values(), key=lambda item: item['count'], reverse=True)
    return jsonify({
        'data': items,
        'total': len(items),
    })


@app.route('/api/screening/rebuild-industry-tags', methods=['POST'])
def rebuild_screening_industry_tags():
    """Rebuild persisted fund industry tags from cached holdings and stock industries."""
    db = get_db()
    data = request.get_json() or {}
    force = bool(data.get('force'))
    limit = data.get('limit')
    fund_codes = [
        row[0]
        for row in db.query(FundBasicInfo.fund_code).all()
        if row[0]
    ]
    try:
        result = batch_refresh_fund_industry_tags(
            db,
            fund_codes=fund_codes,
            force=force,
            limit=limit,
            build_full_dictionary=True,
            allow_missing_stock_network=True,
        )
        rebuild_industry_performance_stats(db)
    except Exception as exc:
        db.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 500
    db.commit()

    return jsonify({
        'success': True,
        'total': len(fund_codes),
        'processed': result,
    })


@app.route('/api/screening/stock-industry/status', methods=['GET'])
def get_stock_industry_status():
    db = get_db()
    total = db.query(StockIndustry).count()
    with_industry = db.query(StockIndustry).filter(
        StockIndustry.industry.isnot(None),
        StockIndustry.industry != '',
    ).count()
    portfolio_stock_total = len(_collect_stock_codes_from_portfolios(db))
    latest = db.query(StockIndustry).order_by(desc(StockIndustry.updated_time)).first()
    return jsonify({
        'total': total,
        'with_industry': with_industry,
        'missing_industry': max(total - with_industry, 0),
        'portfolio_stock_total': portfolio_stock_total,
        'portfolio_stock_unmapped': max(portfolio_stock_total - with_industry, 0),
        'latest_update': latest.updated_time.isoformat() if latest and latest.updated_time else None,
    })


@app.route('/api/screening/stock-industry/warmup', methods=['POST'])
def warmup_stock_industry():
    db = get_db()
    data = request.get_json() or {}
    force = bool(data.get('force'))
    limit = data.get('limit')
    fund_codes = data.get('fund_codes')
    if isinstance(fund_codes, str):
        fund_codes = [code.strip() for code in fund_codes.split(',') if code.strip()]
    try:
        result = warm_stock_industry_dictionary(
            db,
            fund_codes=fund_codes if isinstance(fund_codes, list) else None,
            force=force,
            limit=limit,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 500
    return jsonify({
        'success': True,
        'data': result,
    })


@app.route('/api/screening/stock-industry/build-akshare', methods=['POST'])
def build_stock_industry_from_akshare():
    db = get_db()
    data = request.get_json() or {}
    try:
        result = build_stock_industry_dictionary_from_akshare(
            db,
            force=bool(data.get('force')),
            board_limit=data.get('board_limit'),
            stock_limit=data.get('stock_limit'),
        )
    except Exception as exc:
        db.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 500
    return jsonify({
        'success': True,
        'data': result,
    })


@app.route('/api/screening/query', methods=['POST'])
def query_screening_funds():
    """
    高级基金筛选查询（使用 JOIN 关联查询）
    数据来源：FundBasicInfo + FundRiskMetrics + FundScreeningRank
    """
    data = request.get_json() or {}
    
    # 筛选条件
    filters = data.get('filters', {})
    
    # 排序
    sort_by = data.get('sort_by', 'sharpe_ratio_1y')
    sort_order = data.get('sort_order', 'desc')
    
    # 分页
    page = data.get('page', 1)
    page_size = data.get('page_size', 20)
    
    # 预设策略
    strategy = data.get('strategy')
    
    db = get_db()
    
    # 基础查询：JOIN 三个表
    query = db.query(
        FundBasicInfo,
        FundRiskMetrics,
        FundScreeningRank,
        FundIndustryTag
    ).outerjoin(
        FundRiskMetrics, FundBasicInfo.fund_code == FundRiskMetrics.fund_code
    ).outerjoin(
        FundScreeningRank, FundBasicInfo.fund_code == FundScreeningRank.fund_code
    ).outerjoin(
        FundIndustryTag, FundBasicInfo.fund_code == FundIndustryTag.fund_code
    )
    
    # 应用预设策略
    if strategy == '4433':
        query = query.filter(FundScreeningRank.pass_4433 == 1)
    
    elif strategy == 'high_sharpe':
        query = query.filter(
            FundRiskMetrics.sharpe_ratio_1y > 2,
            FundRiskMetrics.volatility_1y < 25
        )
    
    elif strategy == 'low_volatility':
        query = query.filter(
            FundRiskMetrics.volatility_1y < 15,
            FundRiskMetrics.max_drawdown_1y < 15
        )
    
    elif strategy == 'anti_fragile':
        query = query.filter(
            FundRiskMetrics.max_drawdown_1y < 20,
            FundRiskMetrics.annual_return_1y > 0
        )
    
    # 应用自定义筛选条件
    if filters.get('fund_types'):
        type_conditions = []
        for t in filters['fund_types']:
            type_conditions.append(FundBasicInfo.fund_type.like(f'%{t}%'))
        if type_conditions:
            query = query.filter(or_(*type_conditions))

    # 关键词搜索（基金代码/名称）
    if filters.get('keyword'):
        kw = f'%{filters["keyword"]}%'
        query = query.filter(or_(
            FundBasicInfo.fund_code.like(kw),
            FundBasicInfo.fund_name.like(kw)
        ))

    # 近1年收益率范围
    if filters.get('return_1y_min') is not None:
        query = query.filter(FundBasicInfo.return_1y >= filters['return_1y_min'])
    if filters.get('return_1y_max') is not None:
        query = query.filter(FundBasicInfo.return_1y <= filters['return_1y_max'])

    # 快速类型筛选（精确匹配）
    if filters.get('quick_fund_type'):
        query = query.filter(FundBasicInfo.fund_type == filters['quick_fund_type'])
    
    # 风险指标筛选
    if filters.get('sharpe_min') is not None:
        query = query.filter(FundRiskMetrics.sharpe_ratio_1y >= filters['sharpe_min'])
    
    if filters.get('volatility_max') is not None:
        query = query.filter(FundRiskMetrics.volatility_1y <= filters['volatility_max'])
    
    if filters.get('max_drawdown_max') is not None:
        query = query.filter(FundRiskMetrics.max_drawdown_1y <= filters['max_drawdown_max'])
    
    if filters.get('calmar_min') is not None:
        query = query.filter(FundRiskMetrics.calmar_ratio_1y >= filters['calmar_min'])
    
    # 排名筛选
    if filters.get('rank_1y_max') is not None:
        query = query.filter(FundScreeningRank.rank_pct_1y <= filters['rank_1y_max'])
    if filters.get('rank_3m_max') is not None:
        query = query.filter(FundScreeningRank.rank_pct_3m <= filters['rank_3m_max'])
    
    # 排序（根据排序字段选择对应的表）
    sort_map = {
        'sharpe_ratio_1y': FundRiskMetrics.sharpe_ratio_1y,
        'sharpe_ratio_3y': FundRiskMetrics.sharpe_ratio_3y,
        'return_1y': FundBasicInfo.return_1y,
        'volatility_1y': FundRiskMetrics.volatility_1y,
        'max_drawdown_1y': FundRiskMetrics.max_drawdown_1y,
        'calmar_ratio_1y': FundRiskMetrics.calmar_ratio_1y,
        'rank_pct_1y': FundScreeningRank.rank_pct_1y,
        'rank_pct_3m': FundScreeningRank.rank_pct_3m,
        'fund_code': FundBasicInfo.fund_code,
        'fund_name': FundBasicInfo.fund_name,
        'fund_type': FundBasicInfo.fund_type,
        'updated_time': FundBasicInfo.updated_time,
    }
    
    sort_column = sort_map.get(sort_by, FundRiskMetrics.sharpe_ratio_1y)
    if sort_order == 'desc':
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(asc(sort_column))
    
    # 计算总数
    industry_filters = [
        str(item).strip()
        for item in (filters.get('industry_tags') or [])
        if str(item).strip()
    ]

    if industry_filters:
        query = query.filter(FundIndustryTag.industry_tag.in_(industry_filters))

    total_count = query.count()
    offset = (page - 1) * page_size
    results = query.offset(offset).limit(page_size).all()
    
    # 构建返回数据
    fund_list = []
    
    # 脏数据自动清理标记（不立即清理，而是返回NULL，防止展示离谱数据）
    # 如果用户需要修复，可以点击“更新数据”
    for basic, risk, rank, industry_tag in results:
        # 解析业绩数据
        perf = _json_loads(basic.performance_json, {}) if basic else {}
        
        # 脏数据检测：如果波动率 > 1000%，视为无效数据
        is_dirty_risk = risk and risk.volatility_1y and risk.volatility_1y > 1000
        
        fund_list.append({
            'fund_code': basic.fund_code if basic else None,
            'fund_name': basic.fund_name if basic else None,
            'fund_type': basic.fund_type if basic else None,
            # 业绩数据（来自 FundBasicInfo.performance_json）
            'return_1m': perf.get('1_month_return'),
            'return_3m': perf.get('3_month_return'),
            'return_6m': perf.get('6_month_return'),
            # 如果近1年收益率为 "0.00" 且实际上可能是空数据，则转为 None 或 "--"
            'return_1y': perf.get('1_year_return') if perf.get('1_year_return') not in ['0.00', 0.0, 0, ''] else None,
            'return_3y': perf.get('3_year_return') if perf.get('3_year_return') not in ['0.00', 0.0, 0, ''] else None,
            # 风险指标（来自 FundRiskMetrics），如果脏数据则隐藏
            'max_drawdown_1y': (risk.max_drawdown_1y if risk else None) if not is_dirty_risk else None,
            'max_drawdown_3y': (risk.max_drawdown_3y if risk else None) if not is_dirty_risk else None,
            'volatility_1y': (risk.volatility_1y if risk else None) if not is_dirty_risk else None,
            'volatility_3y': (risk.volatility_3y if risk else None) if not is_dirty_risk else None,
            'sharpe_ratio_1y': (risk.sharpe_ratio_1y if risk else None) if not is_dirty_risk else None,
            'sharpe_ratio_3y': (risk.sharpe_ratio_3y if risk else None) if not is_dirty_risk else None,
            'calmar_ratio_1y': (risk.calmar_ratio_1y if risk else None) if not is_dirty_risk else None,
            'calmar_ratio_3y': (risk.calmar_ratio_3y if risk else None) if not is_dirty_risk else None,
            # 排名数据（来自 FundScreeningRank）
            'rank_pct_1m': rank.rank_pct_1m if rank else None,
            'rank_pct_3m': rank.rank_pct_3m if rank else None,
            'rank_pct_6m': rank.rank_pct_6m if rank else None,
            'rank_pct_1y': rank.rank_pct_1y if rank else None,
            'pass_4433': (rank.pass_4433 == 1) if rank else False,
            'industry_tag': ({
                'name': industry_tag.industry_tag,
                'ratio': industry_tag.industry_ratio,
                'count': industry_tag.industry_count,
                'basis': industry_tag.basis,
                'source': industry_tag.source,
            } if industry_tag else None),
            'industry_tag_name': industry_tag.industry_tag if industry_tag else None,
            # 时间戳
            'updated_time': basic.updated_time.isoformat() if basic and basic.updated_time else None
        })
    
    return jsonify({
        'total': total_count,
        'page': page,
        'page_size': page_size,
        'total_pages': math.ceil(total_count / page_size) if total_count > 0 else 0,
        'data': fund_list
    })


@app.route('/api/screening/strategies', methods=['GET'])
def get_screening_strategies():
    """获取预设筛选策略列表"""
    strategies = [
        {
            'id': '4433',
            'name': '4433法则',
            'description': '同类型基金中：近1/2/3年排名前25%，近3/6个月排名前33%',
            'tags': ['经典策略', '同类排名', '业绩稳定']
        },
        {
            'id': 'high_sharpe',
            'name': '高夏普比率',
            'description': '夏普比率 > 2，单位风险收益最优',
            'tags': ['风险调整', '收益优化']
        },
        {
            'id': 'low_volatility',
            'name': '低波动策略',
            'description': '波动率 < 15%，最大回撤 < 15%，稳健型',
            'tags': ['低风险', '稳健']
        },
        {
            'id': 'anti_fragile',
            'name': '反脆弱策略',
            'description': '在极端行情中表现稳健的基金',
            'tags': ['抗跌', '极端行情']
        },
        {
            'id': 'high_calmar',
            'name': '高卡玛比率',
            'description': '年化收益/最大回撤比值高，性价比最优',
            'tags': ['风险调整', '性价比']
        }
    ]
    return jsonify({'strategies': strategies})


@app.route('/api/screening/fund/<fund_code>', methods=['GET'])
def get_screening_fund_detail(fund_code):
    fund_code = _normalize_fund_code(fund_code)
    """获取单只基金的筛选详情数据（JOIN 查询）"""
    db = get_db()
    
    # JOIN 查询
    result = db.query(
        FundBasicInfo,
        FundRiskMetrics,
        FundScreeningRank,
        FundExtraData
    ).outerjoin(
        FundRiskMetrics, FundBasicInfo.fund_code == FundRiskMetrics.fund_code
    ).outerjoin(
        FundScreeningRank, FundBasicInfo.fund_code == FundScreeningRank.fund_code
    ).outerjoin(
        FundExtraData, FundBasicInfo.fund_code == FundExtraData.fund_code
    ).filter(
        FundBasicInfo.fund_code == fund_code
    ).first()
    
    if not result:
        return jsonify({'error': 'Fund not found'}), 404
    
    basic, risk, rank, extra = result
    perf = _json_loads(basic.performance_json, {})
    
    return jsonify({
        'fund_code': basic.fund_code,
        'fund_name': basic.fund_name,
        'fund_type': basic.fund_type,
        'returns': {
            '1m': perf.get('1_month_return'),
            '3m': perf.get('3_month_return'),
            '6m': perf.get('6_month_return'),
            '1y': perf.get('1_year_return'),
            '2y': perf.get('2_year_return'),
            '3y': perf.get('3_year_return'),
        },
        'risk_metrics': {
            'max_drawdown_1y': risk.max_drawdown_1y if risk else None,
            'max_drawdown_3y': risk.max_drawdown_3y if risk else None,
            'volatility_1y': risk.volatility_1y if risk else None,
            'volatility_3y': risk.volatility_3y if risk else None,
            'sharpe_ratio_1y': risk.sharpe_ratio_1y if risk else None,
            'sharpe_ratio_3y': risk.sharpe_ratio_3y if risk else None,
            'calmar_ratio_1y': risk.calmar_ratio_1y if risk else None,
            'calmar_ratio_3y': risk.calmar_ratio_3y if risk else None
        },
        'rankings': {
            '1m': rank.rank_pct_1m if rank else None,
            '3m': rank.rank_pct_3m if rank else None,
            '6m': rank.rank_pct_6m if rank else None,
            '1y': rank.rank_pct_1y if rank else None,
            '2y': rank.rank_pct_2y if rank else None,
            '3y': rank.rank_pct_3y if rank else None,
        },
        'pass_4433': (rank.pass_4433 == 1) if rank else False,
        'updated_time': basic.updated_time.isoformat() if basic.updated_time else None
    })


@app.route('/api/screening/update-single/<fund_code>', methods=['POST'])
def update_single_fund(fund_code):
    fund_code = _normalize_fund_code(fund_code)
    """更新单只基金数据"""
    db = get_db()
    
    success = update_single_fund_risk_metrics(fund_code, db)
    
    if success:
        return jsonify({'message': f'Fund {fund_code} updated successfully'})
    else:
        return jsonify({'error': f'Failed to update fund {fund_code}'}), 500


@app.route('/api/fund/<fund_code>/data-versions', methods=['GET'])
def get_fund_data_versions(fund_code):
    fund_code = _normalize_fund_code(fund_code)
    """
    获取单只基金各数据源的版本时间
    用于前端检测数据一致性
    """
    db = get_db()
    
    basic = db.query(FundBasicInfo).filter(FundBasicInfo.fund_code == fund_code).first()
    trend = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
    risk = db.query(FundRiskMetrics).filter(FundRiskMetrics.fund_code == fund_code).first()
    rank = db.query(FundScreeningRank).filter(FundScreeningRank.fund_code == fund_code).first()
    
    return jsonify({
        'fund_code': fund_code,
        'basic_info': {
            'exists': basic is not None,
            'updated_time': basic.updated_time.isoformat() if basic and basic.updated_time else None
        },
        'trend': {
            'exists': trend is not None,
            'updated_time': trend.updated_time.isoformat() if trend and trend.updated_time else None
        },
        'risk_metrics': {
            'exists': risk is not None,
            'updated_time': risk.updated_time.isoformat() if risk and risk.updated_time else None,
            'has_valid_data': risk.sharpe_ratio_1y is not None if risk else False
        },
        'screening_rank': {
            'exists': rank is not None,
            'updated_time': rank.updated_time.isoformat() if rank and rank.updated_time else None,
            'pass_4433': (rank.pass_4433 == 1) if rank else None
        }
    })


@app.route('/api/data/stats', methods=['GET'])
def get_data_stats():
    """获取数据库统计信息"""
    db = get_db()
    
    stats = {
        'fund_basic_info': db.query(FundBasicInfo).count(),
        'fund_trend': db.query(FundTrend).count(),
        'fund_risk_metrics': db.query(FundRiskMetrics).filter(
            FundRiskMetrics.sharpe_ratio_1y.isnot(None)
        ).count(),
        'fund_screening_rank': db.query(FundScreeningRank).count(),
        'fund_watchlist': db.query(FundWatchlist).count(),
        'pass_4433_count': db.query(FundScreeningRank).filter(
            FundScreeningRank.pass_4433 == 1
        ).count(),
    }
    
    # 按类型统计
    type_stats = db.query(
        FundBasicInfo.fund_type,
        func.count(FundBasicInfo.fund_code)
    ).group_by(FundBasicInfo.fund_type).all()
    
    stats['by_type'] = {t: c for t, c in type_stats if t}
    
    return jsonify(stats)


# ==================== 基金回测功能 ====================

@app.route('/api/backtest/fixed-investment', methods=['POST'])
def backtest_fixed_investment():
    """
    基金定投回测
    
    请求参数：
    {
        "fund_code": "000001",
        "start_date": "2020-01-01",
        "end_date": "2023-12-31",
        "investment_type": "monthly",  // monthly, weekly, lump_sum
        "amount": 1000,  // 每期投资金额
        "initial_amount": 0, // 初始资金（可选）
        "fee_rate": 0.15,  // 手续费率（百分比）
        "take_profit_rate": 20, // 止盈率（百分比，可选）
        "stop_loss_rate": 10 // 止损率（百分比，可选，正数）
    }
    """
    data = request.get_json()
    
    try:
        fund_code = data.get('fund_code')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        investment_type = data.get('investment_type', 'monthly')
        
        # 处理可能为空的数值输入
        def safe_float(val, default):
            if val is None or val == '':
                return default
            return float(val)

        amount = safe_float(data.get('amount'), 1000)
        initial_amount = safe_float(data.get('initial_amount'), 0)
        fee_rate = safe_float(data.get('fee_rate'), 0.15) / 100
        
        take_profit_rate = data.get('take_profit_rate')
        if take_profit_rate is not None and take_profit_rate != '':
            take_profit_rate = float(take_profit_rate) / 100
        else:
            take_profit_rate = None
            
        stop_loss_rate = data.get('stop_loss_rate')
        if stop_loss_rate is not None and stop_loss_rate != '':
            stop_loss_rate = float(stop_loss_rate) / 100
        else:
            stop_loss_rate = None
    
        if not all([fund_code, start_date, end_date]):
            return jsonify({'error': 'Missing required parameters'}), 400
        
        db = get_db()
        
        # 获取净值数据
        trend = db.query(FundTrend).filter(FundTrend.fund_code == fund_code).first()
        if not trend:
            return jsonify({'error': f'Fund data not found for code {fund_code}'}), 404
        
        net_worth_data = _json_loads(trend.net_worth_trend_json, [])
        if not net_worth_data:
            return jsonify({'error': 'No net worth data available'}), 404
        
        # 转换日期格式并排序
        nav_dict = {}
        for item in net_worth_data:
            date_str = item.get('date')
            nav = item.get('net_worth')
            # 修改判断逻辑，允许 net_worth 为 0 (虽然少见) 但不能为空
            if date_str and nav is not None:
                try:
                    nav_dict[date_str] = float(nav)
                except (ValueError, TypeError):
                    continue
        
        # 按日期排序
        sorted_dates = sorted(nav_dict.keys())
        
        if not sorted_dates:
             return jsonify({'error': 'Valid net worth data is empty'}), 404

        # 辅助日期解析函数
        def parse_date(date_str):
            for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S']:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
            raise ValueError(f"Unknown date format: {date_str}")

        # 过滤日期范围
        try:
            # 只取日期部分进行比较
            start_dt = parse_date(start_date).replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = parse_date(end_date).replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError as e:
            return jsonify({'error': f'Invalid date format: {str(e)}'}), 400
        
        filtered_dates = []
        for d in sorted_dates:
            try:
                current_dt = datetime.strptime(d, '%Y-%m-%d')
                if start_dt <= current_dt <= end_dt:
                    filtered_dates.append(d)
            except ValueError:
                continue

        if len(filtered_dates) < 2:
            return jsonify({'error': f'Insufficient data in range {start_date} to {end_date}. Found {len(filtered_dates)} records.'}), 400
        
        # 执行回测
        result = _run_backtest(
            nav_dict=nav_dict,
            dates=filtered_dates,
            investment_type=investment_type,
            amount=amount,
            initial_amount=initial_amount,
            fee_rate=fee_rate,
            take_profit_rate=take_profit_rate,
            stop_loss_rate=stop_loss_rate
        )
        
        if 'error' in result:
             return jsonify(result), 400

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Backtest execution failed: {str(e)}'}), 500


def _run_backtest(nav_dict, dates, investment_type, amount, initial_amount, fee_rate, take_profit_rate=None, stop_loss_rate=None):
    """
    执行回测计算
    """
    timeline = []
    total_invested = 0
    total_shares = 0
    
    # 确定投资日期
    investment_dates = []
    
    if investment_type == 'lump_sum':
        # 一次性投资：只在第一天
        investment_dates = [dates[0]]
    elif investment_type == 'monthly':
        # 每月定投：每月第一个交易日
        current_month = None
        for date in dates:
            dt = datetime.strptime(date, '%Y-%m-%d')
            month_key = (dt.year, dt.month)
            if month_key != current_month:
                investment_dates.append(date)
                current_month = month_key
    elif investment_type == 'weekly':
        # 每周定投：每周第一个交易日
        current_week = None
        for date in dates:
            dt = datetime.strptime(date, '%Y-%m-%d')
            week_key = (dt.year, dt.isocalendar()[1])
            if week_key != current_week:
                investment_dates.append(date)
                current_week = week_key
    
    # 状态标记
    sold_out = False
    exit_reason = None
    exit_date = None
    cash = 0
    
    # 遍历所有日期，计算持仓
    for i, date in enumerate(dates):
        nav = nav_dict[date]
        
        # 如果已经清仓止盈止损，后续只计算现金价值（假设不重新买入）
        if sold_out:
            timeline.append({
                'date': date,
                'invested': round(total_invested, 2),
                'shares': 0,
                'nav': round(nav, 4),
                'value': round(cash, 2),
                'return': round(cash - total_invested, 2),
                'return_rate': round((cash - total_invested) / total_invested * 100, 2) if total_invested > 0 else 0,
                'is_investment_day': False,
                'status': 'sold',
                'exit_reason': exit_reason
            })
            continue

        # 1. 处理初始资金 (仅第一天)
        if i == 0 and initial_amount > 0:
            actual_amount = initial_amount * (1 - fee_rate)
            shares_bought = actual_amount / nav
            total_shares += shares_bought
            total_invested += initial_amount
            
        # 2. 处理定投
        is_invest_day = False
        if investment_type != 'lump_sum' and date in investment_dates:
            actual_amount = amount * (1 - fee_rate)
            shares_bought = actual_amount / nav
            total_shares += shares_bought
            total_invested += amount
            is_invest_day = True
        elif investment_type == 'lump_sum' and i == 0 and amount > 0:
             # 如果是 lump_sum 且 amount > 0，视为第一天投入
             # 叠加 initial_amount
             actual_amount = amount * (1 - fee_rate)
             shares_bought = actual_amount / nav
             total_shares += shares_bought
             total_invested += amount
             is_invest_day = True
        
        # 计算当前市值
        current_value = total_shares * nav
        total_return = current_value - total_invested
        return_rate = (total_return / total_invested * 100) if total_invested > 0 else 0
        
        # 3. 检查止盈止损
        triggered = False
        if total_invested > 0:
            if take_profit_rate and return_rate >= (take_profit_rate * 100):
                sold_out = True
                exit_reason = 'take_profit'
                triggered = True
            elif stop_loss_rate and return_rate <= -(stop_loss_rate * 100):
                sold_out = True
                exit_reason = 'stop_loss'
                triggered = True
        
        if triggered:
            exit_date = date
            cash = current_value 
            
            timeline.append({
                'date': date,
                'invested': round(total_invested, 2),
                'shares': 0,
                'nav': round(nav, 4),
                'value': round(cash, 2),
                'return': round(cash - total_invested, 2),
                'return_rate': round((cash - total_invested) / total_invested * 100, 2),
                'is_investment_day': is_invest_day,
                'status': 'sold',
                'exit_reason': exit_reason
            })
            continue

        timeline.append({
            'date': date,
            'invested': round(total_invested, 2),
            'shares': round(total_shares, 4),
            'nav': round(nav, 4),
            'value': round(current_value, 2),
            'return': round(total_return, 2),
            'return_rate': round(return_rate, 2),
            'is_investment_day': is_invest_day,
            'status': 'holding'
        })
    
    # 计算汇总指标
    if len(timeline) == 0:
        return {'error': 'No data to backtest'}
    
    final_record = timeline[-1]
    
    # 计算最大回撤
    max_drawdown = 0
    peak_value = 0
    for record in timeline:
        value = record['value']
        if value > peak_value:
            peak_value = value
        if peak_value > 0:
            drawdown = (peak_value - value) / peak_value * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown
    
    # 计算年化收益率
    start_date = datetime.strptime(timeline[0]['date'], '%Y-%m-%d')
    end_date = datetime.strptime(timeline[-1]['date'], '%Y-%m-%d')
    days = (end_date - start_date).days
    years = days / 365.25
    
    total_return_rate = final_record['return_rate'] / 100
    annual_return = 0
    if years > 0 and total_return_rate > -1:
        annual_return = (pow(1 + total_return_rate, 1 / years) - 1) * 100
    
    # 计算夏普比率（简化版，假设无风险利率2%）
    returns = []
    for i in range(1, len(timeline)):
        if timeline[i-1]['value'] > 0:
            daily_return = (timeline[i]['value'] - timeline[i-1]['value']) / timeline[i-1]['value']
            returns.append(daily_return)
    
    sharpe_ratio = 0
    if len(returns) > 0:
        mean_return = sum(returns) / len(returns)
        if len(returns) > 1:
            variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
            std_dev = math.sqrt(variance)
            if std_dev > 0:
                # 年化夏普比率
                risk_free_rate = 0.02 / 252  # 日无风险利率
                sharpe_ratio = (mean_return - risk_free_rate) / std_dev * math.sqrt(252)
    
    summary = {
        'total_invested': round(final_record['invested'], 2),
        'final_value': round(final_record['value'], 2),
        'total_return': round(final_record['return'], 2),
        'return_rate': round(final_record['return_rate'], 2),
        'annual_return': round(annual_return, 2),
        'max_drawdown': round(-max_drawdown, 2),
        'sharpe_ratio': round(sharpe_ratio, 2),
        'investment_count': len(investment_dates) + (1 if initial_amount > 0 else 0),
        'days': days,
        'exit_reason': exit_reason,
        'exit_date': exit_date
    }
    
    return {
        'summary': summary,
        'timeline': timeline
    }


# ---------------------------------------------------------------------------
# Research dashboard
# ---------------------------------------------------------------------------

RESEARCH_FUND_GROUPS = [
    {"key": "equity", "name": "股票型", "keywords": ["股票"]},
    {"key": "hybrid", "name": "混合型", "keywords": ["混合"]},
    {"key": "bond", "name": "债券型", "keywords": ["债券", "债券型"]},
    {"key": "index", "name": "指数型", "keywords": ["指数", "联接"]},
    {"key": "etf", "name": "ETF", "keywords": ["ETF", "交易型开放式指数"]},
    {"key": "qdii", "name": "QDII", "keywords": ["QDII"]},
    {"key": "fof", "name": "FOF", "keywords": ["FOF"]},
    {"key": "money", "name": "货币型", "keywords": ["货币"]},
]


def _to_float(value):
    if value is None or value == '' or value == '--':
        return None
    try:
        text = str(value).replace('%', '').replace(',', '').strip()
        return float(text)
    except (TypeError, ValueError):
        return None


def _round_or_none(value, digits=2):
    num = _to_float(value)
    return round(num, digits) if num is not None else None


def _median(values):
    nums = sorted(v for v in (_to_float(item) for item in values) if v is not None)
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return round(nums[mid], 2)
    return round((nums[mid - 1] + nums[mid]) / 2, 2)


def _avg(values):
    nums = [v for v in (_to_float(item) for item in values) if v is not None]
    return round(sum(nums) / len(nums), 2) if nums else None


def _positive_rate(values):
    nums = [v for v in (_to_float(item) for item in values) if v is not None]
    return round(sum(1 for v in nums if v > 0) / len(nums) * 100, 2) if nums else None


def _fund_type_matches(fund_type, fund_name, keywords):
    text = f"{fund_type or ''} {fund_name or ''}".upper()
    return any(str(keyword).upper() in text for keyword in keywords)


def _fund_group_key(fund_type, fund_name):
    for group in RESEARCH_FUND_GROUPS:
        if _fund_type_matches(fund_type, fund_name, group["keywords"]):
            return group["key"]
    return "other"


def _research_fund_row(basic, risk=None, rank=None, estimate=None):
    perf = _json_loads(basic.performance_json, {}) if basic else {}
    return {
        "fund_code": basic.fund_code if basic else None,
        "fund_name": basic.fund_name if basic else None,
        "fund_type": basic.fund_type if basic else None,
        "return_1m": _round_or_none(perf.get("1_month_return")),
        "return_3m": _round_or_none(perf.get("3_month_return")),
        "return_6m": _round_or_none(perf.get("6_month_return")),
        "return_1y": _round_or_none(perf.get("1_year_return") if perf else basic.return_1y),
        "return_3y": _round_or_none(perf.get("3_year_return")),
        "max_drawdown_1y": _round_or_none(risk.max_drawdown_1y if risk else None),
        "volatility_1y": _round_or_none(risk.volatility_1y if risk else None),
        "sharpe_ratio_1y": _round_or_none(risk.sharpe_ratio_1y if risk else None),
        "calmar_ratio_1y": _round_or_none(risk.calmar_ratio_1y if risk else None),
        "rank_pct_1y": _round_or_none(rank.rank_pct_1y if rank else None),
        "pass_4433": bool(rank and rank.pass_4433 == 1),
        "estimate_change": _round_or_none(estimate.estimate_change if estimate else None),
        "estimate_time": estimate.estimate_time if estimate else None,
        "nav": _round_or_none(estimate.net_worth if estimate else None, 4),
        "nav_date": estimate.net_worth_date if estimate else None,
        "updated_time": basic.updated_time.isoformat() if basic and basic.updated_time else None,
    }


def _research_base_rows(db):
    return db.query(
        FundBasicInfo,
        FundRiskMetrics,
        FundScreeningRank,
        FundEstimate,
    ).outerjoin(
        FundRiskMetrics, FundBasicInfo.fund_code == FundRiskMetrics.fund_code
    ).outerjoin(
        FundScreeningRank, FundBasicInfo.fund_code == FundScreeningRank.fund_code
    ).outerjoin(
        FundEstimate, FundBasicInfo.fund_code == FundEstimate.fund_code
    ).all()


def _build_research_market_stats(db):
    rows = _research_base_rows(db)
    items = [_research_fund_row(basic, risk, rank, estimate) for basic, risk, rank, estimate in rows]
    total = len(items)
    risk_ready = sum(1 for _, risk, _, _ in rows if risk and risk.sharpe_ratio_1y is not None)
    rank_ready = sum(1 for _, _, rank, _ in rows if rank)
    pass_4433 = sum(1 for _, _, rank, _ in rows if rank and rank.pass_4433 == 1)

    type_map = {}
    group_map = {}
    for item in items:
        fund_type = item.get("fund_type") or "未分类"
        type_stat = type_map.setdefault(fund_type, {
            "fund_type": fund_type,
            "count": 0,
            "pass_4433": 0,
            "return_1y_values": [],
            "return_3m_values": [],
        })
        type_stat["count"] += 1
        type_stat["pass_4433"] += 1 if item.get("pass_4433") else 0
        type_stat["return_1y_values"].append(item.get("return_1y"))
        type_stat["return_3m_values"].append(item.get("return_3m"))

        group_key = _fund_group_key(item.get("fund_type"), item.get("fund_name"))
        group_stat = group_map.setdefault(group_key, {
            "key": group_key,
            "name": next((g["name"] for g in RESEARCH_FUND_GROUPS if g["key"] == group_key), "其他"),
            "count": 0,
            "return_1y_values": [],
        })
        group_stat["count"] += 1
        group_stat["return_1y_values"].append(item.get("return_1y"))

    type_stats = []
    for stat in type_map.values():
        type_stats.append({
            "fund_type": stat["fund_type"],
            "count": stat["count"],
            "ratio": round(stat["count"] / total * 100, 2) if total else 0,
            "pass_4433": stat["pass_4433"],
            "pass_rate": round(stat["pass_4433"] / stat["count"] * 100, 2) if stat["count"] else 0,
            "return_1y_median": _median(stat["return_1y_values"]),
            "return_3m_median": _median(stat["return_3m_values"]),
        })
    type_stats.sort(key=lambda item: item["count"], reverse=True)

    group_stats = []
    for stat in group_map.values():
        group_stats.append({
            "key": stat["key"],
            "name": stat["name"],
            "count": stat["count"],
            "ratio": round(stat["count"] / total * 100, 2) if total else 0,
            "return_1y_median": _median(stat["return_1y_values"]),
        })
    group_stats.sort(key=lambda item: item["count"], reverse=True)

    latest_update = max(
        (basic.updated_time for basic, _, _, _ in rows if basic and basic.updated_time),
        default=None,
    )

    return {
        "summary": {
            "total_funds": total,
            "risk_ready": risk_ready,
            "risk_ready_rate": round(risk_ready / total * 100, 2) if total else 0,
            "rank_ready": rank_ready,
            "rank_ready_rate": round(rank_ready / total * 100, 2) if total else 0,
            "pass_4433": pass_4433,
            "pass_4433_rate": round(pass_4433 / total * 100, 2) if total else 0,
            "return_1y_median": _median(item.get("return_1y") for item in items),
            "return_3m_median": _median(item.get("return_3m") for item in items),
            "positive_1y_rate": _positive_rate(item.get("return_1y") for item in items),
            "latest_update": latest_update.isoformat() if latest_update else None,
        },
        "type_stats": type_stats[:30],
        "group_stats": group_stats,
    }


def _build_research_fund_dashboard(db, limit=5):
    rows = _research_base_rows(db)
    grouped = {
        group["key"]: {
            "key": group["key"],
            "name": group["name"],
            "items": [],
            "summary": {},
        }
        for group in RESEARCH_FUND_GROUPS
    }
    grouped["other"] = {"key": "other", "name": "其他", "items": [], "summary": {}}

    for basic, risk, rank, estimate in rows:
        item = _research_fund_row(basic, risk, rank, estimate)
        group_key = _fund_group_key(item.get("fund_type"), item.get("fund_name"))
        grouped.setdefault(group_key, {"key": group_key, "name": "其他", "items": [], "summary": {}})
        grouped[group_key]["items"].append(item)

    cards = []
    for group in grouped.values():
        items = group["items"]
        if not items:
            continue
        sorted_items = sorted(
            items,
            key=lambda item: (
                item.get("pass_4433") is True,
                item.get("sharpe_ratio_1y") if item.get("sharpe_ratio_1y") is not None else -999,
                item.get("return_1y") if item.get("return_1y") is not None else -999,
            ),
            reverse=True,
        )
        cards.append({
            "key": group["key"],
            "name": group["name"],
            "summary": {
                "total": len(items),
                "pass_4433": sum(1 for item in items if item.get("pass_4433")),
                "return_1y_avg": _avg(item.get("return_1y") for item in items),
                "sharpe_1y_avg": _avg(item.get("sharpe_ratio_1y") for item in items),
            },
            "items": sorted_items[:limit],
        })

    cards.sort(key=lambda card: card["summary"]["total"], reverse=True)
    return {"cards": cards, "limit": limit}


def _row_value(row, index, *names):
    for name in names:
        try:
            value = row.get(name)
            if value is not None:
                return value
        except Exception:
            pass
    try:
        return row.iloc[index]
    except Exception:
        return None


def _parse_etf_trade_time(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value
    try:
        num = int(float(value))
        if num > 1000000000:
            return datetime.fromtimestamp(num)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _refresh_etf_tracking_from_akshare(db, limit=500):
    field_names = (
        "f12,f14,f2,f441,f402,f4,f3,f5,f6,f17,f15,f16,f18,f7,f8,f10,"
        "f30,f31,f32,f38,f21,f20,f297,f124"
    )
    params = {
        "pn": "1",
        "pz": str(max(limit, 100)),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
        "fields": field_names,
    }
    rows = []
    source = 'eastmoney.push2'
    try:
        response = requests.get(
            "https://88.push2.eastmoney.com/api/qt/clist/get",
            params=params,
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        rows = (payload.get('data') or {}).get('diff') or []
    except Exception as direct_exc:
        print(f"ETF tracking: eastmoney direct unavailable: {direct_exc}", flush=True)
        try:
            import akshare as ak
            df = ak.fund_etf_spot_em()
            if df is None or getattr(df, 'empty', True):
                rows = []
            else:
                rows = [
                    {
                        'f12': _row_value(row, 0, '代码'),
                        'f14': _row_value(row, 1, '名称'),
                        'f2': _row_value(row, 2, '最新价'),
                        'f441': _row_value(row, 3, 'IOPV实时估值'),
                        'f402': _row_value(row, 4, '折价率'),
                        'f4': _row_value(row, 5, '涨跌额'),
                        'f3': _row_value(row, 6, '涨跌幅'),
                        'f5': _row_value(row, 7, '成交量'),
                        'f6': _row_value(row, 8, '成交额'),
                        'f8': _row_value(row, 14, '换手率'),
                        'f38': _row_value(row, 32, '最新份额'),
                        'f20': _row_value(row, 34, '总市值'),
                        'f124': _row_value(row, 36, '更新时间'),
                    }
                    for _, row in df.head(limit).iterrows()
                ]
                source = 'akshare.eastmoney'
        except Exception as ak_exc:
            raise RuntimeError(f"eastmoney and akshare unavailable: {direct_exc}; {ak_exc}")

    if not rows:
        return 0

    saved = 0
    now = datetime.now()
    for row in rows[:limit]:
        code = _normalize_fund_code(row.get('f12') if isinstance(row, dict) else _row_value(row, 0, '代码'))
        if not code:
            continue

        record = db.query(FundEtfTracking).filter(FundEtfTracking.fund_code == code).first()
        if not record:
            record = FundEtfTracking(fund_code=code)
            db.add(record)

        record.fund_name = str(row.get('f14') or record.fund_name or '')
        record.latest_price = _to_float(row.get('f2'))
        record.iopv = _to_float(row.get('f441'))
        record.discount_rate = _to_float(row.get('f402'))
        record.change_amount = _to_float(row.get('f4'))
        record.change_percent = _to_float(row.get('f3'))
        record.volume = _to_float(row.get('f5'))
        record.amount = _to_float(row.get('f6'))
        record.turnover_rate = _to_float(row.get('f8'))
        record.fund_share = _to_float(row.get('f38'))
        record.market_value = _to_float(row.get('f20'))
        record.trade_time = _parse_etf_trade_time(row.get('f124')) or now
        record.source = source
        record.detail_json = _json_dumps({
            'fields': field_names.split(','),
        })
        record.updated_time = now
        saved += 1

    return saved


def _build_etf_tracking_from_cache(limit=80):
    funds = []
    for fund in getattr(fund_list_cache, 'fund_list', []) or []:
        name = fund.get('NAME') or fund.get('name') or ''
        code = _normalize_fund_code(fund.get('CODE') or fund.get('code'))
        if code and 'ETF' in name.upper():
            funds.append({
                'fund_code': code,
                'fund_name': name,
                'fund_type': 'ETF',
                'estimate_change': None,
                'return_1y': None,
                'nav_date': None,
                'source': 'fund_list_cache',
            })

    return {
        'items': funds[:limit],
        'categories': [],
        'summary': {
            'total': len(funds),
            'with_estimate': 0,
            'avg_estimate_change': None,
            'positive_estimate_rate': None,
            'net_flow_available': False,
            'source': 'fund_list_cache',
            'stale': True,
            'net_flow_note': '已从本地基金列表识别 ETF 池；实时行情需要刷新 AkShare/东方财富快照后显示。',
        },
    }


def _build_etf_tracking_snapshot(db, limit=80, refresh=False):
    source_error = None
    if refresh or db.query(FundEtfTracking).count() == 0:
        try:
            _refresh_etf_tracking_from_akshare(db, limit=max(limit, 300))
            db.commit()
        except Exception as exc:
            db.rollback()
            source_error = str(exc)
            print(f"ETF tracking: akshare unavailable: {exc}", flush=True)

    rows = db.query(FundEtfTracking).order_by(
        desc(FundEtfTracking.change_percent)
    ).limit(limit).all()
    if not rows:
        payload = _build_etf_tracking_from_cache(limit=limit)
        payload['summary']['source_error'] = source_error
        return payload

    items = []
    for row in rows:
        items.append({
            'fund_code': row.fund_code,
            'fund_name': row.fund_name,
            'fund_type': 'ETF',
            'latest_price': row.latest_price,
            'iopv': row.iopv,
            'discount_rate': row.discount_rate,
            'estimate_change': row.change_percent,
            'change_percent': row.change_percent,
            'change_amount': row.change_amount,
            'volume': row.volume,
            'amount': row.amount,
            'turnover_rate': row.turnover_rate,
            'fund_share': row.fund_share,
            'market_value': row.market_value,
            'return_1y': None,
            'nav_date': row.trade_time.date().isoformat() if row.trade_time else None,
            'updated_time': row.updated_time.isoformat() if row.updated_time else None,
            'source': row.source,
        })

    categories = {}
    for item in items:
        name = item.get('fund_name') or ''
        if any(word in name for word in ['债', '货币']):
            category = '债券/货币 ETF'
        elif any(word in name for word in ['港', '纳斯达克', '标普', '日经', '德国', 'QDII']):
            category = '跨境 ETF'
        elif any(word in name for word in ['黄金', '商品', '能源', '豆粕']):
            category = '商品 ETF'
        elif any(word in name for word in ['医药', '消费', '芯片', '半导体', '证券', '银行', '军工', 'AI', '机器人']):
            category = '行业主题 ETF'
        else:
            category = '宽基/普通指数 ETF'
        stat = categories.setdefault(category, {'category': category, 'count': 0, 'estimate_values': []})
        stat['count'] += 1
        stat['estimate_values'].append(item.get('estimate_change'))

    category_items = [
        {
            'category': stat['category'],
            'count': stat['count'],
            'estimate_change_avg': _avg(stat['estimate_values']),
            'return_1y_median': None,
            'net_flow': None,
        }
        for stat in categories.values()
    ]
    category_items.sort(key=lambda item: item['count'], reverse=True)

    latest_update = max((item.get('updated_time') for item in items if item.get('updated_time')), default=None)
    return {
        'items': items,
        'categories': category_items,
        'summary': {
            'total': db.query(FundEtfTracking).count(),
            'with_estimate': sum(1 for item in items if item.get('estimate_change') is not None),
            'avg_estimate_change': _avg(item.get('estimate_change') for item in items),
            'positive_estimate_rate': _positive_rate(item.get('estimate_change') for item in items),
            'net_flow_available': False,
            'source': 'funds.db:fund_etf_tracking',
            'source_error': source_error,
            'latest_update': latest_update,
            'net_flow_note': 'ETF 实时行情来自 AkShare/东方财富免费源；资金净流入暂未接入，当前展示涨跌、成交、IOPV 和份额快照。',
        },
    }


def _build_research_etf_tracking(db, limit=80):
    rows = _research_base_rows(db)
    etf_items = []
    for basic, risk, rank, estimate in rows:
        if _fund_type_matches(basic.fund_type, basic.fund_name, ["ETF", "交易型开放式指数", "指数"]):
            etf_items.append(_research_fund_row(basic, risk, rank, estimate))

    etf_items.sort(
        key=lambda item: (
            item.get("estimate_change") if item.get("estimate_change") is not None else -999,
            item.get("return_1y") if item.get("return_1y") is not None else -999,
        ),
        reverse=True,
    )
    snapshot_count = db.query(FundEtfTracking).count()
    estimate_count = sum(1 for item in etf_items if item.get("estimate_change") is not None)
    if snapshot_count > 0 or not etf_items or estimate_count < max(5, int(len(etf_items) * 0.05)):
        return _build_etf_tracking_snapshot(db, limit=limit)

    category_stats = {}
    for item in etf_items:
        name = item.get("fund_name") or ""
        fund_type = item.get("fund_type") or ""
        if "债" in name or "债" in fund_type:
            category = "债券ETF/指数"
        elif "港" in name or "QDII" in fund_type.upper() or "纳斯达克" in name or "标普" in name:
            category = "跨境ETF/指数"
        elif "黄金" in name or "商品" in name:
            category = "商品ETF/指数"
        elif "行业" in fund_type or any(word in name for word in ["医药", "消费", "芯片", "半导体", "证券", "银行", "军工"]):
            category = "行业主题ETF/指数"
        else:
            category = "宽基/普通指数"
        stat = category_stats.setdefault(category, {"category": category, "count": 0, "estimate_values": [], "return_1y_values": []})
        stat["count"] += 1
        stat["estimate_values"].append(item.get("estimate_change"))
        stat["return_1y_values"].append(item.get("return_1y"))

    categories = []
    for stat in category_stats.values():
        categories.append({
            "category": stat["category"],
            "count": stat["count"],
            "estimate_change_avg": _avg(stat["estimate_values"]),
            "return_1y_median": _median(stat["return_1y_values"]),
            "net_flow": None,
        })
    categories.sort(key=lambda item: item["count"], reverse=True)

    return {
        "items": etf_items[:limit],
        "categories": categories,
        "summary": {
            "total": len(etf_items),
            "with_estimate": sum(1 for item in etf_items if item.get("estimate_change") is not None),
            "avg_estimate_change": _avg(item.get("estimate_change") for item in etf_items),
            "positive_estimate_rate": _positive_rate(item.get("estimate_change") for item in etf_items),
            "net_flow_available": False,
            "net_flow_note": "当前数据源尚未接入 ETF 日/周/月资金净流入，第一版展示估值、净值和收益跟踪。",
        },
    }


def _build_research_sector_summary(limit=50):
    try:
        payload = get_data_service_client().get_market_sectors()
        data = payload.get('data', {}) if isinstance(payload, dict) else {}
        rows = data.get('items', []) if isinstance(data, dict) else []
    except Exception as exc:
        print(f"research sector summary: DataService unavailable: {exc}")
        try:
            fallback = get_fund_master_service().get_sector_rank(limit=limit)
            fallback_rows = fallback.get('data', []) if isinstance(fallback, dict) else []
            rows = [
                {
                    'code': item.get('code') or '',
                    'name': item.get('name') or '',
                    'changePercent': item.get('raw_change', item.get('change_pct')),
                    'mainNetInflow': item.get('raw_main_inflow', item.get('main_inflow')),
                }
                for item in fallback_rows
                if isinstance(item, dict)
            ]
        except Exception as fallback_exc:
            print(f"research sector summary: fallback unavailable: {fallback_exc}")
            rows = []

    items = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        change = _to_float(row.get('changePercent'))
        inflow = _to_float(row.get('mainNetInflow'))
        if change is None:
            mood = 'unknown'
            summary = '暂无涨跌幅数据'
        elif change >= 2:
            mood = 'strong'
            summary = '强势上涨，短线热度较高'
        elif change >= 0:
            mood = 'positive'
            summary = '温和上涨，表现好于弱势板块'
        elif change <= -2:
            mood = 'weak'
            summary = '明显回调，注意波动风险'
        else:
            mood = 'negative'
            summary = '小幅回落，走势偏弱'

        flow_summary = ''
        if inflow is not None:
            if inflow > 0:
                flow_summary = '，主力资金净流入'
            elif inflow < 0:
                flow_summary = '，主力资金净流出'

        items.append({
            'code': row.get('code') or '',
            'name': row.get('name') or '',
            'change_percent': change,
            'main_net_inflow': inflow,
            'mood': mood,
            'summary': f"{summary}{flow_summary}",
        })

    top_gainers = sorted(
        [item for item in items if item.get('change_percent') is not None],
        key=lambda item: item['change_percent'],
        reverse=True,
    )[:8]
    top_losers = sorted(
        [item for item in items if item.get('change_percent') is not None],
        key=lambda item: item['change_percent'],
    )[:8]
    inflow_leaders = sorted(
        [item for item in items if item.get('main_net_inflow') is not None],
        key=lambda item: item['main_net_inflow'],
        reverse=True,
    )[:8]

    return {
        'items': items,
        'top_gainers': top_gainers,
        'top_losers': top_losers,
        'inflow_leaders': inflow_leaders,
        'summary': {
            'total': len(items),
            'strong_count': sum(1 for item in items if item.get('mood') == 'strong'),
            'positive_count': sum(1 for item in items if item.get('change_percent') is not None and item['change_percent'] >= 0),
            'negative_count': sum(1 for item in items if item.get('change_percent') is not None and item['change_percent'] < 0),
        },
    }


@app.route('/api/research/market-stats', methods=['GET'])
def get_research_market_stats():
    db = get_db()
    return jsonify(_build_research_market_stats(db))


@app.route('/api/research/fund-dashboard', methods=['GET'])
def get_research_fund_dashboard():
    db = get_db()
    limit = request.args.get('limit', 5, type=int)
    return jsonify(_build_research_fund_dashboard(db, limit=max(1, min(limit, 20))))


@app.route('/api/research/etf-tracking', methods=['GET'])
def get_research_etf_tracking():
    db = get_db()
    limit = request.args.get('limit', 80, type=int)
    refresh = str(request.args.get('refresh', '')).lower() in ('1', 'true', 'yes')
    if refresh:
        return jsonify(_build_etf_tracking_snapshot(db, limit=max(10, min(limit, 300)), refresh=True))
    return jsonify(_build_research_etf_tracking(db, limit=max(10, min(limit, 300))))


@app.route('/api/research/sector-summary', methods=['GET'])
def get_research_sector_summary():
    limit = request.args.get('limit', 50, type=int)
    return jsonify(_build_research_sector_summary(limit=max(10, min(limit, 200))))


@app.route('/api/research/industry-performance', methods=['GET'])
def get_research_industry_performance():
    db = get_db()
    if db.query(FundIndustryPerformance).count() == 0:
        try:
            rebuild_industry_performance_stats(db)
            db.commit()
        except Exception as exc:
            db.rollback()
            return jsonify({'success': False, 'error': str(exc)}), 500
    return jsonify(_industry_performance_payload(db))


@app.route('/api/research/rebuild-industry-performance', methods=['POST'])
def rebuild_research_industry_performance():
    db = get_db()
    try:
        total = rebuild_industry_performance_stats(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 500
    return jsonify({
        'success': True,
        'total': total,
        'data': _industry_performance_payload(db),
    })


@app.route('/api/research/dashboard', methods=['GET'])
def get_research_dashboard():
    db = get_db()
    limit = request.args.get('limit', 5, type=int)
    etf_limit = request.args.get('etf_limit', 80, type=int)
    if db.query(FundIndustryPerformance).count() == 0:
        try:
            rebuild_industry_performance_stats(db)
            db.commit()
        except Exception as exc:
            db.rollback()
            print(f"research dashboard industry performance rebuild failed: {exc}")
    return jsonify({
        "market_stats": _build_research_market_stats(db),
        "fund_dashboard": _build_research_fund_dashboard(db, limit=max(1, min(limit, 20))),
        "etf_tracking": _build_research_etf_tracking(db, limit=max(10, min(etf_limit, 300))),
        "industry_performance": _industry_performance_payload(db),
        "updated_at": datetime.now().isoformat(),
        "data_source": {
            "primary": "funds.db",
            "industry_performance": "funds.db fund industry tags + screening performance",
            "etf_net_flow": "not_available",
        },
    })

def preload_services():
    """
    服务启动时的预加载任务
    在后台线程执行，不阻塞服务启动
    """
    import threading
    
    def _preload():
        import time
        time.sleep(2)  # 等待服务完全启动
        try:
            ai_service = get_ai_service()
            if ai_service.is_available():
                print("AI 服务已就绪（硅基流动 API）")
        except Exception as e:
            print(f"Preload failed: {e}")
    
    thread = threading.Thread(target=_preload, daemon=True)
    thread.start()

# 启动预加载（在应用启动时执行）
preload_services()

from flask import send_from_directory

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    """服务前端静态文件，SPA 路由支持"""
    import os
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    if path and os.path.exists(os.path.join(static_dir, path)):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, 'index.html')

def _auto_ranking_scheduler():
    """每周自动重新计算一次同类排名（后台线程，每7天执行一次）"""
    import threading
    def _run_weekly():
        while True:
            time.sleep(3600)  # 每小时检查一次
            try:
                db = SessionLocal()
                # 检查上次排名计算时间
                latest_ranking = db.query(FundScreeningRank).order_by(
                    desc(FundScreeningRank.updated_time)
                ).first()
                should_run = False
                if latest_ranking and latest_ranking.updated_time:
                    delta = datetime.now() - latest_ranking.updated_time
                    if delta.total_seconds() > 7 * 24 * 3600:  # 7天
                        should_run = True
                else:
                    should_run = True  # 从未计算过
                db.close()

                if should_run:
                    print("[自动排名] 开始每周同类排名计算...", flush=True)
                    db2 = SessionLocal()
                    try:
                        calculate_same_type_rankings(db2)
                        db2.commit()
                        print("[自动排名] 每周同类排名计算完成", flush=True)
                    except Exception as e:
                        db2.rollback()
                        print(f"[自动排名] 计算失败: {e}", flush=True)
                    finally:
                        db2.close()
            except Exception as e:
                print(f"[自动排名] 调度检查失败: {e}", flush=True)

    t = threading.Thread(target=_run_weekly, daemon=True)
    t.start()
    print("[自动排名] 后台周度排名调度已启动", flush=True)


def _cleanup_stale_tasks_on_startup():
    """启动时清理上次崩溃残留的运行中任务"""
    try:
        db = SessionLocal()
        stale = db.query(DataFetchTask).filter(
            DataFetchTask.status == 'running'
        ).all()
        for task in stale:
            task.status = 'failed'
            task.message = '服务器重启，任务中断'
            task.finished_time = datetime.now()
            task.updated_time = datetime.now()
        if stale:
            db.commit()
            print(f"[启动清理] 已将 {len(stale)} 个残留任务标记为失败", flush=True)
        db.close()
    except Exception as e:
        print(f"[启动清理] 失败: {e}", flush=True)


if __name__ == '__main__':
    _cleanup_stale_tasks_on_startup()
    _auto_ranking_scheduler()
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
