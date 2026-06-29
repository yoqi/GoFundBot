from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class NavPoint:
    date: str
    nav: float


def run_strategy_backtest(
    nav_points: Iterable[Dict[str, Any]],
    strategy_type: str,
    capital: float,
    fee_rate: float = 0.0015,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = params or {}
    points = _normalize_nav_points(nav_points)
    if len(points) < 2:
        return {"error": "Insufficient net worth data for backtest"}

    strategy_type = strategy_type or "fixed_amount"
    if strategy_type not in {
        "fixed_amount",
        "double_down",
        "grid",
        "ma_timing",
        "trend_timing",
        "rocket_plan",
        "ai_plan",
        "dynamic_balance",
        "two_eight_rotation",
        "buy_hold",
        "kpi_analysis",
    }:
        return {"error": f"Unsupported strategy_type: {strategy_type}"}

    state = _new_state(capital)
    timeline: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    ctx: Dict[str, Any] = {
        "last_buy_nav": None,
        "last_period_key": None,
        "grid_base_nav": points[0].nav,
        "grid_level": 0,
        "consecutive_buy": 0,
        "consecutive_sell": 0,
    }

    ma_cache = _moving_averages(points, [10, 20, 60])

    for index, point in enumerate(points):
        date = point.date
        nav = point.nav
        before_trade_count = len(trades)

        if strategy_type == "fixed_amount":
            _strategy_fixed_amount(index, point, state, trades, signals, fee_rate, params, ctx)
        elif strategy_type == "double_down":
            _strategy_double_down(index, point, state, trades, signals, fee_rate, params, ctx)
        elif strategy_type == "grid":
            _strategy_grid(index, point, state, trades, signals, fee_rate, params, ctx)
        elif strategy_type == "ma_timing":
            _strategy_ma_timing(index, point, state, trades, signals, fee_rate, params, ctx, ma_cache)
        elif strategy_type == "trend_timing":
            _strategy_trend_timing(index, point, points, state, trades, signals, fee_rate, params, ctx, ma_cache)
        elif strategy_type == "rocket_plan":
            _strategy_rocket_plan(index, point, state, trades, signals, fee_rate, params, ctx)
        elif strategy_type == "ai_plan":
            _strategy_ai_plan(index, point, points, state, trades, signals, fee_rate, params, ctx, ma_cache)
        elif strategy_type == "dynamic_balance":
            _strategy_dynamic_balance(index, point, state, trades, signals, fee_rate, params, ctx)
        elif strategy_type == "two_eight_rotation":
            _strategy_two_eight_rotation(index, point, points, state, trades, signals, fee_rate, params, ctx, ma_cache)
        elif strategy_type == "buy_hold":
            _strategy_buy_hold(index, point, state, trades, signals, fee_rate, params)
        elif strategy_type == "kpi_analysis":
            _strategy_fixed_amount(index, point, state, trades, signals, fee_rate, params, ctx)

        trade_on_day = len(trades) > before_trade_count
        timeline.append(_snapshot(date, nav, state, trade_on_day))

    summary = _build_summary(points, timeline, trades, capital)
    metrics = {
        "holding_days": summary["holding_days"],
        "max_cost": summary["max_cost"],
        "cumulative_return": summary["total_return"],
        "average_invested": summary["average_invested"],
        "capital_return_rate": summary["return_rate"],
        "annual_return_rate": summary["annual_return"],
        "max_drawdown": summary["max_drawdown"],
        "average_capital_usage": summary["capital_usage_rate"],
        "buy_count": summary["buy_count"],
        "sell_count": summary["sell_count"],
    }

    return {
        "summary": summary,
        "timeline": timeline,
        "trades": trades,
        "signals": signals,
        "metrics": metrics,
        "strategy_config": {
            "strategy_type": strategy_type,
            "capital": round(capital, 2),
            "fee_rate": round(fee_rate * 100, 4),
            "params": params,
        },
    }


def _normalize_nav_points(raw_points: Iterable[Dict[str, Any]]) -> List[NavPoint]:
    points: List[NavPoint] = []
    for item in raw_points or []:
        date = item.get("date") or item.get("navDate") or item.get("trade_date")
        nav = item.get("net_worth")
        if nav is None:
            nav = item.get("nav")
        if not date or nav is None:
            continue
        try:
            date_text = str(date).split(" ")[0].replace("/", "-")
            if len(date_text) == 8 and date_text.isdigit():
                date_text = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}"
            points.append(NavPoint(date=date_text, nav=float(nav)))
        except (TypeError, ValueError):
            continue
    points.sort(key=lambda p: p.date)
    deduped: Dict[str, NavPoint] = {p.date: p for p in points if p.nav > 0}
    return [deduped[key] for key in sorted(deduped)]


def _new_state(capital: float) -> Dict[str, float]:
    capital = max(float(capital or 0), 0)
    return {
        "initial_capital": capital,
        "cash": capital,
        "shares": 0.0,
        "invested": 0.0,
        "fee": 0.0,
        "realized_cash": 0.0,
    }


def _buy(
    date: str,
    nav: float,
    amount: float,
    state: Dict[str, float],
    trades: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    fee_rate: float,
    reason: str,
) -> bool:
    amount = min(float(amount or 0), state["cash"])
    if amount <= 0 or nav <= 0:
        return False
    fee = amount * fee_rate
    actual = amount - fee
    shares = actual / nav
    state["cash"] -= amount
    state["shares"] += shares
    state["invested"] += amount
    state["fee"] += fee
    _append_trade(date, "buy", amount, fee, shares, nav, state, trades, reason)
    signals.append({"date": date, "type": "buy", "nav": round(nav, 4), "reason": reason})
    return True


def _sell(
    date: str,
    nav: float,
    shares: float,
    state: Dict[str, float],
    trades: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    fee_rate: float,
    reason: str,
) -> bool:
    shares = min(float(shares or 0), state["shares"])
    if shares <= 0 or nav <= 0:
        return False
    gross = shares * nav
    fee = gross * fee_rate
    amount = gross - fee
    state["shares"] -= shares
    state["cash"] += amount
    state["realized_cash"] += amount
    state["fee"] += fee
    _append_trade(date, "sell", amount, fee, -shares, nav, state, trades, reason)
    signals.append({"date": date, "type": "sell", "nav": round(nav, 4), "reason": reason})
    return True


def _append_trade(
    date: str,
    trade_type: str,
    amount: float,
    fee: float,
    share_delta: float,
    nav: float,
    state: Dict[str, float],
    trades: List[Dict[str, Any]],
    reason: str,
) -> None:
    market_value = state["shares"] * nav
    total_asset = state["cash"] + market_value
    total_return = total_asset - state.get("initial_capital", 0)
    return_rate = (total_return / state["invested"] * 100) if state["invested"] > 0 else 0
    trades.append({
        "index": len(trades) + 1,
        "date": date,
        "type": trade_type,
        "type_label": "买入" if trade_type == "buy" else "卖出",
        "amount": round(amount, 2),
        "fee": round(fee, 2),
        "share_delta": round(share_delta, 4),
        "holding_shares": round(state["shares"], 4),
        "cost": round(state["invested"], 2),
        "nav": round(nav, 4),
        "acc_nav": round(nav, 4),
        "value": round(market_value, 2),
        "cash": round(state["cash"], 2),
        "total_asset": round(total_asset, 2),
        "return": round(total_return, 2),
        "return_rate": round(return_rate, 2),
        "status": reason,
    })


def _snapshot(date: str, nav: float, state: Dict[str, float], trade_on_day: bool) -> Dict[str, Any]:
    value = state["shares"] * nav
    total_asset = state["cash"] + value
    total_return = total_asset - state["initial_capital"]
    invested = state["invested"]
    return_rate = (total_return / invested * 100) if invested > 0 else 0
    return {
        "date": date,
        "nav": round(nav, 4),
        "invested": round(invested, 2),
        "cash": round(state["cash"], 2),
        "shares": round(state["shares"], 4),
        "value": round(value, 2),
        "total_asset": round(total_asset, 2),
        "return": round(total_return, 2),
        "return_rate": round(return_rate, 2),
        "is_investment_day": trade_on_day,
        "status": "holding" if state["shares"] > 0 else "cash",
    }


def _strategy_fixed_amount(index, point, state, trades, signals, fee_rate, params, ctx):
    amount = _num(params, "amount", _num(params, "base_amount", 1000))
    if index == 0 and _num(params, "initial_amount", 0) > 0:
        _buy(point.date, point.nav, _num(params, "initial_amount", 0), state, trades, signals, fee_rate, "初始买入")

    frequency = params.get("frequency") or params.get("investment_type") or "monthly"
    if frequency == "lump_sum":
        if index == 0:
            _buy(point.date, point.nav, amount, state, trades, signals, fee_rate, "一次性买入")
        return

    if _period_due(point.date, frequency, params, ctx):
        _buy(point.date, point.nav, amount, state, trades, signals, fee_rate, "定额计划")


def _strategy_double_down(index, point, state, trades, signals, fee_rate, params, ctx):
    base_amount = _num(params, "base_amount", _num(params, "amount", 1000))
    trigger_percent = _num(params, "drop_trigger_percent", 3)
    multiplier = max(_num(params, "multiplier", 2), 1)
    max_multiplier = max(_num(params, "max_multiplier", 4), 1)
    frequency = params.get("frequency") or "monthly"

    if index == 0:
        if params.get("start_condition", "immediate") == "immediate":
            if _buy(point.date, point.nav, base_amount, state, trades, signals, fee_rate, "首次买入"):
                ctx["last_buy_nav"] = point.nav
        return

    if not _period_due(point.date, frequency, params, ctx):
        return

    last_nav = ctx.get("last_buy_nav") or point.nav
    drop = (last_nav - point.nav) / last_nav * 100 if last_nav else 0
    if drop >= trigger_percent:
        steps = min(math.floor(drop / trigger_percent), max_multiplier)
        amount = base_amount * min(multiplier ** steps, max_multiplier)
        reason = f"下跌{round(drop, 2)}%翻倍买入"
    else:
        amount = base_amount
        reason = "常规定投"
    if _buy(point.date, point.nav, amount, state, trades, signals, fee_rate, reason):
        ctx["last_buy_nav"] = point.nav


def _strategy_grid(index, point, state, trades, signals, fee_rate, params, ctx):
    amount = _num(params, "base_amount", 1000)
    grid_step = max(_num(params, "grid_step_percent", 3), 0.1) / 100
    sell_profit = max(_num(params, "sell_profit_percent", 5), 0.1) / 100
    max_buy = int(_num(params, "max_consecutive_buy", 99))
    max_sell = int(_num(params, "max_consecutive_sell", 99))
    base_nav = ctx["grid_base_nav"]

    if index == 0 and params.get("start_condition", "immediate") == "immediate":
        if _buy(point.date, point.nav, amount, state, trades, signals, fee_rate, "网格底仓"):
            ctx["last_buy_nav"] = point.nav
        return

    level = math.floor((point.nav - base_nav) / (base_nav * grid_step))
    if level < ctx["grid_level"] and ctx["consecutive_buy"] < max_buy:
        if _buy(point.date, point.nav, amount, state, trades, signals, fee_rate, "跌破网格买入"):
            ctx["grid_level"] = level
            ctx["consecutive_buy"] += 1
            ctx["consecutive_sell"] = 0
            ctx["last_buy_nav"] = point.nav
    elif state["shares"] > 0 and ctx.get("last_buy_nav") and point.nav >= ctx["last_buy_nav"] * (1 + sell_profit) and ctx["consecutive_sell"] < max_sell:
        sell_shares = min(state["shares"], amount / point.nav)
        if _sell(point.date, point.nav, sell_shares, state, trades, signals, fee_rate, "达到网格止盈"):
            ctx["grid_level"] = level
            ctx["consecutive_sell"] += 1
            ctx["consecutive_buy"] = 0


def _strategy_ma_timing(index, point, state, trades, signals, fee_rate, params, ctx, ma_cache):
    ma_days = int(_num(params, "ma_days", 20))
    base_amount = _num(params, "base_amount", 1000)
    buy_factor = _num(params, "below_ma_factor", 1.5)
    sell_percent = _num(params, "above_ma_sell_percent", 20) / 100
    frequency = params.get("frequency") or "weekly"
    ma = ma_cache.get(ma_days, [None] * (index + 1))[index]

    if ma is None:
        return
    if point.nav < ma and _period_due(point.date, frequency, params, ctx):
        _buy(point.date, point.nav, base_amount * buy_factor, state, trades, signals, fee_rate, f"低于MA{ma_days}加仓")
    elif point.nav > ma and sell_percent > 0 and state["shares"] > 0:
        _sell(point.date, point.nav, state["shares"] * sell_percent, state, trades, signals, fee_rate, f"高于MA{ma_days}减仓")


def _strategy_trend_timing(index, point, points, state, trades, signals, fee_rate, params, ctx, ma_cache):
    lookback = int(_num(params, "lookback_days", 20))
    base_amount = _num(params, "base_amount", 1000)
    weak_factor = _num(params, "weak_trend_factor", 0.5)
    sell_percent = _num(params, "downtrend_sell_percent", 15) / 100
    frequency = params.get("frequency") or "weekly"
    if index < lookback:
        return

    past_nav = points[index - lookback].nav
    trend_return = (point.nav - past_nav) / past_nav * 100 if past_nav else 0
    ma20 = ma_cache.get(20, [None] * (index + 1))[index]
    ma60 = ma_cache.get(60, [None] * (index + 1))[index]
    uptrend = trend_return >= _num(params, "trend_threshold_percent", 2) and (ma60 is None or ma20 is None or ma20 >= ma60)
    downtrend = trend_return <= -abs(_num(params, "downtrend_threshold_percent", 2))

    if uptrend and _period_due(point.date, frequency, params, ctx):
        _buy(point.date, point.nav, base_amount, state, trades, signals, fee_rate, "趋势向上买入")
    elif downtrend:
        if state["shares"] > 0 and sell_percent > 0:
            _sell(point.date, point.nav, state["shares"] * sell_percent, state, trades, signals, fee_rate, "趋势转弱减仓")
        elif _period_due(point.date, frequency, params, ctx):
            _buy(point.date, point.nav, base_amount * weak_factor, state, trades, signals, fee_rate, "弱趋势小额买入")


def _strategy_rocket_plan(index, point, state, trades, signals, fee_rate, params, ctx):
    base_amount = _num(params, "base_amount", 1000)
    trigger_percent = max(_num(params, "drop_trigger_percent", 5), 0.1)
    boost_factor = max(_num(params, "boost_factor", 1), 0)
    max_multiplier = max(_num(params, "max_multiplier", 5), 1)
    frequency = params.get("frequency") or "weekly"
    ctx["rocket_peak_nav"] = max(ctx.get("rocket_peak_nav") or point.nav, point.nav)

    if index == 0:
        if _buy(point.date, point.nav, base_amount, state, trades, signals, fee_rate, "火箭计划底仓"):
            ctx["last_buy_nav"] = point.nav
        return

    if not _period_due(point.date, frequency, params, ctx):
        return

    peak_nav = ctx.get("rocket_peak_nav") or point.nav
    drawdown = (peak_nav - point.nav) / peak_nav * 100 if peak_nav else 0
    if drawdown >= trigger_percent:
        stages = math.floor(drawdown / trigger_percent)
        multiplier = min(1 + stages * boost_factor, max_multiplier)
        reason = f"回撤{round(drawdown, 2)}%火箭加速"
    else:
        multiplier = 1
        reason = "火箭计划常规推进"
    if _buy(point.date, point.nav, base_amount * multiplier, state, trades, signals, fee_rate, reason):
        ctx["last_buy_nav"] = point.nav


def _strategy_ai_plan(index, point, points, state, trades, signals, fee_rate, params, ctx, ma_cache):
    base_amount = _num(params, "base_amount", 1000)
    frequency = params.get("frequency") or "weekly"
    dip_trigger = _num(params, "dip_trigger_percent", 4)
    dip_factor = _num(params, "dip_factor", 1.6)
    sell_percent = _num(params, "risk_off_sell_percent", 12) / 100
    lookback = int(_num(params, "lookback_days", 20))

    if index < 20:
        if index == 0:
            _buy(point.date, point.nav, base_amount, state, trades, signals, fee_rate, "智能计划底仓")
        return

    ma20 = ma_cache.get(20, [None] * (index + 1))[index]
    ma60 = ma_cache.get(60, [None] * (index + 1))[index]
    past_index = max(0, index - lookback)
    past_nav = points[past_index].nav
    trend_return = (point.nav - past_nav) / past_nav * 100 if past_nav else 0
    ctx["ai_peak_nav"] = max(ctx.get("ai_peak_nav") or point.nav, point.nav)
    peak_nav = ctx.get("ai_peak_nav") or point.nav
    drawdown = (peak_nav - point.nav) / peak_nav * 100 if peak_nav else 0

    if ma20 is not None and ma60 is not None and ma20 < ma60 and trend_return < 0:
        if state["shares"] > 0 and sell_percent > 0:
            _sell(point.date, point.nav, state["shares"] * sell_percent, state, trades, signals, fee_rate, "智能风控减仓")
        return

    if not _period_due(point.date, frequency, params, ctx):
        return

    if drawdown >= dip_trigger:
        _buy(point.date, point.nav, base_amount * dip_factor, state, trades, signals, fee_rate, "智能低位加仓")
    elif trend_return >= 0:
        _buy(point.date, point.nav, base_amount, state, trades, signals, fee_rate, "智能顺势买入")


def _strategy_dynamic_balance(index, point, state, trades, signals, fee_rate, params, ctx):
    target_percent = min(max(_num(params, "target_fund_percent", 60), 0), 100) / 100
    threshold = max(_num(params, "rebalance_threshold_percent", 5), 0.1) / 100
    frequency = params.get("frequency") or "monthly"
    total_asset = state["cash"] + state["shares"] * point.nav
    if total_asset <= 0:
        return

    if index == 0:
        _buy(point.date, point.nav, total_asset * target_percent, state, trades, signals, fee_rate, "动态平衡建仓")
        return

    if not _period_due(point.date, frequency, params, ctx):
        return

    fund_value = state["shares"] * point.nav
    current_percent = fund_value / total_asset
    target_value = total_asset * target_percent
    drift = current_percent - target_percent
    if drift > threshold:
        _sell(point.date, point.nav, (fund_value - target_value) / point.nav, state, trades, signals, fee_rate, "动态平衡卖出")
    elif drift < -threshold:
        _buy(point.date, point.nav, target_value - fund_value, state, trades, signals, fee_rate, "动态平衡买入")


def _strategy_two_eight_rotation(index, point, points, state, trades, signals, fee_rate, params, ctx, ma_cache):
    lookback = int(_num(params, "lookback_days", 20))
    strong_target = min(max(_num(params, "strong_target_percent", 80), 0), 100) / 100
    weak_target = min(max(_num(params, "weak_target_percent", 20), 0), 100) / 100
    switch_threshold = _num(params, "switch_threshold_percent", 1.5)
    frequency = params.get("frequency") or "weekly"
    if index < lookback:
        if index == 0:
            _buy(point.date, point.nav, state["cash"] * weak_target, state, trades, signals, fee_rate, "二八轮动防守底仓")
        return

    if not _period_due(point.date, frequency, params, ctx):
        return

    past_nav = points[index - lookback].nav
    momentum = (point.nav - past_nav) / past_nav * 100 if past_nav else 0
    ma20 = ma_cache.get(20, [None] * (index + 1))[index]
    ma60 = ma_cache.get(60, [None] * (index + 1))[index]
    target = strong_target if momentum >= switch_threshold and (ma60 is None or ma20 is None or ma20 >= ma60) else weak_target
    reason = "二八轮动进攻" if target == strong_target else "二八轮动防守"
    _rebalance_to_target(point.date, point.nav, target, state, trades, signals, fee_rate, reason)


def _strategy_buy_hold(index, point, state, trades, signals, fee_rate, params):
    if index != 0:
        return
    amount = _num(params, "amount", state["cash"])
    _buy(point.date, point.nav, amount, state, trades, signals, fee_rate, "买入持有基准")


def _rebalance_to_target(date, nav, target_percent, state, trades, signals, fee_rate, reason):
    total_asset = state["cash"] + state["shares"] * nav
    if total_asset <= 0:
        return False
    fund_value = state["shares"] * nav
    target_value = total_asset * target_percent
    diff = target_value - fund_value
    if abs(diff) < 1:
        return False
    if diff > 0:
        return _buy(date, nav, diff, state, trades, signals, fee_rate, reason)
    return _sell(date, nav, abs(diff) / nav, state, trades, signals, fee_rate, reason)


def _period_due(date_text: str, frequency: str, params: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    dt = datetime.strptime(date_text, "%Y-%m-%d")
    if frequency == "daily":
        return True
    if frequency == "weekly":
        target_weekday = int(_num(params, "weekday", _num(params, "investment_day", 0)))
        week_key = (dt.year, dt.isocalendar()[1])
        if dt.weekday() >= target_weekday and ctx.get("last_period_key") != ("w", week_key):
            ctx["last_period_key"] = ("w", week_key)
            return True
        return False
    month_key = (dt.year, dt.month)
    target_day = int(_num(params, "month_day", _num(params, "investment_day", 1)))
    if dt.day >= target_day and ctx.get("last_period_key") != ("m", month_key):
        ctx["last_period_key"] = ("m", month_key)
        return True
    return False


def _moving_averages(points: List[NavPoint], windows: List[int]) -> Dict[int, List[Optional[float]]]:
    result: Dict[int, List[Optional[float]]] = {}
    navs = [p.nav for p in points]
    for window in windows:
        values: List[Optional[float]] = []
        for idx in range(len(navs)):
            if idx + 1 < window:
                values.append(None)
            else:
                chunk = navs[idx + 1 - window:idx + 1]
                values.append(sum(chunk) / window)
        result[window] = values
    return result


def _build_summary(points: List[NavPoint], timeline: List[Dict[str, Any]], trades: List[Dict[str, Any]], capital: float) -> Dict[str, Any]:
    final = timeline[-1]
    start = datetime.strptime(points[0].date, "%Y-%m-%d")
    end = datetime.strptime(points[-1].date, "%Y-%m-%d")
    days = max((end - start).days, 0)
    invested_values = [item["invested"] for item in timeline]
    asset_values = [item["total_asset"] for item in timeline]
    average_invested = sum(invested_values) / len(invested_values) if invested_values else 0
    capital_usage = average_invested / capital * 100 if capital > 0 else 0
    return_rate = final["return_rate"]
    annual_return = 0
    if days > 0 and return_rate > -100:
        annual_return = ((1 + return_rate / 100) ** (365.25 / days) - 1) * 100

    peak = asset_values[0] if asset_values else 0
    max_drawdown = 0
    for value in asset_values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, (value - peak) / peak * 100)

    buy_count = sum(1 for trade in trades if trade["type"] == "buy")
    sell_count = sum(1 for trade in trades if trade["type"] == "sell")
    return {
        "total_capital": round(capital, 2),
        "total_invested": round(final["invested"], 2),
        "final_value": round(final["value"], 2),
        "cash": round(final["cash"], 2),
        "total_asset": round(final["total_asset"], 2),
        "total_return": round(final["return"], 2),
        "return_rate": round(return_rate, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "holding_days": days,
        "max_cost": round(max(invested_values) if invested_values else 0, 2),
        "average_invested": round(average_invested, 2),
        "capital_usage_rate": round(capital_usage, 2),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "investment_count": buy_count,
        "trade_count": len(trades),
        "fee": round(sum(trade["fee"] for trade in trades), 2),
    }


def _num(params: Dict[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
