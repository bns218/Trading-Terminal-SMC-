"""Walk-forward backtest harness with structural look-ahead prevention.

The core guarantee: at decision index i, the strategy function receives
`candles.iloc[:i+1]` — a real pandas slice, not merely "the caller promises
not to peek." Row i+1 onward does not exist in the object handed to the
strategy: `df.iloc[i+1]` raises IndexError, `df["close_time"].max()` cannot
exceed bar i's timestamp. A strategy that tries to cheat by looking beyond
what it was given fails immediately rather than silently succeeding — see
tests/test_backtest_harness.py::test_lookahead_structurally_impossible for
the proof.

Trade simulation after a signal fires is NOT look-ahead: evaluating what
happens to an already-placed order in bars i+1, i+2, ... is exactly what a
backtest is for. What's forbidden is the DECISION at bar i using information
from bar i+1 or later — and that's what's structurally blocked here.

This harness is intentionally minimal in Phase 3: a same-direction single
open trade at a time, entry at next bar's open (never the deciding bar's own
close, which would be an optimistic fill), exit on stop-loss/target/end of
data. It does not yet model brokerage/slippage/lot sizing — that's Phase 7's
PaperExecutor, which this harness is designed to plug into later without
changing the walk-forward/no-lookahead core.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Literal, Optional

import pandas as pd

Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class SignalDecision:
    direction: Direction
    stop_loss: float
    target: float
    reason: str = ""


StrategyFn = Callable[[pd.DataFrame], Optional[SignalDecision]]


@dataclass
class BacktestTrade:
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    direction: Direction
    stop_loss: float
    target: float
    exit_reason: str  # "stop_loss" | "target" | "end_of_data"
    pnl: float = field(init=False)
    r_multiple: float = field(init=False)

    def __post_init__(self) -> None:
        sign = 1 if self.direction == "LONG" else -1
        self.pnl = sign * (self.exit_price - self.entry_price)
        risk = abs(self.entry_price - self.stop_loss)
        self.r_multiple = self.pnl / risk if risk > 0 else 0.0


@dataclass
class BacktestResult:
    trades: list[BacktestTrade]
    total_trades: int
    win_rate: float
    avg_r: float
    max_drawdown: float
    profit_factor: float
    equity_curve: list[float]


def _simulate_trade(candles: pd.DataFrame, entry_index: int, decision: SignalDecision) -> BacktestTrade:
    entry_bar = candles.iloc[entry_index]
    entry_price = float(entry_bar["open"])
    is_long = decision.direction == "LONG"

    for j in range(entry_index, len(candles)):
        bar = candles.iloc[j]
        hit_stop = bar["low"] <= decision.stop_loss if is_long else bar["high"] >= decision.stop_loss
        hit_target = bar["high"] >= decision.target if is_long else bar["low"] <= decision.target
        if hit_stop and hit_target:
            # Conservative assumption: if both could have happened intrabar, assume the
            # worse outcome (stop) — we don't have intrabar sequencing from OHLC alone.
            return BacktestTrade(
                entry_bar["close_time"], entry_price, bar["close_time"], decision.stop_loss,
                decision.direction, decision.stop_loss, decision.target, "stop_loss",
            )
        if hit_stop:
            return BacktestTrade(
                entry_bar["close_time"], entry_price, bar["close_time"], decision.stop_loss,
                decision.direction, decision.stop_loss, decision.target, "stop_loss",
            )
        if hit_target:
            return BacktestTrade(
                entry_bar["close_time"], entry_price, bar["close_time"], decision.target,
                decision.direction, decision.stop_loss, decision.target, "target",
            )

    last_bar = candles.iloc[-1]
    return BacktestTrade(
        entry_bar["close_time"], entry_price, last_bar["close_time"], float(last_bar["close"]),
        decision.direction, decision.stop_loss, decision.target, "end_of_data",
    )


def run_backtest(candles: pd.DataFrame, strategy_fn: StrategyFn) -> BacktestResult:
    """Walk forward one bar at a time. At bar i, strategy_fn sees ONLY
    candles.iloc[:i+1]. A signal at bar i is executed at bar i+1's open.
    Only one open trade at a time (a new signal is ignored while a trade is
    open, to keep this Phase 3 harness simple — Phase 6's risk manager owns
    real position-limit logic later).
    """
    trades: list[BacktestTrade] = []
    in_trade_until_index = -1

    for i in range(len(candles)):
        if i <= in_trade_until_index:
            continue
        view = candles.iloc[: i + 1]
        decision = strategy_fn(view)
        if decision is None:
            continue
        entry_index = i + 1
        if entry_index >= len(candles):
            continue  # no future bar to execute on — cannot evaluate, so skip (not a lookahead violation: we simply have no outcome to report)
        trade = _simulate_trade(candles, entry_index, decision)
        trades.append(trade)
        exit_index = candles.index[candles["close_time"] == trade.exit_time]
        in_trade_until_index = int(exit_index[0]) if len(exit_index) else entry_index

    return _summarize(trades)


def _summarize(trades: list[BacktestTrade]) -> BacktestResult:
    if not trades:
        return BacktestResult([], 0, 0.0, 0.0, 0.0, 0.0, [])

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / len(trades)
    avg_r = sum(t.r_multiple for t in trades) / len(trades)
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    equity_curve = []
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        running += t.pnl
        equity_curve.append(running)
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    return BacktestResult(
        trades=trades,
        total_trades=len(trades),
        win_rate=win_rate,
        avg_r=avg_r,
        max_drawdown=max_dd,
        profit_factor=profit_factor,
        equity_curve=equity_curve,
    )
