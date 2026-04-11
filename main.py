"""
main.py
-------
Entry point for the Momentum Delivery Bot — Daily Signal Scan.

Run manually at ~3 PM IST each trading day via GitHub Actions.

Lifecycle each run:
  1. Download latest daily price history for Nifty 200 universe
  2. Score and rank every stock using the momentum composite framework
  3. Load current open positions from positions.csv
  4. CHECK EXITS — sell any held stock that triggers a signal-driven exit:
       • Composite score  < 1.5        (momentum fading)
       • Signal turned SHORT           (direction reversal)
       • ROC20 < 0 AND price < 50D MA  (trend break — both required)
       • Position down ≥ 8%            (hard stop-loss)
       • Position up   ≥ 18%           (profit harvest)
  5. CHECK ENTRIES — buy any STRONG/MODERATE LONG signal not already held,
       up to PORTFOLIO_SIZE open positions
  6. Fire CNC delivery market orders via stocksdeveloper → Zerodha
  7. Save updated positions.csv and append to trade_log.csv
  8. GitHub Actions commits both files back to the repo

Orders fire at ~3 PM IST as MARKET orders (market closes 3:30 PM).
"""

import logging
import sys
from datetime import datetime

import pytz

from config import (
    ENTRY_QUALITY_FILTER,
    EXIT_COMPOSITE_FLOOR,
    HARD_STOP_PCT,
    MARKET_REGIME_FILTER,
    PORTFOLIO_SIZE,
    POSITION_SIZE_INR,
    PROFIT_TARGET_PCT,
)
from data_feed import fetch_universe_prices, get_regime_signal
from momentum_scorer import MomentumScore, print_ranked_table, rank_universe
from order_manager import buy_delivery, calculate_quantity, sell_delivery
from portfolio_state import DeliveryPosition, PortfolioState
from trade_logger import log_buy, log_sell, print_session_summary

# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

IST = pytz.timezone("Asia/Kolkata")


def ist_now() -> datetime:
    return datetime.now(IST)


# ---------------------------------------------------------------------------
# Exit logic
# ---------------------------------------------------------------------------

def check_exit_reason(
    pos:   DeliveryPosition,
    score: MomentumScore | None,
) -> str | None:
    """
    Evaluate one held position against all exit criteria.
    Returns a reason string if it should be sold, None to keep holding.
    """
    if score is None:
        return "DATA_GAP"

    current_price = score.current_price
    entry_price   = pos.entry_price

    # 1. Hard stop-loss
    loss_pct = (entry_price - current_price) / entry_price
    if loss_pct >= HARD_STOP_PCT:
        return f"HARD_STOP({loss_pct:.1%}_loss)"

    # 2. Profit target
    gain_pct = (current_price - entry_price) / entry_price
    if gain_pct >= PROFIT_TARGET_PCT:
        return f"PROFIT_TARGET({gain_pct:.1%}_gain)"

    # 3. Signal turned SHORT
    if score.signal == "SHORT":
        return "SIGNAL_SHORT"

    # 4. Momentum fading — composite below floor
    if score.composite < EXIT_COMPOSITE_FLOOR:
        return f"MOMENTUM_FADE(composite={score.composite:.2f})"

    # 5. Trend break — ROC20 negative AND price below 50D MA (both required)
    if score.roc20 < 0 and score.price_vs_ma50 < 0:
        return f"TREND_BREAK(roc20={score.roc20:.1f}%,vs_ma={score.price_vs_ma50:.1f}%)"

    return None  # keep holding


# ---------------------------------------------------------------------------
# Entry logic
# ---------------------------------------------------------------------------

def get_entry_candidates(
    ranked:    list[MomentumScore],
    portfolio: PortfolioState,
    bull_regime: bool,
) -> list[MomentumScore]:
    """
    Return new stocks to buy:
    - LONG signal
    - STRONG or MODERATE quality only (no WEAK)
    - GOOD or OK entry type (not CHASE — overextended)
    - Not already held
    - Fill up to PORTFOLIO_SIZE slots
    """
    if not bull_regime:
        logger.info("Bear regime — no new buys.")
        return []

    slots = PORTFOLIO_SIZE - portfolio.count()
    if slots <= 0:
        logger.info("Portfolio full — no new buys.")
        return []

    candidates = [
        s for s in ranked
        if s.signal == "LONG"
        and s.quality in ENTRY_QUALITY_FILTER
        and s.entry_type in ("GOOD", "OK")
        and not portfolio.has(s.symbol)
    ]
    return candidates[:slots]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    now = ist_now()
    logger.info("=" * 66)
    logger.info("  MOMENTUM DELIVERY BOT — DAILY SIGNAL SCAN")
    logger.info(f"  Run time  : {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info(f"  Portfolio : up to {PORTFOLIO_SIZE} stocks | ₹{POSITION_SIZE_INR:,.0f}/stock")
    logger.info(f"  Order type: CNC delivery (NORMAL)")
    logger.info(f"  Entry     : STRONG or MODERATE signals only")
    logger.info(f"  Exits     : composite<{EXIT_COMPOSITE_FLOOR} | SHORT signal | "
                f"trend break | stop {HARD_STOP_PCT:.0%} | target {PROFIT_TARGET_PCT:.0%}")
    logger.info("=" * 66)

    # ── 1. Price data ─────────────────────────────────────────────────────────
    logger.info("\n[1/5] Downloading price history…")
    prices_df = fetch_universe_prices()
    if prices_df.empty:
        logger.error("Price download failed. Aborting.")
        sys.exit(1)

    # ── 2. Market regime ──────────────────────────────────────────────────────
    logger.info("\n[2/5] Checking market regime…")
    bull_regime = get_regime_signal() if MARKET_REGIME_FILTER else True

    # ── 3. Score universe ─────────────────────────────────────────────────────
    logger.info("\n[3/5] Scoring and ranking Nifty 200 universe…")
    ranked   = rank_universe(prices_df)
    rank_map = {s.symbol: s for s in ranked}
    print_ranked_table(ranked, top_n=25)

    # ── 4. Load portfolio ─────────────────────────────────────────────────────
    logger.info("\n[4/5] Loading current portfolio…")
    portfolio = PortfolioState()
    logger.info(portfolio.summary())

    # ── 5. Scan: exits then entries ───────────────────────────────────────────
    logger.info("\n[5/5] Scanning for exits and entries…")

    session_sells = []
    session_buys  = []

    # ── EXITS ─────────────────────────────────────────────────────────────────
    for pos in portfolio.all():
        score  = rank_map.get(pos.symbol)
        reason = check_exit_reason(pos, score)

        if reason is None:
            logger.info(
                f"  HOLD {pos.symbol:<14} "
                f"composite={score.composite:.2f}  "
                f"gain/loss={(score.current_price - pos.entry_price) / pos.entry_price:+.1%}"
            )
            continue

        current_price = score.current_price if score else pos.entry_price
        ok = sell_delivery(pos.symbol, pos.quantity)
        if ok:
            log_sell(
                symbol      = pos.symbol,
                price       = current_price,
                quantity    = pos.quantity,
                entry_price = pos.entry_price,
                composite   = score.composite if score else 0.0,
                quality     = score.quality   if score else "N/A",
                reason      = reason,
            )
            session_sells.append({
                "symbol":      pos.symbol,
                "price":       current_price,
                "entry_price": pos.entry_price,
                "quantity":    pos.quantity,
                "pnl_inr":     round((current_price - pos.entry_price) * pos.quantity, 2),
                "pnl_pct":     round((current_price - pos.entry_price) / pos.entry_price * 100, 2),
                "reason":      reason,
            })
            portfolio.remove(pos.symbol)
        else:
            logger.error(f"  SELL FAILED {pos.symbol} — keeping in portfolio.")

    # ── ENTRIES ───────────────────────────────────────────────────────────────
    entries = get_entry_candidates(ranked, portfolio, bull_regime)

    if not entries:
        logger.info("  No new entries today.")
    else:
        logger.info(f"  {len(entries)} new entry candidate(s):")
        for s in entries:
            logger.info(
                f"    ↑ {s.symbol:<14} composite={s.composite:.2f}  "
                f"accel={s.acceleration:.2f}  quality={s.quality}  entry={s.entry_type}"
            )

    for score in entries:
        qty = calculate_quantity(score.current_price)
        if qty < 1:
            logger.warning(f"  {score.symbol}: price ₹{score.current_price:.2f} too high for allocation, skipping.")
            continue

        ok = buy_delivery(score.symbol, qty)
        if ok:
            log_buy(
                symbol    = score.symbol,
                price     = score.current_price,
                quantity  = qty,
                composite = score.composite,
                quality   = score.quality,
            )
            portfolio.add(
                symbol             = score.symbol,
                entry_price        = score.current_price,
                quantity           = qty,
                composite_at_entry = score.composite,
                quality_at_entry   = score.quality,
                entry_type         = score.entry_type,
            )
            session_buys.append({
                "symbol":    score.symbol,
                "price":     score.current_price,
                "quantity":  qty,
                "composite": score.composite,
                "quality":   score.quality,
            })
        else:
            logger.error(f"  BUY FAILED {score.symbol}.")

    # ── Save ──────────────────────────────────────────────────────────────────
    portfolio.save()

    # ── Summary ───────────────────────────────────────────────────────────────
    print_session_summary(session_buys, session_sells)

    logger.info("\nPortfolio after today's scan:")
    logger.info(portfolio.summary())

    if not session_sells and not session_buys:
        logger.info("\nNo trades today — portfolio unchanged.")

    logger.info("\nDone. positions.csv and trade_log.csv committed by workflow.")


if __name__ == "__main__":
    run()
