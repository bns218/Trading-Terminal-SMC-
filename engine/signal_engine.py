"""Signal engine: combines independent factor scores into BUY/SELL/NO_TRADE.

Pure function, no I/O — the caller is responsible for running the actual
analysis (strategies/indicators.py, strategies/smc.py, strategies/candlestick.py,
strategies/chart_patterns.py, strategies/options_analytics.py, etc.) and
distilling each factor down to a single -1..1 directional-strength score
before calling generate_signal(). This keeps the engine testable without
re-deriving the entire strategy stack for every test case, and keeps the
scoring/confluence logic (the actual "signal engine" contract) decoupled from
how any individual factor is computed.

NO_TRADE must be the common case. Confluence is enforced by TWO independent
conditions (both required, see config/signal_config.py::ConfluenceConfig):
  1. the total weighted score crosses `total_score_threshold`
  2. at least `min_confluence_factors` distinct factors have a same-direction
     raw_score of at least `factor_min_strength` magnitude
Condition 2 exists so a config edit that (accidentally or not) lowers the
total threshold can't alone let a single strong factor produce a trade —
confluence is structural, not just a score cutoff.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from config.signal_config import CONFLUENCE, FACTOR_WEIGHTS, STOP_ATR_MULTIPLE, TARGET_R_MULTIPLES, ConfluenceConfig
from data.models import SignalDirection, SubScore, TradeSignal


@dataclass(frozen=True)
class SignalInputs:
    """Caller-supplied, pre-computed per-factor scores. Each score is in
    [-1, 1]: positive = bullish evidence, negative = bearish evidence,
    magnitude = strength/confidence of that evidence. `reasons` supplies a
    human-readable explanation per factor key (falls back to a generic
    message if a factor's key is missing)."""

    instrument_token: str
    timestamp: datetime
    entry_price: Decimal
    atr: Decimal
    factor_scores: dict[str, float]  # keys should match FACTOR_WEIGHTS keys
    reasons: dict[str, str] = field(default_factory=dict)


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def generate_signal(
    inputs: SignalInputs,
    weights: dict[str, float] = FACTOR_WEIGHTS,
    confluence: ConfluenceConfig = CONFLUENCE,
    stop_atr_multiple: float = STOP_ATR_MULTIPLE,
    target_r_multiples: tuple[float, float, float] = TARGET_R_MULTIPLES,
) -> TradeSignal:
    sub_scores: list[SubScore] = []
    total = 0.0
    for factor, weight in weights.items():
        raw = _clamp(inputs.factor_scores.get(factor, 0.0))
        weighted = raw * weight
        total += weighted
        reason = inputs.reasons.get(factor, f"{factor}: no reason supplied")
        sub_scores.append(SubScore(factor=factor, raw_score=raw, weight=weight, weighted_score=weighted, reason=reason))

    confidence = abs(total)
    direction_sign = 1 if total > 0 else (-1 if total < 0 else 0)

    agreeing_factors = sum(
        1
        for s in sub_scores
        if direction_sign != 0 and (1 if s.raw_score > 0 else (-1 if s.raw_score < 0 else 0)) == direction_sign
        and abs(s.raw_score) >= confluence.factor_min_strength
    )

    meets_confluence = (
        direction_sign != 0
        and confidence >= confluence.total_score_threshold
        and agreeing_factors >= confluence.min_confluence_factors
    )

    if not meets_confluence:
        return TradeSignal(
            instrument_token=inputs.instrument_token,
            direction=SignalDirection.NO_TRADE,
            timestamp=inputs.timestamp,
            confidence=confidence,
            sub_scores=sub_scores,
            reasons=[
                f"Confluence not met: total score {total:.1f} (threshold {confluence.total_score_threshold}), "
                f"{agreeing_factors} agreeing factors (need {confluence.min_confluence_factors})"
            ],
        )

    direction = SignalDirection.BUY if direction_sign > 0 else SignalDirection.SELL
    entry = inputs.entry_price
    stop_distance = Decimal(str(stop_atr_multiple)) * inputs.atr

    if direction == SignalDirection.BUY:
        stop_loss = entry - stop_distance
        targets = [entry + Decimal(str(m)) * stop_distance for m in target_r_multiples]
    else:
        stop_loss = entry + stop_distance
        targets = [entry - Decimal(str(m)) * stop_distance for m in target_r_multiples]

    reasons = [s.reason for s in sub_scores if abs(s.raw_score) >= confluence.factor_min_strength and (1 if s.raw_score > 0 else -1) == direction_sign]

    invalidation = [
        f"Price closes beyond stop-loss level {stop_loss}",
        f"Overall confluence score reverses sign or drops below {confluence.total_score_threshold}",
    ]

    return TradeSignal(
        instrument_token=inputs.instrument_token,
        direction=direction,
        timestamp=inputs.timestamp,
        confidence=confidence,
        sub_scores=sub_scores,
        entry=entry,
        stop_loss=stop_loss,
        targets=targets,
        risk_reward=target_r_multiples[0],
        reasons=reasons,
        invalidation_conditions=invalidation,
    )
