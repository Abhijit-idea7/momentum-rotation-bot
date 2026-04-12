"""
main.py
-------
Dual Momentum Delivery Bot — Monthly Rebalance.
Run on the LAST TRADING DAY of each month via GitHub Actions (manual trigger).

Strategy (Gary Antonacci's Dual Momentum, Nifty 200 adaptation):

  STEP 1 — ABSOLUTE MOMENTUM CHECK:
    Is Nifty 50's 12-month return (excluding last 21 days) > 6%?
    -> NO:   Sell ALL open positions. Stay in cash. Re-check next month.
    -> YES:  Proceed to relative momentum.

  STEP 2 — RELATIVE MOMENTUM (cross-sectional):
    Rank all Nifty 200 stocks by their 12M-1M return.
    SELL: holdings that have dropped below rank HOLD_BUFFER (20).
    BUY:  top TOP_N_HOLD (15) stocks not already held, filling vacant slots.

  RISK VALVE (monthly hard stop — safety net, not part of original DM):
    Also sell any position down > HARD_STOP_PCT (15%) from entry price.
    Protects against individual stock blow-ups between monthly rebalances.
"""

import logging
import sys
from datetime import date, datetime

import pytz

from config import (
    HARD_STOP_PCT,
    HOLD_BUFFER,
    MARKET_REGIME_FILTER,
    PORTFOLIO_SIZE,
    POSITION_SIZE_INR,
    TOP_N_HOLD,
)
from data_feed import fetch_universe_prices, get_absolute_momentum
from momentum_scorer import MomentumRank, print_ranked_table, rank_universe
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
# Exit evaluation
# ---------------------------------------------------------------------------

def check_exit_reason(
    pos:      DeliveryPosition,
    rank_map: dict[str, MomentumRank],
    risk_on:  bool,
) -> str | None:
    """
    Return an exit reason string if this position should be sold, else None.

    Exit triggers (in priority order):
      1. RISK_OFF     — absolute momentum failed; sell everything
      2. HARD_STOP    — position down >15% from entry (monthly safety check)
      3. RANK_EXIT    — stock dropped below HOLD_BUFFER rank
      4. NOT_RANKED   — stock dropped out of scoreable universe
    """
    # 1. Absolute momentum failed — global risk-off, sell all
    if not risk_on:
        return "RISK_OFF"

    cur = rank_map.get(pos.symbol)

    # 2. Stock no longer scoreable (too little data, delisted, etc.)
    if cur is None:
        return "NOT_RANKED"

    # 3. Hard stop — individual position down >15% from entry
    if HARD_STOP_PCT is not None:
        loss_pct = (pos.entry_price - cur.current_price) / pos.entry_price
        if loss_pct >= HARD_STOP_PCT:
            return f"HARD_STOP({loss_pct:.1%})"

    # 4. Rank has fallen below the hold buffer
    if cur.rank > HOLD_BUFFER:
        return f"RANK_EXIT(rank={cur.rank})"

    return None   # keep holding


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    now = ist_now()

    sep = "=" * 66
    logger.info(sep)
    logger.info("  DUAL MOMENTUM DELIVERY BOT — MONTHLY REBALANCE")
    logger.info(f"  Run time     : {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info(f"  Strategy     : Dual Momentum (Antonacci) — Nifty 200")
    logger.info(f"  Lookback     : 12M-1M (252 days, skip 21 days)")
    logger.info(f"  Portfolio    : top {TOP_N_HOLD} stocks | Rs{POSITION_SIZE_INR:,.0f}/slot")
    logger.info(f"  Hold buffer  : sell if rank > {HOLD_BUFFER}")
    logger.info(f"  Hard stop    : {HARD_STOP_PCT:.0%} from entry (monthly check)")
    logger.info(sep)

    # ── 1. Price data ─────────────────────────────────────────────────────────
    logger.info("\n[1/5] Downloading price history (3y)...")
    prices_df = fetch_universe_prices()
    if prices_df.empty:
        logger.error("Price download failed. Aborting.")
        sys.exit(1)

    # ── 2. Absolute momentum check ────────────────────────────────────────────
    logger.info("\n[2/5] Absolute momentum check (Nifty 50 12M-1M vs 6%)...")
    if MARKET_REGIME_FILTER:
        risk_on, abs_return = get_absolute_momentum()
    else:
        risk_on, abs_return = True, 0.0
        logger.info("  Regime filter disabled — treating as RISK-ON.")

    # ── 3. Rank universe ──────────────────────────────────────────────────────
    logger.info("\n[3/5] Ranking Nifty 200 by 12M-1M momentum...")
    ranked   = rank_universe(prices_df)
    rank_map = {r.symbol: r for r in ranked}
    print_ranked_table(ranked, top_n=25)

    if risk_on:
        logger.info(f"\n  RISK-ON: Nifty 12M-1M = {abs_return:+.1%} > 6% threshold")
        logger.info(f"  Holding targets: top {TOP_N_HOLD} stocks (sell if rank > {HOLD_BUFFER})")
    else:
        logger.info(f"\n  RISK-OFF: Nifty 12M-1M = {abs_return:+.1%} <= 6% threshold")
        logger.info("  Selling ALL positions — moving to cash until next month.")

    # ── 4. Load portfolio ─────────────────────────────────────────────────────
    logger.info("\n[4/5] Loading current portfolio...")
    portfolio = PortfolioState()
    logger.info(portfolio.summary())

    # ── 5. Rebalance ──────────────────────────────────────────────────────────
    logger.info("\n[5/5] Rebalancing...")
    session_sells: list[dict] = []
    session_buys:  list[dict] = []

    # ── EXITS ─────────────────────────────────────────────────────────────────
    for pos in portfolio.all():
        reason = check_exit_reason(pos, rank_map, risk_on)

        if reason is None:
            cur   = rank_map[pos.symbol]
            gain  = (cur.current_price - pos.entry_price) / pos.entry_price
            logger.info(
                f"  HOLD {pos.symbol:<14} rank=#{cur.rank:<4} "
                f"12M-1M={cur.momentum_return:+.1%}  unrealised={gain:+.1%}"
            )
            continue

        # Determine exit price
        cur       = rank_map.get(pos.symbol)
        cur_price = cur.current_price if cur else pos.entry_price
        mom_ret   = cur.momentum_return if cur else pos.momentum_return_at_entry
        cur_rank  = cur.rank if cur else 0

        ok = sell_delivery(pos.symbol, pos.quantity)
        if ok:
            log_sell(
                symbol          = pos.symbol,
                price           = cur_price,
                quantity        = pos.quantity,
                entry_price     = pos.entry_price,
                momentum_return = mom_ret,
                rank            = cur_rank,
                reason          = reason,
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
            portfolio.remove(pos.symbol)
        else:
            logger.error(f"  SELL FAILED {pos.symbol} — keeping in portfolio.")

    # ── ENTRIES (only if risk-on) ─────────────────────────────────────────────
    if not risk_on:
        logger.info("  RISK-OFF — no new buys this month.")
    else:
        slots = TOP_N_HOLD - portfolio.count()
        held  = portfolio.symbols()

        # Candidates: top TOP_N_HOLD by rank, not already held
        candidates = [r for r in ranked[:TOP_N_HOLD] if r.symbol not in held][:slots]

        if not candidates:
            logger.info("  No new entries — portfolio already at capacity or no candidates.")
        else:
            logger.info(f"  {len(candidates)} new entry candidate(s):")
            for r in candidates:
                logger.info(
                    f"    ^ {r.symbol:<14} rank=#{r.rank:<4} "
                    f"12M-1M={r.momentum_return:+.1%}  price=Rs{r.current_price:.2f}"
                )

        for r in candidates:
            qty = calculate_quantity(r.current_price)
            if qty < 1:
                logger.warning(
                    f"  {r.symbol}: price Rs{r.current_price:.2f} too high "
                    f"for Rs{POSITION_SIZE_INR:,.0f} allocation — skipping."
                )
                continue

            ok = buy_delivery(r.symbol, qty)
            if ok:
                log_buy(
                    symbol          = r.symbol,
                    price           = r.current_price,
                    quantity        = qty,
                    momentum_return = r.momentum_return,
                    rank            = r.rank,
                )
                portfolio.add(
                    symbol                   = r.symbol,
                    entry_price              = r.current_price,
                    quantity                 = qty,
                    momentum_return_at_entry = r.momentum_return,
                    rank_at_entry            = r.rank,
                )
                session_buys.append({
                    "symbol":          r.symbol,
                    "price":           r.current_price,
                    "quantity":        qty,
                    "momentum_return": r.momentum_return,
                    "rank":            r.rank,
                })
            else:
                logger.error(f"  BUY FAILED {r.symbol}.")

    # ── Save state ────────────────────────────────────────────────────────────
    portfolio.save()

    # ── Summary ───────────────────────────────────────────────────────────────
    print_session_summary(session_buys, session_sells)
    logger.info("\nPortfolio after rebalance:")
    logger.info(portfolio.summary())

    if not session_sells and not session_buys:
        logger.info("\nNo trades this month — portfolio unchanged.")

    logger.info("\nDone. positions.csv and trade_log.csv committed by workflow.")


if __name__ == "__main__":
    run()
