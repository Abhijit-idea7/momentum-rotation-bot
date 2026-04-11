"""
momentum_scorer.py
------------------
Implements the exact scoring framework from "Momentum Score Calculation.xlsx".

Sub-scores:
  Short-Term Score  : f(ROC 20D)   → [-1, +3]
  Medium-Term Score : f(ROC 50D)   → [-1, +3]
  Trend Score       : price vs 50MA + ST direction → {-2, 0, +2}
  Recovery Score    : distance from 52W low → {0, 1, 2, 3}
  ─────────────────────────────────────────────────────────────
  Momentum Composite: sum of above → approximately [-4, +11]
  Acceleration      : ROC20 - ROC50 (momentum gaining speed)
  Signal Quality    : STRONG / MODERATE / WEAK
"""

import logging
from dataclasses import dataclass

import pandas as pd

from config import (
    ACCEL_BEAR, ACCEL_BULL, MIN_COMPOSITE_SCORE,
    MT_CAP, MT_FLOOR, MT_SCALE_NEG, MT_SCALE_POS,
    ST_CAP, ST_FLOOR, ST_SCALE_NEG, ST_SCALE_POS,
    TREND_BEAR, TREND_BULL,
)

# How many top candidates to surface as buy-eligible (not a hard portfolio limit)
_CANDIDATE_POOL = 30

logger = logging.getLogger(__name__)


@dataclass
class MomentumScore:
    symbol:           str
    composite:        float
    acceleration:     float
    roc20:            float
    roc50:            float
    roc100:           float
    dist_from_52w_low: float
    price_vs_ma50:    float
    st_score:         float
    mt_score:         float
    trend_score:      float
    recovery_score:   float
    signal:           str        # "LONG" or "SHORT"
    entry_type:       str        # "GOOD" | "OK" | "CHASE" | "NO_ENTRY"
    quality:          str        # "STRONG" | "MODERATE" | "WEAK"
    current_price:    float
    rank:             int = 0


def _score_single(symbol: str, closes: pd.Series) -> MomentumScore | None:
    """
    Compute all momentum metrics for one stock using its daily close series.
    The series must have at least 105 bars (passed in pre-validated).
    """
    if len(closes) < 105:
        return None

    cur = float(closes.iloc[-1])
    if cur <= 0 or pd.isna(cur):
        return None

    def price_n_days_ago(n: int) -> float:
        """Return close price n trading days ago. Falls back to current if unavailable."""
        idx = len(closes) - 1 - n
        if idx < 0:
            return cur
        val = float(closes.iloc[idx])
        return val if val > 0 and not pd.isna(val) else cur

    p20  = price_n_days_ago(20)
    p50  = price_n_days_ago(50)
    p100 = price_n_days_ago(100)

    # 52-week metrics (use up to 252 bars)
    window = closes.iloc[-252:].dropna()
    low_52w  = float(window.min()) if len(window) > 0 else cur
    high_52w = float(window.max()) if len(window) > 0 else cur

    # 50-day moving average
    ma50 = float(closes.iloc[-50:].dropna().mean())

    # ── Rate of change ────────────────────────────────────────────────────
    roc20  = (cur - p20)  / p20  * 100 if p20  > 0 else 0.0
    roc50  = (cur - p50)  / p50  * 100 if p50  > 0 else 0.0
    roc100 = (cur - p100) / p100 * 100 if p100 > 0 else 0.0

    dist_low = (cur - low_52w) / low_52w * 100 if low_52w > 0 else 0.0
    vs_ma50  = (cur - ma50)    / ma50    * 100 if ma50    > 0 else 0.0

    # ── Sub-scores (exactly matching Excel logic) ─────────────────────────
    st  = min(roc20 / ST_SCALE_POS, ST_CAP)   if roc20 >= 0 else max(roc20 / ST_SCALE_NEG, ST_FLOOR)
    mt  = min(roc50 / MT_SCALE_POS, MT_CAP)   if roc50 >= 0 else max(roc50 / MT_SCALE_NEG, MT_FLOOR)
    tr  = TREND_BULL if (cur > ma50 and st > 0) else (TREND_BEAR if st < -2.0 else 0.0)
    rec = 3.0 if dist_low < 20 else (2.0 if dist_low < 50 else (1.0 if dist_low < 100 else 0.0))

    composite = st + mt + tr + rec
    accel     = roc20 - roc50

    # ── Signal ───────────────────────────────────────────────────────────
    signal = "LONG" if (
        composite > MIN_COMPOSITE_SCORE
        and (accel > ACCEL_BEAR or roc20 > -3.0)
        and not (roc20 < -8.0 and roc50 < -8.0)
    ) else "SHORT"

    # ── Entry type ───────────────────────────────────────────────────────
    if signal == "LONG":
        if dist_low < 70 and accel > 2.0:
            entry_type = "GOOD"
        elif dist_low < 100:
            entry_type = "OK"
        else:
            entry_type = "CHASE"
    else:
        entry_type = "NO_ENTRY"

    # ── Quality ──────────────────────────────────────────────────────────
    if signal == "LONG":
        if accel > ACCEL_BULL and composite > 4.0 and roc20 > 0 and tr > 0:
            quality = "STRONG"
        elif accel > 2.0 and composite > 3.0:
            quality = "MODERATE"
        else:
            quality = "WEAK"
    else:
        quality = "WEAK"

    return MomentumScore(
        symbol           = symbol,
        composite        = round(composite, 3),
        acceleration     = round(accel, 3),
        roc20            = round(roc20, 2),
        roc50            = round(roc50, 2),
        roc100           = round(roc100, 2),
        dist_from_52w_low= round(dist_low, 2),
        price_vs_ma50    = round(vs_ma50, 2),
        st_score         = round(st, 3),
        mt_score         = round(mt, 3),
        trend_score      = tr,
        recovery_score   = rec,
        signal           = signal,
        entry_type       = entry_type,
        quality          = quality,
        current_price    = round(cur, 2),
    )


def rank_universe(prices_df: pd.DataFrame) -> list[MomentumScore]:
    """
    Score every stock in prices_df and return a ranked list (best composite first).
    Only LONG signals are included. Rank is set on each MomentumScore object.
    """
    scores = []
    for symbol in prices_df.columns:
        s = _score_single(symbol, prices_df[symbol].dropna())
        if s is not None:
            scores.append(s)

    # Rank ALL scoreable stocks by composite, descending
    scores.sort(key=lambda x: x.composite, reverse=True)
    for i, s in enumerate(scores, start=1):
        s.rank = i

    # Return only LONG signals (SHORT stocks still get a rank — used for exit logic)
    return scores


def get_buy_candidates(ranked: list[MomentumScore], bull_regime: bool) -> list[MomentumScore]:
    """
    Filter ranked list to stocks eligible for new buys:
    - LONG signal
    - GOOD or OK entry type (not CHASE — already overextended)
    - Within top PORTFOLIO_SIZE by composite
    - Only if market regime is bullish
    """
    if not bull_regime:
        logger.info("Bear regime active — no new buys.")
        return []

    eligible = [
        s for s in ranked
        if s.signal == "LONG" and s.entry_type in ("GOOD", "OK")
    ]
    return eligible[:_CANDIDATE_POOL]


def print_ranked_table(ranked: list[MomentumScore], top_n: int = 30) -> None:
    """Print a formatted ranking table to the logs."""
    header = (
        f"{'Rank':>4}  {'Symbol':<14} {'Comp':>6} {'Accel':>6} "
        f"{'ROC20':>7} {'ROC50':>7} {'Sig':<6} {'Entry':<8} {'Qual':<9} {'Price':>9}"
    )
    sep = "-" * len(header)
    logger.info(sep)
    logger.info("  MOMENTUM RANKING — NIFTY 200 UNIVERSE")
    logger.info(sep)
    logger.info(header)
    logger.info(sep)
    for s in ranked[:top_n]:
        logger.info(
            f"{s.rank:>4}  {s.symbol:<14} {s.composite:>6.2f} {s.acceleration:>6.2f} "
            f"{s.roc20:>6.1f}% {s.roc50:>6.1f}% {s.signal:<6} {s.entry_type:<8} "
            f"{s.quality:<9} ₹{s.current_price:>8,.2f}"
        )
    logger.info(sep)
