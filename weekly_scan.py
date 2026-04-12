"""
weekly_scan.py
--------------
Weekly Hard Stop Scanner — run every Friday before 3:30 PM IST.

Single purpose: scan all open delivery positions for the selected universe
and cut any that have breached the hard stop (down > HARD_STOP_PCT from entry).

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
  GitHub Actions UI: Actions → "Dual Momentum — Weekly Hard Stop Scan"
  Select universe (NIFTY200 or BEES) before running.

The workflow commits the updated positions and trade log if any stops fire.
"""

import argparse
import logging
import sys
from datetime import date, datetime

import pytz

from config import HARD_STOP_PCT
from data_feed import get_current_price
from order_manager import sell_delivery
from portfolio_state import PortfolioState
from trade_logger import log_sell
from universes import UniverseConfig, get_universe, list_universes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("weekly_scan")
IST = pytz.timezone("Asia/Kolkata")


def parse_args():
    p = argparse.ArgumentParser(description="Dual Momentum — Weekly Hard Stop Scan")
    p.add_argument(
        "--universe", "-u",
        choices=list_universes(),
        default="NIFTY200",
        help=(
            "Which universe to scan. "
            "NIFTY200 = Nifty 200 stock positions. "
            "BEES = BEES ETF positions. "
            "(default: NIFTY200)"
        ),
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview stop breaches without placing any sell orders or saving state.",
    )
    return p.parse_args()


# Warn on positions within this fraction of the stop (early warning system)
# e.g. if stop is 15%, warn when position is down > 10%
_WARN_THRESHOLD = HARD_STOP_PCT * 0.67


def run(dry_run: bool = False, universe_name: str = "NIFTY200") -> None:
    ucfg: UniverseConfig = get_universe(universe_name)
    now = datetime.now(IST)
    sep = "=" * 64

    logger.info(sep)
    if dry_run:
        logger.info(f"  WEEKLY HARD STOP SCANNER [{ucfg.name}] — DRY RUN")
    else:
        logger.info(f"  WEEKLY HARD STOP SCANNER [{ucfg.name}]")
    logger.info(f"  Universe   : {ucfg.display_name}")
    logger.info(f"  Scan time  : {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info(f"  Hard stop  : cut any position down > {HARD_STOP_PCT:.0%} from entry")
    logger.info(f"  Warning    : flag positions down > {_WARN_THRESHOLD:.0%} (approaching stop)")
    logger.info(f"  Positions  : {ucfg.positions_file}")
    logger.info("  Scope      : hard stop exits ONLY — no buys, no rank exits")
    if dry_run:
        logger.info("  *** DRY RUN — sell orders will NOT be sent to broker ***")
    logger.info(sep)

    portfolio = PortfolioState(positions_file=ucfg.positions_file)

    if portfolio.count() == 0:
        logger.info(f"\nPortfolio is empty ({ucfg.positions_file}) — nothing to scan.")
        return

    logger.info(f"\nScanning {portfolio.count()} open position(s)...\n")

    stops_fired:   list[dict] = []
    fetch_errors:  list[str]  = []

    for pos in sorted(portfolio.all(), key=lambda p: p.symbol):
        cur_price = get_current_price(pos.symbol)

        if cur_price is None:
            logger.warning(
                f"  [????] {pos.symbol:<16} — price fetch failed, "
                f"skipping (check manually)"
            )
            fetch_errors.append(pos.symbol)
            continue

        loss_pct  = (pos.entry_price - cur_price) / pos.entry_price
        gain_pct  = (cur_price - pos.entry_price) / pos.entry_price
        days_held = (date.today() - date.fromisoformat(pos.entry_date)).days
        pnl_inr   = round((cur_price - pos.entry_price) * pos.quantity, 2)

        if loss_pct >= HARD_STOP_PCT:
            # ── Hard stop breached ────────────────────────────────────────
            logger.warning(
                f"  [STOP] {pos.symbol:<16}  "
                f"entry=Rs{pos.entry_price:>8.2f}  now=Rs{cur_price:>8.2f}  "
                f"loss={loss_pct:.1%}  P&L=Rs{pnl_inr:+,.0f}  held={days_held}d"
            )
            if dry_run:
                logger.warning(
                    f"  [DRY ] Would SELL {pos.symbol} x{pos.quantity} "
                    f"@ Rs{cur_price:.2f} — order NOT sent"
                )
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
                        trade_log_file  = ucfg.trade_log_file,
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
                f"  [WARN] {pos.symbol:<16}  "
                f"entry=Rs{pos.entry_price:>8.2f}  now=Rs{cur_price:>8.2f}  "
                f"loss={loss_pct:.1%}  P&L=Rs{pnl_inr:+,.0f}  held={days_held}d  "
                f"(stop at {HARD_STOP_PCT:.0%})"
            )

        elif gain_pct >= 0:
            # ── Position profitable ───────────────────────────────────────
            logger.info(
                f"  [ OK ] {pos.symbol:<16}  "
                f"entry=Rs{pos.entry_price:>8.2f}  now=Rs{cur_price:>8.2f}  "
                f"gain={gain_pct:+.1%}  P&L=Rs{pnl_inr:+,.0f}  held={days_held}d"
            )
        else:
            # ── Position in drawdown but within stop ──────────────────────
            logger.info(
                f"  [HOLD] {pos.symbol:<16}  "
                f"entry=Rs{pos.entry_price:>8.2f}  now=Rs{cur_price:>8.2f}  "
                f"loss={loss_pct:.1%}  P&L=Rs{pnl_inr:+,.0f}  held={days_held}d"
            )

    # ── Persist changes if any stops fired (skipped in dry-run) ─────────────
    if stops_fired and not dry_run:
        portfolio.save()

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info(f"\n{sep}")
    logger.info(f"  SCAN SUMMARY [{ucfg.name}] — {date.today().isoformat()}")
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
                f"    XX {s['symbol']:<16}  "
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
        logger.warning(
            f"\n  Price fetch failures — check manually: {', '.join(fetch_errors)}"
        )

    if dry_run:
        logger.info("\n  *** DRY RUN COMPLETE — no orders sent, no files changed ***")

    logger.info(sep)


if __name__ == "__main__":
    args = parse_args()
    run(dry_run=args.dry_run, universe_name=args.universe)
