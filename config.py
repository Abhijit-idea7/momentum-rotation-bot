"""
config.py
---------
All configuration for the Momentum Rotation Delivery Bot.
Mirrors the structure of the intraday bot's config.py.
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
# Momentum Scoring Parameters  (from Excel framework)
# ---------------------------------------------------------------------------
MIN_COMPOSITE_SCORE    = 2.0    # Minimum composite score for LONG signal
PORTFOLIO_SIZE         = 15     # Max simultaneous open delivery positions
MIN_HISTORY_BARS       = 110    # Minimum daily bars needed to score a stock

# Score scaling factors (match Excel formulas exactly)
ST_SCALE_POS  = 5.0    # ROC20 / ST_SCALE_POS when positive  → cap at 3
ST_SCALE_NEG  = 10.0   # ROC20 / ST_SCALE_NEG when negative  → floor at -1
MT_SCALE_POS  = 10.0   # ROC50 / MT_SCALE_POS when positive  → cap at 3
MT_SCALE_NEG  = 20.0   # ROC50 / MT_SCALE_NEG when negative  → floor at -1
ST_CAP        = 3.0
ST_FLOOR      = -1.0
MT_CAP        = 3.0
MT_FLOOR      = -1.0
TREND_BULL    = 2.0     # Trend score when price > 50MA and bullish
TREND_BEAR    = -2.0    # Trend score when very bearish
ACCEL_BULL    = 5.0     # Acceleration threshold for STRONG quality
ACCEL_BEAR    = -5.0    # Acceleration floor for LONG signal

# ---------------------------------------------------------------------------
# Entry Filter  — only buy STRONG or MODERATE signals (not WEAK noise)
# ---------------------------------------------------------------------------
ENTRY_QUALITY_FILTER   = ("STRONG", "MODERATE")

# ---------------------------------------------------------------------------
# Signal-Driven Exit Criteria  (checked daily, not on a calendar)
# ---------------------------------------------------------------------------
# 1. Momentum fade     — composite drops below this floor
EXIT_COMPOSITE_FLOOR   = 1.5

# 2. Trend break       — ROC20 turns negative AND price breaks below 50D MA
#    Both conditions must be true simultaneously (avoids premature exits)

# 3. Hard stop         — position down this % from entry price → cut loss
HARD_STOP_PCT          = 0.08   # 8%

# 4. Profit target     — harvest gains when position up this % from entry
PROFIT_TARGET_PCT      = 0.18   # 18%

# Market regime filter: don't open new positions if Nifty 50 is below 200D MA
MARKET_REGIME_FILTER   = True
REGIME_TICKER          = "^NSEI"

# ---------------------------------------------------------------------------
# Position Sizing
# ---------------------------------------------------------------------------
POSITION_SIZE_INR = 100_000    # Rs 1 lakh per stock (15 stocks = Rs 15 lakh)

# ---------------------------------------------------------------------------
# Timing  (all IST)
# ---------------------------------------------------------------------------
ORDER_TIME    = "15:00"        # Target order fire time (market closes 15:30)

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

# ---------------------------------------------------------------------------
# Order Defaults — NORMAL = CNC Delivery in Zerodha via stocksdeveloper
# ---------------------------------------------------------------------------
EXCHANGE     = "NSE"
PRODUCT_TYPE = "NORMAL"     # CNC delivery (NOT INTRADAY)
ORDER_TYPE   = "MARKET"
VARIETY      = "REGULAR"

# ---------------------------------------------------------------------------
# File paths (committed back to repo after each rebalance)
# ---------------------------------------------------------------------------
POSITIONS_FILE = "positions.csv"     # Current open delivery positions
TRADE_LOG_FILE = "trade_log.csv"     # Full history of all buy/sell trades
