from datetime import datetime, timezone
from decimal import Decimal

import pytest

from config.signal_config import ConfluenceConfig
from data.models import SignalDirection
from engine.signal_engine import SignalInputs, generate_signal

NOW = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)


def make_inputs(scores: dict, entry="24500", atr="50", reasons=None):
    return SignalInputs(
        instrument_token="26000",
        timestamp=NOW,
        entry_price=Decimal(entry),
        atr=Decimal(atr),
        factor_scores=scores,
        reasons=reasons or {},
    )


# Strong bullish confluence: htf_trend + smc + indicators + option_chain all
# fully bullish -> 20+20+15+15 = 70 >= 60 threshold, 4 agreeing factors >= 3.
STRONG_BULLISH = {
    "htf_trend": 1.0,
    "smc": 1.0,
    "price_action": 0.0,
    "indicators": 1.0,
    "option_chain": 1.0,
    "volume_momentum": 0.0,
    "candlestick_chart": 0.0,
}


def test_strong_confluence_produces_buy():
    signal = generate_signal(make_inputs(STRONG_BULLISH))
    assert signal.direction == SignalDirection.BUY
    assert signal.confidence == pytest.approx(70.0)
    assert len(signal.sub_scores) == 7  # every factor always represented


def test_strong_bearish_confluence_produces_sell():
    bearish = {k: -v for k, v in STRONG_BULLISH.items()}
    signal = generate_signal(make_inputs(bearish))
    assert signal.direction == SignalDirection.SELL


def test_single_maxed_factor_alone_never_produces_trade():
    """The core confluence guarantee: even a factor at its absolute maximum
    strength, alone, cannot reach the total-score threshold given these
    weights (max single factor = 20 < 60 threshold)."""
    scores = {k: 0.0 for k in STRONG_BULLISH}
    scores["htf_trend"] = 1.0
    signal = generate_signal(make_inputs(scores))
    assert signal.direction == SignalDirection.NO_TRADE
    assert signal.entry is None
    assert signal.stop_loss is None
    assert signal.targets == []


def test_two_maxed_factors_still_below_threshold():
    scores = {k: 0.0 for k in STRONG_BULLISH}
    scores["htf_trend"] = 1.0
    scores["smc"] = 1.0  # 20+20=40 < 60
    signal = generate_signal(make_inputs(scores))
    assert signal.direction == SignalDirection.NO_TRADE


def test_min_confluence_factor_count_enforced_independently_of_score():
    """Even when the total score clears the threshold, an explicit
    min_confluence_factors requirement higher than what's structurally
    achievable blocks the trade — proving condition 2 is checked, not just
    condition 1."""
    signal = generate_signal(
        make_inputs(STRONG_BULLISH),
        confluence=ConfluenceConfig(total_score_threshold=60.0, min_confluence_factors=5, factor_min_strength=0.3),
    )
    assert signal.direction == SignalDirection.NO_TRADE


def test_no_trade_is_more_common_with_weak_mixed_signals():
    mixed = {
        "htf_trend": 0.4, "smc": -0.3, "price_action": 0.2, "indicators": 0.1,
        "option_chain": -0.2, "volume_momentum": 0.3, "candlestick_chart": -0.1,
    }
    signal = generate_signal(make_inputs(mixed))
    assert signal.direction == SignalDirection.NO_TRADE


def test_no_trade_reasons_populated():
    signal = generate_signal(make_inputs({k: 0.0 for k in STRONG_BULLISH}))
    assert signal.direction == SignalDirection.NO_TRADE
    assert len(signal.reasons) == 1
    assert "Confluence not met" in signal.reasons[0]


def test_buy_entry_stop_targets_arithmetic():
    signal = generate_signal(make_inputs(STRONG_BULLISH, entry="24500", atr="50"))
    assert signal.entry == Decimal("24500")
    assert signal.stop_loss == Decimal("24500") - Decimal("1.5") * Decimal("50")  # 24425
    stop_distance = Decimal("75")
    assert signal.targets == [
        Decimal("24500") + Decimal("1") * stop_distance,
        Decimal("24500") + Decimal("2") * stop_distance,
        Decimal("24500") + Decimal("3") * stop_distance,
    ]
    assert signal.risk_reward == 1.0


def test_sell_entry_stop_targets_arithmetic():
    bearish = {k: -v for k, v in STRONG_BULLISH.items()}
    signal = generate_signal(make_inputs(bearish, entry="24500", atr="50"))
    assert signal.stop_loss == Decimal("24500") + Decimal("75")  # entry + 1.5*atr
    stop_distance = Decimal("75")
    assert signal.targets[0] == Decimal("24500") - stop_distance


def test_raw_score_clamped_outside_range():
    scores = {k: 0.0 for k in STRONG_BULLISH}
    scores["htf_trend"] = 5.0  # way outside [-1,1]
    signal = generate_signal(make_inputs(scores))
    htf_sub = next(s for s in signal.sub_scores if s.factor == "htf_trend")
    assert htf_sub.raw_score == 1.0
    assert htf_sub.weighted_score == 20.0


def test_reasons_only_include_agreeing_factors_above_threshold():
    scores = dict(STRONG_BULLISH)
    scores["candlestick_chart"] = 0.1  # below factor_min_strength (0.3), should be excluded from reasons
    reasons_map = {
        "htf_trend": "HTF EMA stack bullish",
        "smc": "Bullish BOS confirmed",
        "indicators": "RSI rising through 50",
        "option_chain": "PCR favors calls",
        "candlestick_chart": "weak doji",
    }
    signal = generate_signal(make_inputs(scores, reasons=reasons_map))
    assert "weak doji" not in signal.reasons
    assert "HTF EMA stack bullish" in signal.reasons


def test_invalidation_conditions_present_for_trade_signal():
    signal = generate_signal(make_inputs(STRONG_BULLISH))
    assert len(signal.invalidation_conditions) >= 1
    assert any("stop-loss" in c.lower() for c in signal.invalidation_conditions)


def test_confidence_zero_when_all_factors_neutral():
    signal = generate_signal(make_inputs({k: 0.0 for k in STRONG_BULLISH}))
    assert signal.confidence == 0.0
