"""Signal engine weights and confluence thresholds.

STATUS: UNVALIDATED DEFAULTS. These weights and thresholds are the ones
specified in the project brief, not backtested or calibrated against real
market data. Do not present any backtest run against these defaults as
evidence the strategy works — that would need a real, out-of-sample
validation you run yourself. They exist here, in config/, specifically so
you can retune them without touching engine code.
"""
from __future__ import annotations

from dataclasses import dataclass

# Points allocated to each factor. Sums to 100 by construction — the signal
# engine does not enforce or renormalize this if you edit it, so keep the
# sum meaningful when you retune.
FACTOR_WEIGHTS: dict[str, float] = {
    "htf_trend": 20.0,
    "smc": 20.0,
    "price_action": 15.0,
    "indicators": 15.0,
    "option_chain": 15.0,
    "volume_momentum": 10.0,
    "candlestick_chart": 5.0,
}


@dataclass(frozen=True)
class ConfluenceConfig:
    # A signal only fires if BOTH conditions hold — total score alone is not
    # enough, precisely so a single maxed-out factor (at most 20 points, well
    # under the threshold) can never alone produce a trade.
    total_score_threshold: float = 60.0  # out of 100 (i.e. out of sum(FACTOR_WEIGHTS))
    min_confluence_factors: int = 3  # minimum number of factors agreeing with the overall direction
    factor_min_strength: float = 0.3  # a factor only counts toward confluence if |raw_score| >= this


CONFLUENCE = ConfluenceConfig()

# Stop-loss distance = STOP_ATR_MULTIPLE * ATR(entry timeframe). Targets are
# expressed as R-multiples of that stop distance (T1/T2/T3).
STOP_ATR_MULTIPLE: float = 1.5
TARGET_R_MULTIPLES: tuple[float, float, float] = (1.0, 2.0, 3.0)
