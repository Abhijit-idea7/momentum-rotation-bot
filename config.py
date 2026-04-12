"""
config.py
---------
Dual Momentum Delivery Bot — Configuration.

Strategy: Gary Antonacci's Dual Momentum, adapted for Nifty 200.
  1. Absolute momentum  — Nifty 50 12M-1M return > 6% → risk-on, else → cash
  2. Relative momentum  — buy top 15 Nifty 200 stocks by 12M-1M return
  Rebalance: monthly (last trading day of each month)
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Nifty 200 Universe  (NSE symbols — no .NS suffix; yfinance adds it)
# ---------------------------------------------------------------------------
NIFTY200_UNIVERSE = [
    # Nifty 50 core
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK",
    "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "HCLTECH",
    "SUNPHARMA", "BAJFINANCE", "TITAN", "ULTRACEMCO", "WIPRO",
    "NESTLEIND", "POWERGRID", "NTPC", "TECHM", "ONGC",
    "M&M", "TATAMOTORS", "TATASTEEL", "ADANIENT", "BAJAJFINSV",
    "DIVISLAB", "DRREDDY", "CIPLA", "EICHERMOT", "COALINDIA",
    "JSWSTEEL", "BRITANNIA", "APOLLOHOSP", "BPCL", "HEROMOTOCO",
    "HINDALCO", "GRASIM", "SBILIFE", "HDFCLIFE", "TATACONSUM",
    "INDUSINDBK", "ADANIPORTS", "LTIM", "BAJAJ-AUTO",

    # Nifty Next 50 / Nifty 100 extension
    "SIEMENS", "HAVELLS", "PIDILITIND", "DABUR", "MARICO",
    "COLPAL", "BERGEPAINT", "GODREJCP", "MUTHOOTFIN", "TATAPOWER",
    "IRCTC", "BANKBARODA", "CANBK", "FEDERALBNK",
    "INDIGO", "TRENT", "DMART", "ZOMATO",
    "VEDL", "NMDC", "GAIL", "IOC", "HINDPETRO",
    "BALKRISIND", "MPHASIS", "LTTS", "COFORGE", "PERSISTENT", "OFSS",
    "BIOCON", "AUROPHARMA", "TORNTPHARM", "LUPIN", "ALKEM",
    "PIIND", "VBL", "SHREECEM", "AMBUJACEM", "ACC",

    # Mid-cap quality
    "CHOLAFIN", "CUMMINSIND", "VOLTAS", "CONCOR",
    "HINDZINC", "HUDCO", "RVNL", "IRFC", "PFC", "RECLTD",
    "ICICIGI", "MFSL", "STARHEALTH",
    "KPITTECH", "INTELLECT",
    "ESCORTS", "ASHOKLEY", "TVSMOTOR", "EXIDEIND",
    "AMARARAJA", "BOSCHLTD", "SUNDRMFAST", "SCHAEFFLER", "TIINDIA",
    "APLAPOLLO", "JSWENERGY", "TORNTPOWER", "CESC", "NHPC", "SJVN",
    "INDIANB", "BANKINDIA", "UNIONBANK", "IDFCFIRSTB",
    "RBLBANK", "KARURVYSYA",
    "ASTRAL", "POLYCAB", "KEI",
    "IEX", "CAMS", "CDSL", "BSE", "MCX",
    "ANGELONE", "MOTILALOFS", "360ONE", "ANANDRATHI",
    "CANFINHOME", "MANAPPURAM",
    "SBICARD", "BANDHANBNK",
    "JBCHEPHARM", "PFIZER", "ABBOTINDIA",
    "LAURUSLABS", "GLENMARK", "GRANULES",
    "INDHOTEL",
    "ZYDUSLIFE", "IPCALAB", "AJANTPHARM", "CAPLIPOINT",
    "HAPPSTMNDS", "INDIAMART", "INFOEDGE",
    "AFFLE", "LATENTVIEW",
    "LICI", "ABFRL",
    "GPPL", "ADANIGREEN", "ADANITRANS",
    "NCC", "HGINFRA",
    "NATIONALUM", "SAIL",
]

# ---------------------------------------------------------------------------
# Dual Momentum Parameters
# ---------------------------------------------------------------------------
MOMENTUM_LOOKBACK_DAYS      = 252   # ~12 months of trading days
SKIP_RECENT_DAYS            = 21    # Skip last month (avoids short-term reversal)
ABSOLUTE_MOMENTUM_THRESHOLD = 0.06  # Nifty 50 12M-1M return must exceed 6% → risk-on
TOP_N_HOLD                  = 15    # Maximum simultaneous open positions
HOLD_BUFFER                 = 20    # Keep holding if still in top HOLD_BUFFER (reduces churn)
MIN_HISTORY_BARS            = 285   # Minimum bars needed: 252 + 21 + 12 buffer

# ---------------------------------------------------------------------------
# Risk valve  (safety net — not part of original Dual Momentum)
# Protects against individual stock gap-downs between monthly rebalances.
# Applied on the monthly rebalance date only. Set to None to disable.
# ---------------------------------------------------------------------------
HARD_STOP_PCT = 0.15    # Sell if position down >15% from entry

# ---------------------------------------------------------------------------
# Portfolio / Position Sizing
# ---------------------------------------------------------------------------
PORTFOLIO_SIZE    = TOP_N_HOLD
POSITION_SIZE_INR = 100_000    # ₹1 lakh per stock (15 slots = ₹15 lakh)

# ---------------------------------------------------------------------------
# Market regime (uses REGIME_TICKER for absolute momentum check)
# ---------------------------------------------------------------------------
MARKET_REGIME_FILTER = True
REGIME_TICKER        = "^NSEI"

# ---------------------------------------------------------------------------
# File paths  (committed to repo after every rebalance)
# ---------------------------------------------------------------------------
POSITIONS_FILE = "positions.csv"
TRADE_LOG_FILE = "trade_log.csv"

# ---------------------------------------------------------------------------
# Order settings — NORMAL = CNC Delivery in Zerodha via stocksdeveloper
# ---------------------------------------------------------------------------
EXCHANGE     = "NSE"
PRODUCT_TYPE = "NORMAL"    # CNC delivery (NOT INTRADAY)
ORDER_TYPE   = "MARKET"
VARIETY      = "REGULAR"
ORDER_TIME   = "15:00"     # Target: fire orders before 15:30 market close

# ---------------------------------------------------------------------------
# Stocksdeveloper Webhook  (same endpoint as intraday bot)
# ---------------------------------------------------------------------------
STOCKSDEVELOPER_URL     = "https://tv.stocksdeveloper.in/"
STOCKSDEVELOPER_API_KEY = os.getenv("STOCKSDEVELOPER_API_KEY")
STOCKSDEVELOPER_ACCOUNT = os.getenv("STOCKSDEVELOPER_ACCOUNT", "AbhiZerodha")

if not STOCKSDEVELOPER_API_KEY:
    raise EnvironmentError(
        "STOCKSDEVELOPER_API_KEY is not set. "
        "Add it to your .env file or GitHub Actions secrets."
    )
