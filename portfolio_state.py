"""
portfolio_state.py
------------------
Persists open delivery positions across rebalance runs via positions.csv.

Unlike the intraday bot (in-memory only), delivery positions live for weeks
or months, so we commit positions.csv back to the repo after every rebalance.

CSV columns:
  symbol, entry_date, entry_price, quantity, composite_at_entry,
  quality_at_entry, entry_type
"""

import csv
import logging
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

from config import POSITIONS_FILE

logger = logging.getLogger(__name__)

_FIELDS = [
    "symbol", "entry_date", "entry_price", "quantity",
    "composite_at_entry", "quality_at_entry", "entry_type",
]


@dataclass
class DeliveryPosition:
    symbol:             str
    entry_date:         str     # "YYYY-MM-DD"
    entry_price:        float
    quantity:           int
    composite_at_entry: float
    quality_at_entry:   str
    entry_type:         str


class PortfolioState:
    """Loads and saves the current set of open delivery positions."""

    def __init__(self) -> None:
        self._positions: dict[str, DeliveryPosition] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        path = Path(POSITIONS_FILE)
        if not path.exists():
            logger.info(f"{POSITIONS_FILE} not found — starting with empty portfolio.")
            return

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pos = DeliveryPosition(
                    symbol             = row["symbol"],
                    entry_date         = row["entry_date"],
                    entry_price        = float(row["entry_price"]),
                    quantity           = int(row["quantity"]),
                    composite_at_entry = float(row["composite_at_entry"]),
                    quality_at_entry   = row["quality_at_entry"],
                    entry_type         = row["entry_type"],
                )
                self._positions[pos.symbol] = pos

        logger.info(f"Loaded {len(self._positions)} open positions from {POSITIONS_FILE}")

    def save(self) -> None:
        """Write current positions to CSV (overwrite)."""
        with open(POSITIONS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDS)
            writer.writeheader()
            for pos in self._positions.values():
                writer.writerow(asdict(pos))
        logger.info(f"Saved {len(self._positions)} open positions to {POSITIONS_FILE}")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Mutations  (call save() after all mutations to persist)
    # ------------------------------------------------------------------

    def add(
        self,
        symbol:             str,
        entry_price:        float,
        quantity:           int,
        composite_at_entry: float,
        quality_at_entry:   str,
        entry_type:         str,
    ) -> None:
        self._positions[symbol] = DeliveryPosition(
            symbol             = symbol,
            entry_date         = date.today().isoformat(),
            entry_price        = round(entry_price, 2),
            quantity           = quantity,
            composite_at_entry = round(composite_at_entry, 3),
            quality_at_entry   = quality_at_entry,
            entry_type         = entry_type,
        )
        logger.info(
            f"[PORTFOLIO] +ADD {symbol} | qty={quantity} @ ₹{entry_price:.2f} "
            f"composite={composite_at_entry:.2f} quality={quality_at_entry}"
        )

    def remove(self, symbol: str) -> DeliveryPosition | None:
        pos = self._positions.pop(symbol, None)
        if pos:
            logger.info(f"[PORTFOLIO] -REMOVE {symbol} from open positions.")
        return pos

    def summary(self) -> str:
        if not self._positions:
            return "Portfolio: empty"
        lines = [f"Open positions ({self.count()}):"]
        for p in self._positions.values():
            lines.append(
                f"  {p.symbol:<14} qty={p.quantity:<5} "
                f"entry=₹{p.entry_price:,.2f}  date={p.entry_date}  "
                f"composite={p.composite_at_entry:.2f}  [{p.quality_at_entry}]"
            )
        return "\n".join(lines)
