# -*- coding: UTF-8 -*-
"""
DataService 代理路由模块
将前端的 /api/data-service/* 请求转发到 DataService 后端

DataService-first architecture 的一部分：
  前端 → Flask Backend (此模块) → DataServiceClient → DataService (Node.js)
"""

from flask import Blueprint, jsonify, request
from services.data_service_client import DataServiceError, get_data_service_client

# 创建 Blueprint，所有路由以 /api/data-service 开头
data_service_bp = Blueprint('data_service', __name__, url_prefix='/api/data-service')


@data_service_bp.route('/health', methods=['GET'])
def health():
    """DataService 健康检查"""
    try:
        client = get_data_service_client()
        result = client.health()
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code
    except Exception as e:
        return jsonify({
            "success": False,
            "error": {"code": "DATA_SERVICE_UNAVAILABLE", "message": str(e)}
        }), 503


# ---------------------------------------------------------------------------
# Fund 相关代理
# ---------------------------------------------------------------------------

@data_service_bp.route('/funds/search', methods=['GET'])
def proxy_fund_search():
    """代理基金搜索 → DataService /api/funds/search"""
    keyword = request.args.get('q', '')
    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400
    try:
        result = get_data_service_client().search_funds(keyword)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/detail', methods=['GET'])
def proxy_fund_detail(code):
    """代理基金详情 → DataService /api/funds/:code/detail"""
    try:
        result = get_data_service_client().get_fund_detail(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/estimate', methods=['GET'])
def proxy_fund_estimate(code):
    """代理基金估值 → DataService /api/funds/:code/estimate"""
    try:
        result = get_data_service_client().get_fund_estimate(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/estimates', methods=['GET'])
def proxy_fund_estimates():
    """代理批量基金估值 → DataService /api/funds/estimates"""
    codes = request.args.get('codes', '')
    if not codes:
        return jsonify({"error": "codes parameter is required"}), 400
    try:
        result = get_data_service_client().get_fund_estimates(codes.split(','))
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/basic', methods=['GET'])
def proxy_fund_basic(code):
    """代理基金基本信息 → DataService /api/funds/:code/basic"""
    try:
        result = get_data_service_client().get_fund_basic(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/nav-history', methods=['GET'])
def proxy_fund_nav_history(code):
    """代理基金净值历史 → DataService /api/funds/:code/nav-history"""
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')
    try:
        result = get_data_service_client().get_fund_nav_history(code, start_date, end_date)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/rank-history', methods=['GET'])
def proxy_fund_rank_history(code):
    """代理基金排名历史 → DataService /api/funds/:code/rank-history"""
    try:
        result = get_data_service_client().get_fund_rank_history(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/dividends', methods=['GET'])
def proxy_fund_dividends(code):
    """代理基金分红 → DataService /api/funds/:code/dividends"""
    try:
        result = get_data_service_client().get_fund_dividends(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/holdings', methods=['GET'])
def proxy_fund_holdings(code):
    """代理基金持仓 → DataService /api/funds/:code/holdings"""
    try:
        result = get_data_service_client().get_fund_holdings(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/managers', methods=['GET'])
def proxy_fund_managers(code):
    """代理基金经理 → DataService /api/funds/:code/managers"""
    try:
        result = get_data_service_client().get_fund_managers(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/funds/<code>/asset-allocation', methods=['GET'])
def proxy_fund_asset_allocation(code):
    """代理基金资产配置 → DataService /api/funds/:code/asset-allocation"""
    try:
        result = get_data_service_client().get_fund_asset_allocation(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


# ---------------------------------------------------------------------------
# Stock 相关代理
# ---------------------------------------------------------------------------

@data_service_bp.route('/stocks/<code>/reference', methods=['GET'])
def proxy_stock_reference(code):
    """代理股票引用 → DataService /api/stocks/:code/reference"""
    try:
        result = get_data_service_client().get_stock_reference(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/stocks/references', methods=['GET'])
def proxy_stock_references():
    """代理批量股票引用 → DataService /api/stocks/references"""
    codes = request.args.get('codes', '')
    if not codes:
        return jsonify({"error": "codes parameter is required"}), 400
    try:
        result = get_data_service_client().get_stock_references(codes.split(','))
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


# ---------------------------------------------------------------------------
# Market 相关代理
# ---------------------------------------------------------------------------

@data_service_bp.route('/market/quotes', methods=['GET'])
def proxy_market_quotes():
    """代理市场行情 → DataService /api/market/quotes"""
    symbols = request.args.get('symbols', '')
    if not symbols:
        return jsonify({"error": "symbols parameter is required"}), 400
    try:
        result = get_data_service_client().get_market_quotes(symbols)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/market/kline/<symbol>', methods=['GET'])
def proxy_market_kline(symbol):
    """代理市场K线 → DataService /api/market/kline/:symbol"""
    period = request.args.get('period', 'daily')
    adjust = request.args.get('adjust', 'none')
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')
    try:
        result = get_data_service_client().get_market_kline(
            symbol, period=period, adjust=adjust,
            start_date=start_date, end_date=end_date
        )
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/market/indices', methods=['GET'])
def proxy_market_indices():
    """代理市场指数 → DataService /api/market/indices"""
    try:
        result = get_data_service_client().get_market_indices()
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/market/sectors', methods=['GET'])
def proxy_market_sectors():
    """代理行业板块 → DataService /api/market/sectors"""
    try:
        result = get_data_service_client().get_market_sectors()
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


@data_service_bp.route('/market/sectors/<code>/constituents', methods=['GET'])
def proxy_market_sector_constituents(code):
    """代理板块成分股 → DataService /api/market/sectors/:code/constituents"""
    try:
        result = get_data_service_client().get_market_sector_constituents(code)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code


# ---------------------------------------------------------------------------
# News 相关代理
# ---------------------------------------------------------------------------

@data_service_bp.route('/news/flash', methods=['GET'])
def proxy_flash_news():
    """代理快讯 → DataService /api/news/flash"""
    count = request.args.get('count', 30, type=int)
    try:
        result = get_data_service_client().get_flash_news(count=count)
        return jsonify(result)
    except DataServiceError as e:
        return jsonify(e.to_payload()), e.status_code
