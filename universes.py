"""
universes.py
------------
Universe registry for the Dual Momentum Delivery Bot.

Each universe defines its own:
  - Symbol list (stocks or ETFs)
  - Portfolio sizing params  (top_n_hold, hold_buffer)
  - State file paths         (positions CSV, trade-log CSV)

Select at runtime via --universe NIFTY200 or --universe BEES.
Both universes use identical strategy logic — only the universe params differ.

Tracked separately:
  NIFTY200 → positions_nifty200.csv + trade_log_nifty200.csv
  BEES     → positions_bees.csv     + trade_log_bees.csv
"""

from dataclasses import dataclass

from config import NIFTY200_UNIVERSE   # single source of truth for the N200 list


# ── Universe config dataclass ─────────────────────────────────────────────────

@dataclass(frozen=True)
class UniverseConfig:
    name:           str
    display_name:   str
    symbols:        tuple          # frozen tuple — immutable after creation
    top_n_hold:     int            # maximum simultaneous positions
    hold_buffer:    int            # keep holding until rank exceeds this
    positions_file: str            # CSV that tracks open positions
    trade_log_file: str            # CSV that records every BUY/SELL


# ── BEES ETF Universe ─────────────────────────────────────────────────────────
#
# Nippon India BeES family + other popular liquid NSE ETFs.
#
# Momentum works extremely well on ETFs because:
#   - Each ETF captures a distinct risk factor (sector/asset class)
#   - Rotation between asset classes is the core of tactical allocation
#   - Gold/Silver provide real diversification when equities are weak
#
# ETFs excluded intentionally:
#   LIQUIDBEES — money-market fund (near-zero return, not a momentum candidate)
#
# Note: if a symbol is absent from Yahoo Finance (delisted / renamed), the
# data_feed will silently drop it (>40% missing filter), so extras are safe.
#
BEES_UNIVERSE: list[str] = [
    # ── Broad equity index ETFs ───────────────────────────────────────────────
    "NIFTYBEES",        # Nippon India ETF Nifty 50 BeES
    "JUNIORBEES",       # Nippon India ETF Nifty Next 50 Junior BeES
    "BANKBEES",         # Nippon India ETF Bank Nifty BeES

    # ── Sector equity ETFs ────────────────────────────────────────────────────
    "ITBEES",           # Nippon India ETF IT BeES
    "PHARMABEES",       # Nippon India ETF Pharma BeES
    "INFRABEES",        # Nippon India ETF Infra BeES
    "CPSEETF",          # Nippon India ETF CPSE (formerly CPSEBEES)
    "PSUBNKBEES",       # Nippon India ETF PSU Bank BeES
    "NIFTYQUALITYBEES", # Nippon India ETF Nifty Quality 30 BeES
    "LOWVOLBEES",       # Nippon India ETF Nifty Low Volatility 30 BeES
    "MIDCAPETF",        # Nippon India ETF Nifty Midcap 150

    # ── Commodity ETFs ────────────────────────────────────────────────────────
    "GOLDBEES",         # Nippon India ETF Gold BeES
    "SILVERBEES",       # Nippon India ETF Silver BeES

    # ── Other AMC ETFs (same strategy, different fund house) ──────────────────
    "SETFNIF50",        # SBI ETF Nifty 50
    "HDFCNIFTY",        # HDFC Nifty 50 ETF
    # MAFANG removed — subscription closed, not available for new investment
]


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, UniverseConfig] = {
    "NIFTY200": UniverseConfig(
        name           = "NIFTY200",
        display_name   = "Nifty 200 Stocks",
        symbols        = tuple(NIFTY200_UNIVERSE),
        top_n_hold     = 15,   # hold top 15 of ~200 stocks
        hold_buffer    = 20,   # keep until rank drops below 20
        positions_file = "positions_nifty200.csv",
        trade_log_file = "trade_log_nifty200.csv",
    ),
    "BEES": UniverseConfig(
        name           = "BEES",
        display_name   = "BEES ETFs",
        symbols        = tuple(BEES_UNIVERSE),
        top_n_hold     = 5,    # hold top 5 of ~15 ETFs
        hold_buffer    = 7,    # keep until rank drops below 7
        positions_file = "positions_bees.csv",
        trade_log_file = "trade_log_bees.csv",
    ),
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_universe(name: str) -> UniverseConfig:
    """Return the UniverseConfig for the given universe name.

    Args:
        name: 'NIFTY200' or 'BEES'  (case-insensitive)

    Raises:
        ValueError: if name is not recognised
    """
    key = name.upper()
    if key not in _REGISTRY:
        raise ValueError(
            f"Unknown universe '{name}'. "
            f"Valid choices: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[key]


def list_universes() -> list[str]:
    """Return all registered universe names."""
    return list(_REGISTRY.keys())
