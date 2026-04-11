"""
portfolio_state.py
------------------
Persists open delivery positions AND re-entry cooldowns across daily runs.

Two CSV files are committed to the repo after every scan:
  positions.csv  — current open positions
  cooldown.csv   — symbols recently exited (blocks re-entry for N days)
"""

import csv
import logging
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path

from config import COOLDOWN_FILE, POSITIONS_FILE, REENTRY_COOLDOWN_DAYS

logger = logging.getLogger(__name__)

_POS_FIELDS = [
    "symbol", "entry_date", "entry_price", "quantity",
    "composite_at_entry", "quality_at_entry", "entry_type",
]
_CD_FIELDS = ["symbol", "exit_date"]


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
    """Loads and saves open positions + re-entry cooldowns."""

    def __init__(self) -> None:
        self._positions: dict[str, DeliveryPosition] = {}
        self._cooldowns: dict[str, date] = {}          # symbol → exit_date
        self._load_positions()
        self._load_cooldowns()

    # ── Persistence ───────────────────────────────────────────────────────

    def _load_positions(self) -> None:
        path = Path(POSITIONS_FILE)
        if not path.exists():
            logger.info(f"{POSITIONS_FILE} not found — starting with empty portfolio.")
            return
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get("symbol"):
                    continue
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

    def _load_cooldowns(self) -> None:
        path = Path(COOLDOWN_FILE)
        if not path.exists():
            return
        today = date.today()
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get("symbol"):
                    continue
                exit_dt = date.fromisoformat(row["exit_date"])
                # Only keep cooldowns that haven't expired yet
                if (today - exit_dt).days < REENTRY_COOLDOWN_DAYS:
                    self._cooldowns[row["symbol"]] = exit_dt
        logger.info(f"Loaded {len(self._cooldowns)} active cooldowns from {COOLDOWN_FILE}")

    def save(self) -> None:
        """Write positions and cooldowns to CSV (overwrite both files)."""
        with open(POSITIONS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_POS_FIELDS)
            writer.writeheader()
            for pos in self._positions.values():
                writer.writerow(asdict(pos))

        # Prune expired cooldowns before saving
        today = date.today()
        active = {s: d for s, d in self._cooldowns.items()
                  if (today - d).days < REENTRY_COOLDOWN_DAYS}
        self._cooldowns = active

        with open(COOLDOWN_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CD_FIELDS)
            writer.writeheader()
            for sym, dt in self._cooldowns.items():
                writer.writerow({"symbol": sym, "exit_date": dt.isoformat()})

        logger.info(
            f"Saved {len(self._positions)} positions | "
            f"{len(self._cooldowns)} active cooldowns"
        )

    # ── Position queries ──────────────────────────────────────────────────

    def all(self) -> list[DeliveryPosition]:
        return list(self._positions.values())

    def has(self, symbol: str) -> bool:
        return symbol in self._positions

    def get(self, symbol: str) -> DeliveryPosition | None:
        return self._positions.get(symbol)

    def count(self) -> int:
        return len(self._positions)

    # ── Cooldown queries ──────────────────────────────────────────────────

    def is_on_cooldown(self, symbol: str) -> bool:
        """True if this symbol was exited recently and cannot be re-entered yet."""
        if symbol not in self._cooldowns:
            return False
        days_since_exit = (date.today() - self._cooldowns[symbol]).days
        return days_since_exit < REENTRY_COOLDOWN_DAYS

    def cooldown_days_remaining(self, symbol: str) -> int:
        if symbol not in self._cooldowns:
            return 0
        elapsed = (date.today() - self._cooldowns[symbol]).days
        return max(0, REENTRY_COOLDOWN_DAYS - elapsed)

    # ── Mutations ─────────────────────────────────────────────────────────

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
        # Clear any stale cooldown for this symbol (we just bought it)
        self._cooldowns.pop(symbol, None)
        logger.info(
            f"[PORTFOLIO] +ADD {symbol} | qty={quantity} @ ₹{entry_price:.2f} "
            f"composite={composite_at_entry:.2f} [{quality_at_entry}]"
        )

    def remove(self, symbol: str) -> DeliveryPosition | None:
        pos = self._positions.pop(symbol, None)
        if pos:
            # Record exit date for re-entry cooldown
            self._cooldowns[symbol] = date.today()
            logger.info(
                f"[PORTFOLIO] -REMOVE {symbol} | "
                f"cooldown until {(date.today() + timedelta(days=REENTRY_COOLDOWN_DAYS)).isoformat()}"
            )
        return pos

    def summary(self) -> str:
        lines = []
        if not self._positions:
            lines.append("Portfolio: empty")
        else:
            lines.append(f"Open positions ({self.count()}):")
            today = date.today()
            for p in self._positions.values():
                entry_dt  = date.fromisoformat(p.entry_date)
                days_held = (today - entry_dt).days
                lines.append(
                    f"  {p.symbol:<14} qty={p.quantity:<5} "
                    f"entry=₹{p.entry_price:,.2f}  held={days_held}d  "
                    f"composite={p.composite_at_entry:.2f}  [{p.quality_at_entry}]"
                )
        if self._cooldowns:
            lines.append(f"On cooldown ({len(self._cooldowns)}): "
                         + ", ".join(
                             f"{s}({self.cooldown_days_remaining(s)}d)"
                             for s in self._cooldowns
                         ))
        return "\n".join(lines)
