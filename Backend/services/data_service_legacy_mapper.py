# -*- coding: utf-8 -*-
"""
DataService → Legacy /api/fund/<code> response mapper.

DO NOT connect this to production routes.  It exists only for:
  - compare_data_service_contract.py
  - future canary / grey-release testing
  - gap analysis

When a field cannot be mapped the mapper sets it to None, an empty list,
or a ``missing`` marker — it never fabricates data.
"""

from typing import Any, Dict, List, Optional


def map_data_service_detail_to_legacy(data_service_payload: dict) -> dict:
    """Convert a DataService /funds/:code/detail response into the legacy
    /api/fund/<code> shape.

    Returns a dict whose top-level keys match the old fund detail contract
    as closely as possible.
    """
    if not isinstance(data_service_payload, dict):
        return _empty_legacy()

    ds_data = data_service_payload.get("data", {}) if isinstance(data_service_payload, dict) else {}
    sections: Dict[str, Any] = ds_data.get("sections", {}) if isinstance(ds_data, dict) else {}

    legacy: Dict[str, Any] = {}

    # -- basic_info --
    legacy["basic_info"] = _map_basic_info(sections.get("basic", {}))

    # -- realtime_estimate --
    legacy["realtime_estimate"] = _map_estimate(sections.get("estimate", {}))

    # -- net_worth_trend + accumulated_net_worth --
    nav = _map_nav_history(sections.get("navHistory", {}))
    legacy["net_worth_trend"] = nav["net_worth_trend"]
    legacy["accumulated_net_worth"] = nav["accumulated_net_worth"]

    # -- ranking_trend + ranking_percentage --
    rank = _map_rank_history(sections.get("rankHistory", {}))
    legacy["ranking_trend"] = rank["ranking_trend"]
    legacy["ranking_percentage"] = rank["ranking_percentage"]

    # -- portfolio --
    legacy["portfolio"] = _map_holdings(sections.get("holdings", {}))

    # -- asset_allocation --
    legacy["asset_allocation"] = _map_asset_allocation(sections.get("assetAllocation", {}))

    # -- fund_managers --
    legacy["fund_managers"] = _map_managers(sections.get("managers", {}))

    # -- performance --
    legacy["performance"] = _map_performance(sections.get("performance", {}))

    # -- subscription_redemption --
    legacy["subscription_redemption"] = _map_subscription_redemption(
        sections.get("subscriptionRedemption", {})
    )

    # -- holder_structure --
    legacy["holder_structure"] = _map_holder_structure(sections.get("holderStructure", {}))

    # -- same_type_funds --
    legacy["same_type_funds"] = _map_same_type_funds(sections.get("sameTypeFunds", {}))

    # -- risk_metrics (computed from navHistory, NOT from EastMoney) --
    legacy["risk_metrics"] = calculate_risk_metrics_from_nav_history(sections.get("navHistory", {}))

    # -- scale_fluctuation --
    legacy["scale_fluctuation"] = _map_scale_fluctuation(sections.get("scaleFluctuation", {}))

    # -- position_trend --
    legacy["position_trend"] = _map_position_trend(sections.get("positionTrend", {}))

    # -- total_return_trend --
    legacy["total_return_trend"] = _map_total_return_trend(sections.get("totalReturnTrend", {}))

    # -- performance_evaluation --
    legacy["performance_evaluation"] = _map_performance_evaluation(sections.get("performanceEvaluation", {}))

    legacy["cleaning_timestamp"] = ds_data.get("updatedAt")

    return legacy


# ---------------------------------------------------------------------------
# Section mappers
# ---------------------------------------------------------------------------

def _section_data(section: dict):
    """Extract .data from a detail section dict. Returns None if section is not a dict.
    Returns the .data value regardless of type (dict, list, etc.)."""
    if not isinstance(section, dict):
        return None
    data = section.get("data")
    if data is None:
        return None
    return data


def _map_basic_info(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"missing": True}
    return {
        "fund_code": data.get("code"),
        "fund_name": data.get("name"),
        "fund_type": data.get("type"),
        "original_rate": data.get("originalRate"),
        "current_rate": data.get("currentRate"),
        "min_subscription_amount": data.get("minSubscriptionAmount"),
        "is_hb": data.get("isHB"),
    }


def _map_estimate(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"missing": True}
    return {
        "fund_code": data.get("code"),
        "name": data.get("name"),
        "net_worth": _fmt_float4(data.get("nav")),
        "net_worth_date": data.get("navDate"),
        "estimate_value": _fmt_float4(data.get("estimatedNav")),
        "estimate_change": _to_str(data.get("estimatedChangePercent")),
        "estimate_time": data.get("estimateTime"),
    }


def _map_nav_history(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"net_worth_trend": [], "accumulated_net_worth": []}

    items = data.get("items", []) if isinstance(data, dict) else []

    net_worth_trend = []
    accumulated_net_worth = []
    for item in items:
        if not isinstance(item, dict):
            continue
        net_worth_trend.append({
            "date": item.get("date"),
            "net_worth": _safe_float(item.get("nav")),
            "equity_return": _safe_float(item.get("dailyReturn")),
            "dividend": None,
        })
        accumulated_net_worth.append({
            "date": item.get("date"),
            "position_percentage": _safe_float(item.get("accNav")),
        })

    return {"net_worth_trend": net_worth_trend, "accumulated_net_worth": accumulated_net_worth}


def _map_rank_history(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"ranking_trend": [], "ranking_percentage": []}

    items = data.get("items", []) if isinstance(data, dict) else []

    ranking_trend = []
    ranking_percentage = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ranking_trend.append({
            "date": item.get("date"),
            "rank": item.get("rank"),
            "total_funds": str(item.get("total", "")) if item.get("total") is not None else None,
        })
        ranking_percentage.append({
            "date": item.get("date"),
            "position_percentage": item.get("percentile"),
        })

    return {"ranking_trend": ranking_trend, "ranking_percentage": ranking_percentage}


def _map_holdings(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"stock_codes": [], "bond_codes": [], "stock_codes_new": [], "bond_codes_new": []}

    items = data.get("items", []) if isinstance(data, dict) else []
    stock_codes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        stock_codes.append({
            "code": item.get("stockCode"),
            "name": item.get("stockName"),
            "market": item.get("market"),
            "original_code": None,
            "ratio": item.get("ratio"),
        })

    bond_codes = data.get("bondCodes", []) if isinstance(data, dict) else []
    bond_codes_new = data.get("bondCodesNew", []) if isinstance(data, dict) else []

    return {
        "stock_codes": stock_codes,
        "bond_codes": bond_codes if isinstance(bond_codes, list) else [],
        "stock_codes_new": stock_codes,
        "bond_codes_new": bond_codes_new if isinstance(bond_codes_new, list) else [],
    }


def _map_asset_allocation(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"categories": [], "series": []}

    categories = []
    series = []
    if data.get("stock") is not None:
        categories.append("股票")
        series.append({"name": "股票", "data": [data.get("stock")], "type": None, "yAxis": 0})
    if data.get("bond") is not None:
        categories.append("债券")
        series.append({"name": "债券", "data": [data.get("bond")], "type": None, "yAxis": 0})
    if data.get("cash") is not None:
        categories.append("现金")
        series.append({"name": "现金", "data": [data.get("cash")], "type": None, "yAxis": 0})
    if data.get("other") is not None:
        categories.append("其他")
        series.append({"name": "其他", "data": [data.get("other")], "type": None, "yAxis": 0})

    return {"categories": categories, "series": series}


def _map_managers(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "start_date": item.get("startDate"),
            "photo_url": None,
            "star_rating": None,
            "work_experience": None,
            "ability_assessment": None,
            "performance": None,
            "managed_fund_size": None,
        })
    return result


def _map_performance(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {}
    return {
        "1_month_return": data.get("return1m"),
        "3_month_return": data.get("return3m"),
        "6_month_return": data.get("return6m"),
        "1_year_return": data.get("return1y"),
    }


def _map_subscription_redemption(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"categories": [], "series": []}

    categories = []
    series = []
    fields = [
        ("minPurchaseAmount", "最低申购金额"),
        ("purchaseFee", "申购费率"),
        ("redemptionFee", "赎回费率"),
        ("managementFee", "管理费率"),
        ("custodyFee", "托管费率"),
    ]
    for key, label in fields:
        val = data.get(key)
        if val is not None:
            categories.append(label)
            series.append({"name": label, "data": [val]})

    return {"categories": categories, "series": series}


def _map_holder_structure(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"missing": True, "reason": "not yet covered by DataService"}
    return {
        "categories": data.get("categories", []),
        "series": data.get("series", []),
    }


def _map_same_type_funds(section: dict) -> list:
    data = _section_data(section)
    if not data:
        return []
    if not isinstance(data, list):
        return []
    # Data is [[{code, name, returnRate}, ...], ...] — convert camelCase to snake_case
    result = []
    for category in data:
        if not isinstance(category, list):
            result.append([])
            continue
        funds = []
        for fund in category:
            if not isinstance(fund, dict):
                continue
            funds.append({
                "code": fund.get("code", ""),
                "name": fund.get("name", ""),
                "return_rate": _safe_float(fund.get("returnRate")),
            })
        result.append(funds)
    return result


def _map_scale_fluctuation(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"categories": [], "series": []}
    return {
        "categories": data.get("categories", []),
        "series": data.get("series", []),
    }


def _map_position_trend(section: dict) -> list:
    data = _section_data(section)
    if not data:
        return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        result.append({
            "date": item.get("date"),
            "position_percentage": _safe_float(item.get("positionPercentage")),
        })
    return result


def _map_total_return_trend(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"series": []}
    series = data.get("series", []) if isinstance(data, dict) else []
    return {"series": series}


def _map_performance_evaluation(section: dict) -> dict:
    data = _section_data(section)
    if not data:
        return {"missing": True}
    return {
        "avr": data.get("avr"),
        "categories": data.get("categories", []),
        "data": data.get("data", []),
        "dsc": data.get("dsc", []),
    }


# ---------------------------------------------------------------------------
# Risk metrics (computed from navHistory, NOT from EastMoney)
# ---------------------------------------------------------------------------

def calculate_risk_metrics_from_nav_history(nav_history_section: dict) -> dict:
    """Compute risk metrics from DataService detail.sections.navHistory.data.

    Uses the same algorithm as Backend app.py calc_fund_risk_metrics().
    Returns a dict with the old /api/fund/<code> risk_metrics shape, or None
    for each field when data is insufficient.
    """
    data = _section_data(nav_history_section)
    if not data:
        return _empty_risk_metrics()

    items = data.get("items", []) if isinstance(data, dict) else []
    if len(items) < 10:
        return _empty_risk_metrics()

    # Extract NAV values and dates
    values = []
    dates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        nav = item.get("nav")
        date = item.get("date")
        if nav is not None and nav > 0:
            values.append(float(nav))
            dates.append(str(date))

    if len(values) < 10:
        return _empty_risk_metrics()

    import math
    from datetime import datetime, timedelta

    now = datetime.now()

    def get_period_data(months):
        if months == "all":
            return values, dates
        cutoff = (now - timedelta(days=months * 30)).strftime("%Y-%m-%d")
        pv, pd = [], []
        for i, d in enumerate(dates):
            if d >= cutoff:
                pv.append(values[i])
                pd.append(d)
        return pv, pd

    def calc_max_drawdown(pv):
        if len(pv) < 2:
            return None
        peak = pv[0]
        max_dd = 0.0
        for v in pv:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)

    def calc_daily_returns(pv):
        if len(pv) < 2:
            return []
        rets = []
        for i in range(1, len(pv)):
            if pv[i - 1] != 0:
                rets.append((pv[i] - pv[i - 1]) / pv[i - 1])
        return rets

    def calc_annual_return(pv, trading_days):
        if len(pv) < 2 or pv[0] == 0 or trading_days <= 0:
            return None
        total_return = (pv[-1] - pv[0]) / pv[0]
        annual = ((1 + total_return) ** (252.0 / trading_days) - 1) * 100.0
        return round(annual, 2)

    def calc_volatility(daily_rets):
        if len(daily_rets) < 10:
            return None
        mean = sum(daily_rets) / len(daily_rets)
        var = sum((r - mean) ** 2 for r in daily_rets) / len(daily_rets)
        daily_vol = math.sqrt(var)
        annual_vol = daily_vol * math.sqrt(252) * 100.0
        return round(annual_vol, 2)

    def calc_sharpe(annual_ret, vol, risk_free=2.0):
        if vol is None or vol == 0 or annual_ret is None:
            return None
        return round((annual_ret - risk_free) / vol, 2)

    result = {}

    # Max drawdown for various periods
    for period, months in [("3m", 3), ("6m", 6), ("1y", 12), ("3y", 36), ("all", "all")]:
        pv, _ = get_period_data(months)
        result[f"max_drawdown_{period}"] = calc_max_drawdown(pv)

    # Annual return, volatility, sharpe, calmar for 1y and 3y
    min_days = {"1y": 200, "3y": 600}
    for period, months in [("1y", 12), ("3y", 36)]:
        pv, pd = get_period_data(months)
        trading_days = len(pv)

        if trading_days < min_days.get(period, 30):
            result[f"annual_return_{period}"] = None
            result[f"volatility_{period}"] = None
            result[f"sharpe_ratio_{period}"] = None
            result[f"calmar_ratio_{period}"] = None
            continue

        daily_rets = calc_daily_returns(pv)
        annual_ret = calc_annual_return(pv, trading_days)
        vol = calc_volatility(daily_rets)
        sharpe = calc_sharpe(annual_ret, vol)

        if vol is not None and vol > 500:
            result[f"annual_return_{period}"] = None
            result[f"volatility_{period}"] = None
            result[f"sharpe_ratio_{period}"] = None
            result[f"calmar_ratio_{period}"] = None
            continue

        result[f"annual_return_{period}"] = annual_ret
        result[f"volatility_{period}"] = vol
        result[f"sharpe_ratio_{period}"] = sharpe

        max_dd = result.get(f"max_drawdown_{period}")
        if annual_ret is not None and max_dd is not None and max_dd > 0:
            result[f"calmar_ratio_{period}"] = round(annual_ret / max_dd, 2)
        else:
            result[f"calmar_ratio_{period}"] = None

    return result


def _empty_risk_metrics() -> dict:
    return {
        "annual_return_1y": None, "annual_return_3y": None,
        "volatility_1y": None, "volatility_3y": None,
        "max_drawdown_3m": None, "max_drawdown_6m": None,
        "max_drawdown_1y": None, "max_drawdown_3y": None,
        "max_drawdown_all": None,
        "sharpe_ratio_1y": None, "sharpe_ratio_3y": None,
        "calmar_ratio_1y": None, "calmar_ratio_3y": None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_str(value) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _fmt_float4(value) -> Optional[str]:
    """Format float to 4 decimal places, matching legacy format."""
    if value is None:
        return None
    try:
        return f"{float(value):.4f}"
    except (ValueError, TypeError):
        return str(value)


def _safe_float(value) -> Optional[float]:
    """Convert value to float, returning None on failure."""
    if value is None or value == '' or value == '-':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _empty_legacy() -> dict:
    return {
        "basic_info": {"missing": True},
        "realtime_estimate": {"missing": True},
        "net_worth_trend": [],
        "accumulated_net_worth": [],
        "ranking_trend": [],
        "ranking_percentage": [],
        "portfolio": {"stock_codes": [], "bond_codes": []},
        "asset_allocation": {"categories": [], "series": []},
        "fund_managers": [],
        "performance": {},
        "subscription_redemption": {"categories": [], "series": []},
        "holder_structure": {"missing": True},
        "same_type_funds": {"missing": True},
        "risk_metrics": {"missing": True},
        "scale_fluctuation": {"missing": True},
        "position_trend": {"missing": True},
        "total_return_trend": {"missing": True},
        "performance_evaluation": {"missing": True},
        "cleaning_timestamp": None,
    }
