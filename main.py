"""
main.py
-------
Momentum Delivery Bot — Daily Signal Scan.
Run manually at ~3 PM IST each trading day via GitHub Actions.

Exit rules (signal-driven, evaluated in this order):
  1. Hard stop    — down ≥ 6% from entry              [always active, even day 1]
  2. Profit target— up  ≥ 18% from entry              [always active]
  3. Momentum fade— composite < floor                 [after MIN_HOLD_DAYS]
                    floor = 2.5 in bear regime, 1.5 in bull
  4. Trend break  — ROC20 < -3% AND price < 50D MA by >3%  [after MIN_HOLD_DAYS]
  (SIGNAL_SHORT removed — MOMENTUM_FADE at 1.5 covers composite-based exits cleanly)

Entry rules:
  • STRONG signal only (no MODERATE)
  • GOOD or OK entry type (not CHASE)
  • Not already held + slot available + bull regime
  • Not on re-entry cooldown (15 days after last exit)
"""

import logging
import sys
from datetime import date, datetime

import pytz

from config import (
    BEAR_COMPOSITE_FLOOR,
    ENTRY_QUALITY_FILTER,
    EXIT_COMPOSITE_FLOOR,
    HARD_STOP_PCT,
    MARKET_REGIME_FILTER,
    MIN_HOLD_DAYS,
    PORTFOLIO_SIZE,
    POSITION_SIZE_INR,
    PROFIT_TARGET_PCT,
    TREND_BREAK_MA_PCT,
    TREND_BREAK_ROC20,
)
from data_feed import fetch_universe_prices, get_regime_signal
from momentum_scorer import MomentumScore, print_ranked_table, rank_universe
from order_manager import buy_delivery, calculate_quantity, sell_delivery
from portfolio_state import DeliveryPosition, PortfolioState
from trade_logger import log_buy, log_sell, print_session_summary

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
    pos:        DeliveryPosition,
    score:      MomentumScore | None,
    bull_regime: bool,
) -> str | None:
    """
    Evaluate all exit criteria for one held position.
    Returns a reason string to sell, or None to keep holding.
    """
    if score is None:
        return "DATA_GAP"

    cur_price   = score.current_price
    entry_price = pos.entry_price
    days_held   = (date.today() - date.fromisoformat(pos.entry_date)).days

    loss_pct = (entry_price - cur_price) / entry_price
    gain_pct = (cur_price - entry_price) / entry_price

    # ── Always active (even on day 1) ──────────────────────────────────────
    if loss_pct >= HARD_STOP_PCT:
        return f"HARD_STOP({loss_pct:.1%})"

    if gain_pct >= PROFIT_TARGET_PCT:
        return f"PROFIT_TARGET({gain_pct:.1%})"

    # ── Soft exits — only after minimum hold period ────────────────────────
    if days_held < MIN_HOLD_DAYS:
        return None   # too early to judge trend/momentum signals

    # Bear regime uses a tighter composite floor (exit faster when macro weak)
    floor = EXIT_COMPOSITE_FLOOR if bull_regime else BEAR_COMPOSITE_FLOOR
    if score.composite < floor:
        return f"MOMENTUM_FADE(composite={score.composite:.2f},floor={floor})"

    if score.roc20 < TREND_BREAK_ROC20 and score.price_vs_ma50 < TREND_BREAK_MA_PCT:
        return f"TREND_BREAK(roc20={score.roc20:.1f}%,vs_ma={score.price_vs_ma50:.1f}%)"

    return None


# ---------------------------------------------------------------------------
# Entry logic
# ---------------------------------------------------------------------------

def get_entry_candidates(
    ranked:      list[MomentumScore],
    portfolio:   PortfolioState,
    bull_regime: bool,
) -> list[MomentumScore]:
    """
    Return new stocks to buy:
    - STRONG signal only
    - GOOD or OK entry type (not CHASE)
    - Not already held
    - Not on re-entry cooldown
    - Fill up to PORTFOLIO_SIZE slots
    """
    if not bull_regime:
        logger.info("Bear regime — no new buys.")
        return []

    slots = PORTFOLIO_SIZE - portfolio.count()
    if slots <= 0:
        logger.info("Portfolio full — no new buys.")
        return []

    candidates = []
    for s in ranked:
        if len(candidates) >= slots:
            break
        if s.signal != "LONG":
            continue
        if s.quality not in ENTRY_QUALITY_FILTER:
            continue
        if s.entry_type not in ("GOOD", "OK"):
            continue
        if portfolio.has(s.symbol):
            continue
        if portfolio.is_on_cooldown(s.symbol):
            remaining = portfolio.cooldown_days_remaining(s.symbol)
            logger.info(f"  SKIP {s.symbol} — cooldown ({remaining}d remaining)")
            continue
        candidates.append(s)

    return candidates


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    now = ist_now()
    composite_floor = EXIT_COMPOSITE_FLOOR  # will be overridden per position if bear

    logger.info("=" * 66)
    logger.info("  MOMENTUM DELIVERY BOT — DAILY SIGNAL SCAN")
    logger.info(f"  Run time    : {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info(f"  Portfolio   : up to {PORTFOLIO_SIZE} stocks | ₹{POSITION_SIZE_INR:,.0f}/stock")
    logger.info(f"  Entry filter: {', '.join(ENTRY_QUALITY_FILTER)} signals only")
    logger.info(f"  Hard stop   : {HARD_STOP_PCT:.0%}  |  Target : {PROFIT_TARGET_PCT:.0%}")
    logger.info(f"  Min hold    : {MIN_HOLD_DAYS} days  |  Cooldown: {15} days after exit")
    logger.info(f"  Bear floor  : {BEAR_COMPOSITE_FLOOR} (vs {EXIT_COMPOSITE_FLOOR} in bull)")
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

    # ── 5. Scan ───────────────────────────────────────────────────────────────
    logger.info("\n[5/5] Scanning exits then entries…")
    session_sells = []
    session_buys  = []

    # ── EXITS ─────────────────────────────────────────────────────────────────
    for pos in portfolio.all():
        score  = rank_map.get(pos.symbol)
        reason = check_exit_reason(pos, score, bull_regime)

        days_held = (date.today() - date.fromisoformat(pos.entry_date)).days

        if reason is None:
            gain_loss = (
                (score.current_price - pos.entry_price) / pos.entry_price
                if score else 0.0
            )
            logger.info(
                f"  HOLD {pos.symbol:<14} "
                f"composite={score.composite:.2f}  "
                f"held={days_held}d  "
                f"P&L={gain_loss:+.1%}"
            )
            continue

        cur_price = score.current_price if score else pos.entry_price
        ok = sell_delivery(pos.symbol, pos.quantity)
        if ok:
            log_sell(
                symbol      = pos.symbol,
                price       = cur_price,
                quantity    = pos.quantity,
                entry_price = pos.entry_price,
                composite   = score.composite if score else 0.0,
                quality     = score.quality   if score else "N/A",
                reason      = reason,
            )
            session_sells.append({
                "symbol":      pos.symbol,
                "price":       cur_price,
                "entry_price": pos.entry_price,
                "quantity":    pos.quantity,
                "pnl_inr":     round((cur_price - pos.entry_price) * pos.quantity, 2),
                "pnl_pct":     round((cur_price - pos.entry_price) / pos.entry_price * 100, 2),
                "reason":      reason,
            })
            portfolio.remove(pos.symbol)   # also records cooldown
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
                f"accel={s.acceleration:.2f}  [{s.quality}]  entry_type={s.entry_type}"
            )

    for score in entries:
        qty = calculate_quantity(score.current_price)
        if qty < 1:
            logger.warning(
                f"  {score.symbol}: ₹{score.current_price:.2f} too high for "
                f"₹{POSITION_SIZE_INR:,.0f} allocation, skipping."
            )
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

    logger.info("\nDone. positions.csv, cooldown.csv and trade_log.csv committed by workflow.")


if __name__ == "__main__":
    run()
