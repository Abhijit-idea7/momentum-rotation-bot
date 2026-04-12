"""
portfolio_state.py
------------------
Persists open delivery positions across monthly rebalances.

Single CSV file committed to the repo after every rebalance:
  positions.csv  — current open positions with entry metadata

No cooldown tracking — Dual Momentum has no re-entry gate.
Stocks exit because they fell out of the top-ranked tier, and
they re-enter the next month if they rank highly again.
"""

import csv
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from config import POSITIONS_FILE

logger = logging.getLogger(__name__)

_POS_FIELDS = [
    "symbol", "entry_date", "entry_price", "quantity",
    "momentum_return_at_entry", "rank_at_entry",
]


@dataclass
class DeliveryPosition:
    symbol:                   str
    entry_date:               str     # "YYYY-MM-DD"
    entry_price:              float
    quantity:                 int
    momentum_return_at_entry: float   # 12M-1M return on the month we bought
    rank_at_entry:            int     # Rank in universe on the month we bought


class PortfolioState:
    """Loads and saves open positions from/to positions.csv."""

    def __init__(self) -> None:
        self._positions: dict[str, DeliveryPosition] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        path = Path(POSITIONS_FILE)
        if not path.exists():
            logger.info(f"{POSITIONS_FILE} not found — starting with empty portfolio.")
            return
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get("symbol"):
                    continue
                pos = DeliveryPosition(
                    symbol                   = row["symbol"],
                    entry_date               = row["entry_date"],
                    entry_price              = float(row["entry_price"]),
                    quantity                 = int(row["quantity"]),
                    momentum_return_at_entry = float(row["momentum_return_at_entry"]),
                    rank_at_entry            = int(row["rank_at_entry"]),
                )
                self._positions[pos.symbol] = pos
        logger.info(f"Loaded {len(self._positions)} open positions from {POSITIONS_FILE}")

    def save(self) -> None:
        """Overwrite positions.csv with current state."""
        with open(POSITIONS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_POS_FIELDS)
            writer.writeheader()
            for pos in self._positions.values():
                writer.writerow(asdict(pos))
        logger.info(f"Saved {len(self._positions)} open positions to {POSITIONS_FILE}")

    # ── Queries ───────────────────────────────────────────────────────────────

    def all(self) -> list[DeliveryPosition]:
        return list(self._positions.values())

    def symbols(self) -> set[str]:
        return set(self._positions.keys())

    def has(self, symbol: str) -> bool:
        return symbol in self._positions

    def get(self, symbol: str) -> DeliveryPosition | None:
        return self._positions.get(symbol)

    def count(self) -> int:
        return len(self._positions)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def add(
        self,
        symbol:                   str,
        entry_price:              float,
        quantity:                 int,
        momentum_return_at_entry: float,
        rank_at_entry:            int,
    ) -> None:
        self._positions[symbol] = DeliveryPosition(
            symbol                   = symbol,
            entry_date               = date.today().isoformat(),
            entry_price              = round(entry_price, 2),
            quantity                 = quantity,
            momentum_return_at_entry = round(momentum_return_at_entry, 4),
            rank_at_entry            = rank_at_entry,
        )
        logger.info(
            f"[PORTFOLIO] +BUY  {symbol:<14} qty={quantity} @ Rs{entry_price:.2f}  "
            f"12M-1M={momentum_return_at_entry:+.1%}  rank=#{rank_at_entry}"
        )

    def remove(self, symbol: str) -> DeliveryPosition | None:
        pos = self._positions.pop(symbol, None)
        if pos:
            logger.info(f"[PORTFOLIO] -SELL {symbol:<14} (removed from portfolio)")
        return pos

    # ── Display ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        lines = []
        today = date.today()
        if not self._positions:
            lines.append("Portfolio: empty (all cash)")
        else:
            lines.append(f"Open positions ({self.count()}):")
            for p in sorted(self._positions.values(),
                            key=lambda x: x.momentum_return_at_entry, reverse=True):
                entry_dt  = date.fromisoformat(p.entry_date)
                days_held = (today - entry_dt).days
                lines.append(
                    f"  {p.symbol:<14} qty={p.quantity:<5} "
                    f"entry=Rs{p.entry_price:,.2f}  held={days_held}d  "
                    f"12M-1M={p.momentum_return_at_entry:+.1%}  rank=#{p.rank_at_entry}"
                )
        return "\n".join(lines)
