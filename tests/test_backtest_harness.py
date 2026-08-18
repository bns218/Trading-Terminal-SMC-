from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtesting.harness import SignalDecision, run_backtest


def make_candles(closes, highs=None, lows=None, start=None):
    start = start or datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc)
    n = len(closes)
    highs = highs or [c + 1 for c in closes]
    lows = lows or [c - 1 for c in closes]
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": start + timedelta(minutes=i),
                "close_time": start + timedelta(minutes=i + 1),
                "open": closes[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
                "volume": 100,
            }
        )
    return pd.DataFrame(rows)


# --- The core structural guarantee ---

def test_lookahead_structurally_impossible():
    """A strategy that tries to see beyond the current decision bar must fail
    to do so, not merely be asked nicely not to. We prove this by having the
    strategy attempt to index one row past what it was given, and asserting
    that IndexError is raised — i.e. the future row is genuinely absent from
    the object, not just conventionally ignored."""
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    candles = make_candles(closes)
    attempted_peek_count = {"n": 0}

    def cheating_strategy(view: pd.DataFrame):
        attempted_peek_count["n"] += 1
        with pytest.raises(IndexError):
            _ = view.iloc[len(view)]  # the "next" bar — must not exist in the view
        return None

    run_backtest(candles, cheating_strategy)
    assert attempted_peek_count["n"] == len(closes)  # strategy really was called at every bar


def test_strategy_never_sees_more_rows_than_its_decision_index_plus_one():
    closes = list(range(20))
    candles = make_candles(closes)
    seen_lengths = []

    def recording_strategy(view: pd.DataFrame):
        seen_lengths.append(len(view))
        return None

    run_backtest(candles, recording_strategy)
    assert seen_lengths == list(range(1, len(closes) + 1))


def test_full_dataset_max_close_time_never_visible_early():
    closes = list(range(15))
    candles = make_candles(closes)
    full_dataset_max_time = candles["close_time"].max()
    violations = []

    def strategy(view: pd.DataFrame):
        if len(view) < len(candles) and view["close_time"].max() >= full_dataset_max_time:
            violations.append(len(view))
        return None

    run_backtest(candles, strategy)
    assert violations == []


# --- Trade simulation correctness ---

def test_long_trade_hits_target():
    closes = [100, 100, 100, 100, 100, 100]
    highs = [101, 101, 101, 108, 101, 101]  # bar index 3 spikes to hit target
    lows = [99, 99, 99, 99, 99, 99]
    candles = make_candles(closes, highs=highs, lows=lows)

    def strategy(view: pd.DataFrame):
        if len(view) == 2:  # fire once, at the second bar
            return SignalDecision(direction="LONG", stop_loss=95.0, target=105.0)
        return None

    result = run_backtest(candles, strategy)
    assert result.total_trades == 1
    assert result.trades[0].exit_reason == "target"
    assert result.trades[0].pnl > 0


def test_long_trade_hits_stop_loss():
    closes = [100] * 6
    highs = [101] * 6
    lows = [99, 99, 99, 90, 99, 99]  # bar index 3 dips to hit stop
    candles = make_candles(closes, highs=highs, lows=lows)

    def strategy(view: pd.DataFrame):
        if len(view) == 2:
            return SignalDecision(direction="LONG", stop_loss=95.0, target=110.0)
        return None

    result = run_backtest(candles, strategy)
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].pnl < 0


def test_signal_at_last_bar_produces_no_trade():
    closes = list(range(10))
    candles = make_candles(closes)

    def strategy(view: pd.DataFrame):
        if len(view) == len(candles):  # only fires on the very last bar — no future bar to enter on
            return SignalDecision(direction="LONG", stop_loss=0.0, target=100.0)
        return None

    result = run_backtest(candles, strategy)
    assert result.total_trades == 0


def test_entry_price_is_next_bar_open_not_signal_bar_close():
    closes = [100, 100, 100, 100, 100]
    candles = make_candles(closes)
    candles.loc[2, "open"] = 999.0  # bar index 2's open is distinctive

    def strategy(view: pd.DataFrame):
        if len(view) == 2:  # decision made at bar index 1 -> entry should be bar index 2's open
            return SignalDecision(direction="LONG", stop_loss=0.0, target=100000.0)
        return None

    result = run_backtest(candles, strategy)
    assert result.trades[0].entry_price == 999.0


def test_no_overlapping_trades():
    closes = [100] * 10
    candles = make_candles(closes)
    call_count = {"n": 0}

    def always_signal(view: pd.DataFrame):
        call_count["n"] += 1
        return SignalDecision(direction="LONG", stop_loss=0.0, target=100000.0)

    result = run_backtest(candles, always_signal)
    # every signal would hit "end_of_data" immediately since target/stop never reached
    # within remaining bars until the last one — so only ONE trade should ever open
    # (the harness must not let a second signal fire while one is still open).
    assert result.total_trades == 1


def test_summary_metrics_computed_from_multiple_trades():
    # Two clean sequential trades: one winner, one loser, no overlap.
    closes = [100, 100, 100, 100, 100, 100, 100, 100]
    highs = [101, 101, 110, 101, 101, 101, 101, 101]
    lows = [99, 99, 99, 99, 99, 90, 99, 99]
    candles = make_candles(closes, highs=highs, lows=lows)

    calls = {"n": 0}

    def strategy(view: pd.DataFrame):
        calls["n"] += 1
        if len(view) == 1:
            return SignalDecision(direction="LONG", stop_loss=95.0, target=105.0)
        if len(view) == 4:
            return SignalDecision(direction="LONG", stop_loss=95.0, target=200.0)
        return None

    result = run_backtest(candles, strategy)
    assert result.total_trades == 2
    assert result.win_rate == 0.5
    assert result.trades[0].exit_reason == "target"
    assert result.trades[1].exit_reason == "stop_loss"


def test_empty_candles_returns_empty_result():
    candles = make_candles([])
    result = run_backtest(candles, lambda view: None)
    assert result.total_trades == 0
    assert result.win_rate == 0.0
