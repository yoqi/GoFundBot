from services.backtest_engine import run_strategy_backtest


def nav_series(values):
    return [
        {"date": f"2024-01-{idx + 1:02d}", "net_worth": value}
        for idx, value in enumerate(values)
    ]


def test_fixed_amount_daily_buys_each_trade_day():
    result = run_strategy_backtest(
        nav_series([1.0, 1.1, 1.2]),
        "fixed_amount",
        capital=10000,
        fee_rate=0,
        params={"frequency": "daily", "amount": 1000},
    )

    assert "error" not in result
    assert result["summary"]["buy_count"] == 3
    assert result["summary"]["total_invested"] == 3000
    assert result["trades"][0]["type"] == "buy"


def test_double_down_increases_amount_after_drop():
    result = run_strategy_backtest(
        nav_series([1.0, 0.98, 0.94, 0.93]),
        "double_down",
        capital=10000,
        fee_rate=0,
        params={
            "frequency": "daily",
            "base_amount": 1000,
            "drop_trigger_percent": 3,
            "multiplier": 2,
            "max_multiplier": 4,
        },
    )

    buy_amounts = [trade["amount"] for trade in result["trades"] if trade["type"] == "buy"]
    assert buy_amounts[0] == 1000
    assert max(buy_amounts) > 1000


def test_grid_buys_on_drop_and_sells_on_rebound():
    result = run_strategy_backtest(
        nav_series([1.0, 0.96, 0.93, 1.02]),
        "grid",
        capital=10000,
        fee_rate=0,
        params={
            "base_amount": 1000,
            "grid_step_percent": 3,
            "sell_profit_percent": 4,
            "max_consecutive_sell": 2,
            "start_condition": "immediate",
        },
    )

    trade_types = [trade["type"] for trade in result["trades"]]
    assert "buy" in trade_types
    assert "sell" in trade_types


def test_ma_timing_generates_trades_after_average_exists():
    values = [1.0] * 10 + [0.95, 0.94, 0.93, 1.05, 1.08]
    result = run_strategy_backtest(
        nav_series(values),
        "ma_timing",
        capital=10000,
        fee_rate=0,
        params={
            "frequency": "daily",
            "ma_days": 10,
            "base_amount": 1000,
            "below_ma_factor": 1.5,
            "above_ma_sell_percent": 20,
        },
    )

    assert result["summary"]["trade_count"] > 0
    assert any(signal["type"] == "buy" for signal in result["signals"])


def test_trend_timing_buys_in_uptrend():
    values = [1.0, 1.01, 1.02, 1.03, 1.05, 1.08, 1.1]
    result = run_strategy_backtest(
        nav_series(values),
        "trend_timing",
        capital=10000,
        fee_rate=0,
        params={
            "frequency": "daily",
            "lookback_days": 3,
            "trend_threshold_percent": 2,
            "base_amount": 1000,
        },
    )

    assert result["summary"]["buy_count"] > 0
    assert result["summary"]["total_invested"] > 0


def test_rocket_plan_accelerates_on_drawdown():
    result = run_strategy_backtest(
        nav_series([1.0, 0.98, 0.9, 0.86, 0.88]),
        "rocket_plan",
        capital=10000,
        fee_rate=0,
        params={
            "frequency": "daily",
            "base_amount": 500,
            "drop_trigger_percent": 5,
            "boost_factor": 1,
            "max_multiplier": 4,
        },
    )

    amounts = [trade["amount"] for trade in result["trades"] if trade["type"] == "buy"]
    assert amounts[0] == 500
    assert max(amounts) > 500


def test_ai_plan_can_buy_and_risk_off_sell():
    values = [1.0] * 20 + [1.03, 1.05, 1.07, 0.94, 0.9, 0.86]
    result = run_strategy_backtest(
        nav_series(values),
        "ai_plan",
        capital=10000,
        fee_rate=0,
        params={
            "frequency": "daily",
            "base_amount": 1000,
            "lookback_days": 3,
            "risk_off_sell_percent": 20,
        },
    )

    assert result["summary"]["trade_count"] > 0
    assert any(trade["type"] == "buy" for trade in result["trades"])


def test_dynamic_balance_starts_near_target_allocation():
    result = run_strategy_backtest(
        nav_series([1.0, 1.2, 1.3, 1.1]),
        "dynamic_balance",
        capital=10000,
        fee_rate=0,
        params={
            "frequency": "daily",
            "target_fund_percent": 60,
            "rebalance_threshold_percent": 5,
        },
    )

    assert result["trades"][0]["amount"] == 6000
    assert result["summary"]["trade_count"] > 1


def test_two_eight_rotation_adjusts_exposure():
    values = [1.0, 1.01, 1.02, 1.03, 1.06, 1.09, 1.12]
    result = run_strategy_backtest(
        nav_series(values),
        "two_eight_rotation",
        capital=10000,
        fee_rate=0,
        params={
            "frequency": "daily",
            "lookback_days": 3,
            "switch_threshold_percent": 1,
            "strong_target_percent": 80,
            "weak_target_percent": 20,
        },
    )

    assert result["summary"]["trade_count"] > 1
    assert any("二八轮动进攻" in trade["status"] for trade in result["trades"])


def test_buy_hold_buys_once():
    result = run_strategy_backtest(
        nav_series([1.0, 1.2, 1.4]),
        "buy_hold",
        capital=10000,
        fee_rate=0,
        params={},
    )

    assert result["summary"]["buy_count"] == 1
    assert result["summary"]["sell_count"] == 0
