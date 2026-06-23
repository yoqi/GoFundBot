# -*- coding: UTF-8 -*-
"""
Fund-Master API 路由模块
提供市场数据相关的 REST API

DataService-first architecture:
  - Try DataServiceClient for each endpoint.
  - On failure, fallback to legacy FundMasterService / MarketDataService.
  - Never change the response structure visible to Frontend.
"""

from flask import Blueprint, jsonify, request
from fund_master_service import get_fund_master_service
from services.data_service_client import DataServiceError, get_data_service_client

# 创建 Blueprint
fund_master_bp = Blueprint('fund_master', __name__, url_prefix='/api/market')


@fund_master_bp.route('/overview', methods=['GET'])
def get_market_overview():
    """
    获取市场概览（汇总所有关键数据）
    GET /api/market/overview
    """
    service = get_fund_master_service()
    return jsonify(service.get_market_overview())


@fund_master_bp.route('/news', methods=['GET'])
def get_flash_news():
    """
    获取7x24快讯 – DataService first, legacy fallback
    GET /api/market/news?count=30&page=1
    """
    count = request.args.get('count', 30, type=int)
    page = request.args.get('page', 1, type=int)

    # 1) Try DataService
    try:
        ds_payload = get_data_service_client().get_flash_news(count=count, page=page)
        ds_data = ds_payload.get('data', {}) if isinstance(ds_payload, dict) else {}
        ds_items = ds_data.get('items', []) if isinstance(ds_data, dict) else []

        if ds_items:
            # Map to legacy format expected by Frontend / AI summary
            news_list = []
            for item in ds_items:
                if not isinstance(item, dict):
                    continue
                news_list.append({
                    'title': item.get('title', ''),
                    'evaluate': item.get('summary', ''),
                    'publish_time': item.get('publishedAt', ''),
                    'related_stocks': [],
                    'source': item.get('source', ''),
                })
            meta = ds_payload.get('meta', {}) if isinstance(ds_payload.get('meta'), dict) else {}
            sources = sorted({item.get('source', '') for item in news_list if item.get('source')})
            print(f"market news: DataService success, {len(news_list)} items (page={page}, total={ds_data.get('total', len(news_list))}, sources={sources})")
            return jsonify({
                'success': True,
                'data': news_list,
                'update_time': meta.get('updatedAt', ''),
                'total': ds_data.get('total', len(news_list)),
                'hasMore': ds_data.get('hasMore', False),
                'sources': sources,
                'source': meta.get('provider') or 'data_service',
            })
    except DataServiceError as e:
        print(f"market news: DataService unavailable, fallback to legacy: {e}")

    # 2) Fallback to legacy (aggregates 3 sources: Baidu, EastMoney, CLS)
    service = get_fund_master_service()
    return jsonify(service.get_flash_news(count=count))


@fund_master_bp.route('/sectors', methods=['GET'])
def get_sector_rank():
    """
    获取行业板块排行 – TongHuaShun first

    GET /api/market/sectors?limit=90

    数据流：路由 → FundMasterService → akshare.stock_board_industry_summary_ths。
    东财行业板块源长期不稳定，这里不再作为主链路。
    """
    limit = request.args.get('limit', 90, type=int)
    limit = max(1, min(int(limit or 90), 120))

    service = get_fund_master_service()
    return jsonify(service.get_sector_rank(limit=limit))


@fund_master_bp.route('/sector/<code>/constituents', methods=['GET'])
def get_sector_constituents(code):
    """
    获取板块成分股 – DataService first, legacy fallback
    GET /api/market/sector/<code>/constituents
    """
    # 1) Try DataService
    try:
        ds_payload = get_data_service_client().get_market_sector_constituents(code)
        ds_data = ds_payload.get('data', {}) if isinstance(ds_payload, dict) else {}
        ds_items = ds_data.get('items', []) if isinstance(ds_data, dict) else []

        if ds_items:
            constituents = []
            for item in ds_items:
                if not isinstance(item, dict):
                    continue
                constituents.append({
                    'code': item.get('code', ''),
                    'name': item.get('name', ''),
                    'price': _safe_float(item.get('price')),
                    'change_pct': _fmt_pct(item.get('changePercent')),
                    'market_value': _safe_float(item.get('marketValue')),
                    'pe': _safe_float(item.get('pe')),
                })
            print(f"sector constituents: DataService success, {len(constituents)} items for {code}")
            return jsonify({
                'success': True,
                'data': constituents,
            })
    except DataServiceError as e:
        print(f"sector constituents: DataService unavailable, fallback to legacy: {e}")

    # 2) Fallback to legacy
    from services.market_data import get_market_data_service as get_mds
    mds = get_mds()
    result = mds.get_industry_constituents(code)
    return jsonify(result)


@fund_master_bp.route('/index', methods=['GET'])
def get_market_index():
    """
    获取市场指数汇总 – DataService first, legacy fallback
    GET /api/market/index
    """
    # 1) Try DataService
    try:
        ds_payload = get_data_service_client().get_market_indices()
        ds_data = ds_payload.get('data', {}) if isinstance(ds_payload, dict) else {}
        ds_items = ds_data.get('items', []) if isinstance(ds_data, dict) else []

        if ds_items:
            # Map to legacy format
            indices = []
            for item in ds_items:
                if not isinstance(item, dict):
                    continue
                change_num = _safe_float(item.get('changePercent'))
                price_num = _safe_float(item.get('price'))
                indices.append({
                    'name': item.get('name', ''),
                    'price': f"{price_num:.2f}" if price_num is not None else '-',
                    'change_pct': f"{'+' if (change_num or 0) >= 0 else ''}{change_num:.2f}%" if change_num is not None else '0.00%',
                    'market': item.get('market', ''),
                    'raw_change': change_num or 0.0,
                })
            print(f"market index: DataService success, {len(indices)} indices")
            return jsonify({
                'success': True,
                'data': indices,
                'update_time': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })
    except DataServiceError as e:
        print(f"market index: DataService unavailable, fallback to legacy: {e}")

    # 2) Fallback to legacy
    service = get_fund_master_service()
    return jsonify(service.get_market_index())


@fund_master_bp.route('/gold/realtime', methods=['GET'])
def get_gold_realtime():
    """
    获取实时贵金属价格
    GET /api/market/gold/realtime
    """
    service = get_fund_master_service()
    return jsonify(service.get_gold_realtime())


@fund_master_bp.route('/gold/history', methods=['GET'])
def get_gold_history():
    """
    获取黄金历史价格
    GET /api/market/gold/history?days=10
    """
    days = request.args.get('days', 10, type=int)
    service = get_fund_master_service()
    return jsonify(service.get_gold_history(days=days))


@fund_master_bp.route('/volume', methods=['GET'])
def get_a_volume_7days():
    """
    获取近7日A股成交量
    GET /api/market/volume
    """
    service = get_fund_master_service()
    return jsonify(service.get_a_volume_7days())


@fund_master_bp.route('/indices/intraday', methods=['GET'])
def get_indices_intraday():
    """
    获取多指数分时数据（上证、深证、沪深300）
    GET /api/market/indices/intraday
    """
    service = get_fund_master_service()
    return jsonify(service.get_indices_intraday())


@fund_master_bp.route('/sse', methods=['GET'])
def get_sse_30min():
    """
    获取近30分钟上证指数
    GET /api/market/sse
    """
    service = get_fund_master_service()
    return jsonify(service.get_sse_30min())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value) -> float:
    """Convert value to float, returning 0.0 on failure."""
    if value is None or value == '' or value == '-':
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _fmt_pct(value) -> str:
    """Format a number as a signed percentage string."""
    num = _safe_float(value)
    return f"{'+' if num >= 0 else ''}{num:.2f}%"


def _sector_moves_look_one_sided(sectors) -> bool:
    """Reject obviously suspicious sector snapshots before falling back."""
    if not sectors or len(sectors) < 20:
        return False
    moves = [_safe_float(item.get('raw_change')) for item in sectors]
    non_zero = [move for move in moves if abs(move) > 0.0001]
    if len(non_zero) < 10:
        return True
    up_count = sum(1 for move in non_zero if move > 0)
    down_count = sum(1 for move in non_zero if move < 0)
    return up_count == len(non_zero) or down_count == len(non_zero)


def _fmt_amount_yi(value) -> str:
    """Format a value in 亿元 (yi). The DataService already returns raw values."""
    num = _safe_float(value)
    if abs(num) >= 1e8:
        return f"{num / 1e8:.2f}亿"
    if abs(num) >= 1e4:
        return f"{num / 1e4:.2f}万"
    return f"{num:.2f}"
