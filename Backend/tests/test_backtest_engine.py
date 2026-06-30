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


def test_target_profit_plan_buys_more_after_drop_and_sells_last_lot():
    result = run_strategy_backtest(
        nav_series([1.0, 1.0, 0.96, 0.92, 0.99, 1.02]),
        "target_profit_plan",
        capital=10000,
        fee_rate=0,
        params={
            "plan_type": "manual",
            "profit_target_percent": 50,
            "buy_drop_percent": 3,
            "buy_amount": 1000,
            "buy_increase_percent": 10,
            "last_buy_rise_sell_percent": 6,
            "max_consecutive_sell": 2,
            "start_rule": "immediate",
            "min_trade_interval_days": 1,
        },
    )

    buy_amounts = [trade["amount"] for trade in result["trades"] if trade["type"] == "buy"]
    assert buy_amounts[:3] == [1000, 1100, 1210]
    assert any(trade["type"] == "sell" and "最后一笔" in trade["status"] for trade in result["trades"])


def test_target_profit_manual_restarts_after_profit_target():
    result = run_strategy_backtest(
        nav_series([1.0, 1.0, 1.12, 1.2, 1.3]),
        "target_profit_plan",
        capital=10000,
        fee_rate=0,
        params={
            "plan_type": "manual",
            "profit_target_percent": 10,
            "buy_amount": 1000,
            "start_rule": "immediate",
            "min_trade_interval_days": 1,
        },
    )

    assert any("盈利目标达成" in trade["status"] for trade in result["trades"])
    completed_trade = next(trade for trade in result["trades"] if "盈利目标达成" in trade["status"])
    assert completed_trade["cost"] == 1000
    assert completed_trade["return_rate"] == 12
    restart_trade = next(trade for trade in result["trades"] if "自动开启下一期" in trade["status"])
    assert restart_trade["date"] != completed_trade["date"]
    assert result["summary"]["completed_cycles"] >= 1
    assert restart_trade["type"] == "buy"


def test_target_profit_plan_ignores_fee_for_trade_math():
    result = run_strategy_backtest(
        nav_series([1.0, 0.95]),
        "target_profit_plan",
        capital=10000,
        fee_rate=0.0015,
        params={
            "plan_type": "manual",
            "profit_target_percent": 50,
            "buy_amount": 1000,
            "start_rule": "immediate",
            "min_trade_interval_days": 1,
        },
    )

    first_trade = result["trades"][0]
    assert first_trade["share_delta"] == 1000
    assert first_trade["return"] == 0
    assert first_trade["fee"] == 0


def test_target_profit_single_lot_waits_for_profit_target_after_restart():
    result = run_strategy_backtest(
        nav_series([1.0, 1.12, 1.0, 1.06, 1.11]),
        "target_profit_plan",
        capital=10000,
        fee_rate=0,
        params={
            "plan_type": "manual",
            "profit_target_percent": 10,
            "buy_amount": 1000,
            "last_buy_rise_sell_percent": 5,
            "start_rule": "immediate",
            "min_trade_interval_days": 1,
        },
    )

    trades = result["trades"]
    assert trades[0]["type"] == "buy"
    assert "盈利目标达成" in trades[1]["status"]
    assert "自动开启下一期" in trades[2]["status"]
    assert trades[3]["date"] == "2024-01-05"
    assert "盈利目标达成" in trades[3]["status"]
    assert not any(trade["date"] == "2024-01-04" and trade["type"] == "sell" for trade in trades)


def test_target_profit_auto_small_restarts_next_cycle():
    result = run_strategy_backtest(
        nav_series([1.0, 1.0, 1.12, 1.13, 1.14]),
        "target_profit_plan",
        capital=10000,
        fee_rate=0,
        params={
            "plan_type": "auto_small",
            "start_rule": "immediate",
            "min_trade_interval_days": 1,
        },
    )

    assert result["summary"]["completed_cycles"] >= 1
    assert any("自动开启下一期" in trade["status"] for trade in result["trades"])
    completed_trade = next(trade for trade in result["trades"] if "盈利目标达成" in trade["status"])
    restart_trade = next(trade for trade in result["trades"] if "自动开启下一期" in trade["status"])
    assert restart_trade["date"] != completed_trade["date"]
    assert result["summary"]["total_invested"] > 0
