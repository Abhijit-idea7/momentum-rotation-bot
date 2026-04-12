"""
weekly_scan.py
--------------
Weekly Hard Stop Scanner — run every Friday before 3:30 PM IST.

Single purpose: scan all open delivery positions and cut any that have
breached the hard stop (down > HARD_STOP_PCT from entry price).

Does NOT:
  - Download or rank the full universe
  - Exit positions based on rank or momentum fade
  - Open any new positions
  - Run any momentum logic

Those decisions belong to main.py (monthly rebalance).
This scanner is purely a circuit breaker — it limits how much damage
a single position can do between monthly rebalances.

Without this: a stock falling 30% in 3 weeks is only caught at month-end,
locking in the full 30% loss.
With this: the stop fires the first Friday the breach is detected, typically
limiting the loss to 15-20% depending on how fast the stock moved.

How to use:
  Run manually via GitHub Actions every Friday ~3 PM IST.
  Or let the scheduled cron in weekly_stop.yml run it automatically.
  The workflow commits updated positions.csv + trade_log.csv if any stops fire.
"""

import logging
import sys
from datetime import date, datetime

import pytz

from config import HARD_STOP_PCT
from data_feed import get_current_price
from order_manager import sell_delivery
from portfolio_state import PortfolioState
from trade_logger import log_sell

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("weekly_scan")
IST = pytz.timezone("Asia/Kolkata")

# Warn on positions within this fraction of the stop (early warning system)
# e.g. if stop is 15%, warn when position is down > 10%
_WARN_THRESHOLD = HARD_STOP_PCT * 0.67


def run() -> None:
    now = datetime.now(IST)
    sep = "=" * 64

    logger.info(sep)
    logger.info("  WEEKLY HARD STOP SCANNER")
    logger.info(f"  Scan time  : {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info(f"  Hard stop  : cut any position down > {HARD_STOP_PCT:.0%} from entry")
    logger.info(f"  Warning    : flag positions down > {_WARN_THRESHOLD:.0%} (approaching stop)")
    logger.info("  Scope      : hard stop exits ONLY — no buys, no rank exits")
    logger.info(sep)

    portfolio = PortfolioState()

    if portfolio.count() == 0:
        logger.info("\nPortfolio is empty — nothing to scan.")
        return

    logger.info(f"\nScanning {portfolio.count()} open position(s)...\n")

    stops_fired:   list[dict] = []
    fetch_errors:  list[str]  = []

    for pos in sorted(portfolio.all(), key=lambda p: p.symbol):
        cur_price = get_current_price(pos.symbol)

        if cur_price is None:
            logger.warning(f"  [????] {pos.symbol:<14} — price fetch failed, skipping (check manually)")
            fetch_errors.append(pos.symbol)
            continue

        loss_pct  = (pos.entry_price - cur_price) / pos.entry_price
        gain_pct  = (cur_price - pos.entry_price) / pos.entry_price
        days_held = (date.today() - date.fromisoformat(pos.entry_date)).days
        pnl_inr   = round((cur_price - pos.entry_price) * pos.quantity, 2)

        if loss_pct >= HARD_STOP_PCT:
            # ── Hard stop breached — fire sell order ──────────────────────
            logger.warning(
                f"  [STOP] {pos.symbol:<14}  "
                f"entry=Rs{pos.entry_price:>8.2f}  now=Rs{cur_price:>8.2f}  "
                f"loss={loss_pct:.1%}  P&L=Rs{pnl_inr:+,.0f}  held={days_held}d"
            )
            ok = sell_delivery(pos.symbol, pos.quantity)
            if ok:
                log_sell(
                    symbol          = pos.symbol,
                    price           = cur_price,
                    quantity        = pos.quantity,
                    entry_price     = pos.entry_price,
                    momentum_return = pos.momentum_return_at_entry,
                    rank            = pos.rank_at_entry,
                    reason          = f"WEEKLY_HARD_STOP({loss_pct:.1%})",
                )
                portfolio.remove(pos.symbol)
                stops_fired.append({
                    "symbol":      pos.symbol,
                    "entry_price": pos.entry_price,
                    "exit_price":  cur_price,
                    "quantity":    pos.quantity,
                    "loss_pct":    loss_pct,
                    "pnl_inr":     pnl_inr,
                    "days_held":   days_held,
                })
            else:
                logger.error(
                    f"  [FAIL] Sell order FAILED for {pos.symbol} — "
                    f"position kept. Check broker and retry manually."
                )

        elif loss_pct >= _WARN_THRESHOLD:
            # ── Approaching stop — flag for attention ─────────────────────
            logger.warning(
                f"  [WARN] {pos.symbol:<14}  "
                f"entry=Rs{pos.entry_price:>8.2f}  now=Rs{cur_price:>8.2f}  "
                f"loss={loss_pct:.1%}  P&L=Rs{pnl_inr:+,.0f}  held={days_held}d  "
                f"(stop at {HARD_STOP_PCT:.0%})"
            )

        elif gain_pct >= 0:
            # ── Position profitable ───────────────────────────────────────
            logger.info(
                f"  [ OK ] {pos.symbol:<14}  "
                f"entry=Rs{pos.entry_price:>8.2f}  now=Rs{cur_price:>8.2f}  "
                f"gain={gain_pct:+.1%}  P&L=Rs{pnl_inr:+,.0f}  held={days_held}d"
            )
        else:
            # ── Position in drawdown but within stop ──────────────────────
            logger.info(
                f"  [HOLD] {pos.symbol:<14}  "
                f"entry=Rs{pos.entry_price:>8.2f}  now=Rs{cur_price:>8.2f}  "
                f"loss={loss_pct:.1%}  P&L=Rs{pnl_inr:+,.0f}  held={days_held}d"
            )

    # ── Persist changes if any stops fired ───────────────────────────────────
    if stops_fired:
        portfolio.save()

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info(f"\n{sep}")
    logger.info(f"  SCAN SUMMARY — {date.today().isoformat()}")
    logger.info(sep)
    logger.info(f"  Positions scanned  : {portfolio.count() + len(stops_fired)}")
    logger.info(f"  Hard stops fired   : {len(stops_fired)}")
    logger.info(f"  Price fetch errors : {len(fetch_errors)}")

    if stops_fired:
        total_loss = sum(s["pnl_inr"] for s in stops_fired)
        logger.info(f"  Total realised P&L : Rs{total_loss:+,.0f}")
        logger.info(f"\n  Positions cut this week:")
        for s in stops_fired:
            logger.info(
                f"    XX {s['symbol']:<14}  "
                f"Rs{s['entry_price']:.2f} -> Rs{s['exit_price']:.2f}  "
                f"loss={s['loss_pct']:.1%}  P&L=Rs{s['pnl_inr']:+,.0f}  "
                f"held={s['days_held']}d"
            )
        logger.info(
            f"\n  {len(stops_fired)} slot(s) now empty — "
            f"will be refilled at next monthly rebalance."
        )
    else:
        logger.info("\n  No stops triggered — all positions within threshold.")

    if fetch_errors:
        logger.warning(f"\n  Price fetch failures — check manually: {', '.join(fetch_errors)}")

    logger.info(sep)


if __name__ == "__main__":
    run()
