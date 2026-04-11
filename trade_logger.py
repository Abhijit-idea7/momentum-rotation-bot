"""
trade_logger.py
---------------
Appends every BUY/SELL trade to trade_log.csv which is committed to the repo.
Provides full history of all rebalance actions and estimated P&L on sells.

CSV columns:
  date, symbol, action, price, quantity, value_inr,
  reason, composite_score, quality, pnl_inr, pnl_pct
"""

import csv
import logging
from datetime import date
from pathlib import Path

from config import TRADE_LOG_FILE

logger = logging.getLogger(__name__)

_FIELDS = [
    "date", "symbol", "action", "price", "quantity", "value_inr",
    "reason", "composite_score", "quality", "pnl_inr", "pnl_pct",
]


def log_buy(
    symbol:    str,
    price:     float,
    quantity:  int,
    composite: float,
    quality:   str,
    reason:    str = "NEW_ENTRY",
) -> None:
    _append_row(
        symbol    = symbol,
        action    = "BUY",
        price     = price,
        quantity  = quantity,
        reason    = reason,
        composite = composite,
        quality   = quality,
        pnl_inr   = 0.0,
        pnl_pct   = 0.0,
    )


def log_sell(
    symbol:        str,
    price:         float,
    quantity:      int,
    entry_price:   float,
    composite:     float,
    quality:       str,
    reason:        str,
) -> None:
    pnl_inr = round((price - entry_price) * quantity, 2)
    pnl_pct = round((price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0.0

    emoji = "✅" if pnl_inr >= 0 else "❌"
    logger.info(
        f"[TRADE] {emoji} SELL {symbol} × {quantity} | "
        f"entry=₹{entry_price:.2f} exit=₹{price:.2f} | "
        f"P&L=₹{pnl_inr:+,.0f} ({pnl_pct:+.1f}%) | reason={reason}"
    )

    _append_row(
        symbol    = symbol,
        action    = "SELL",
        price     = price,
        quantity  = quantity,
        reason    = reason,
        composite = composite,
        quality   = quality,
        pnl_inr   = pnl_inr,
        pnl_pct   = pnl_pct,
    )


def _append_row(
    symbol:    str,
    action:    str,
    price:     float,
    quantity:  int,
    reason:    str,
    composite: float,
    quality:   str,
    pnl_inr:   float,
    pnl_pct:   float,
) -> None:
    path = Path(TRADE_LOG_FILE)
    file_exists = path.exists()
    value_inr = round(price * quantity, 2)

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "date":            date.today().isoformat(),
            "symbol":          symbol,
            "action":          action,
            "price":           round(price, 2),
            "quantity":        quantity,
            "value_inr":       value_inr,
            "reason":          reason,
            "composite_score": round(composite, 3),
            "quality":         quality,
            "pnl_inr":         pnl_inr,
            "pnl_pct":         pnl_pct,
        })


def print_session_summary(session_buys: list, session_sells: list) -> None:
    """Print a summary of today's rebalance to the log."""
    sep = "=" * 62
    logger.info(sep)
    logger.info(f"  REBALANCE SUMMARY — {date.today().isoformat()}")
    logger.info(sep)
    logger.info(f"  Sells executed : {len(session_sells)}")
    logger.info(f"  Buys executed  : {len(session_buys)}")

    if session_sells:
        total_pnl = sum(t.get("pnl_inr", 0) for t in session_sells)
        logger.info(f"  Realised P&L   : ₹{total_pnl:+,.0f}")
        logger.info("  SELLS:")
        for t in session_sells:
            emoji = "✅" if t.get("pnl_inr", 0) >= 0 else "❌"
            logger.info(
                f"    {emoji} {t['symbol']:<14} ×{t['quantity']:<5} "
                f"₹{t['entry_price']:,.2f}→₹{t['price']:,.2f}  "
                f"P&L=₹{t.get('pnl_inr',0):+,.0f} ({t.get('pnl_pct',0):+.1f}%)  [{t['reason']}]"
            )

    if session_buys:
        logger.info("  BUYS:")
        for t in session_buys:
            logger.info(
                f"    ↑  {t['symbol']:<14} ×{t['quantity']:<5} "
                f"₹{t['price']:,.2f}  composite={t['composite']:.2f}  [{t['quality']}]"
            )

    logger.info(sep)
