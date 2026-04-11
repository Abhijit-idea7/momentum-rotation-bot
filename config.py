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
# Entry Filter  — STRONG only: eliminates noisy MODERATE entries that
# drive excessive turnover and transaction cost drag
# ---------------------------------------------------------------------------
ENTRY_QUALITY_FILTER   = ("STRONG",)      # Change 1: was ("STRONG", "MODERATE")

# ---------------------------------------------------------------------------
# Signal-Driven Exit Criteria  (checked daily, not on a calendar)
# ---------------------------------------------------------------------------
# 1. Hard stop  — position down this % from entry price → cut loss immediately
HARD_STOP_PCT          = 0.06   # Change 2: widened from 3% → 6% (room to breathe)

# 2. Profit target — harvest gains when position up this % from entry
PROFIT_TARGET_PCT      = 0.18   # 18% (unchanged)

# 3. Momentum fade — composite drops below this floor → sell
EXIT_COMPOSITE_FLOOR   = 1.5    # normal (bull) regime floor

# 4. Bear regime floor — when market is in downtrend, exit faster
BEAR_COMPOSITE_FLOOR   = 2.5    # Change 5: stricter floor during bear market

# 5. Trend break — ROC20 < 0 AND price < 50D MA (both required)
#    (no config needed — conditions are in code)

# ---------------------------------------------------------------------------
# Hold & Re-entry Controls  (reduce churn)
# ---------------------------------------------------------------------------
# Change 3: minimum days to hold before soft exits (TREND_BREAK, MOMENTUM_FADE)
# are evaluated. Hard stop is ALWAYS active from day 1.
MIN_HOLD_DAYS          = 7

# Change 4: days after exiting a stock before it can be re-entered
REENTRY_COOLDOWN_DAYS  = 15

# ---------------------------------------------------------------------------
# Market regime filter: don't open new positions if Nifty 50 is below 200D MA
# ---------------------------------------------------------------------------
MARKET_REGIME_FILTER   = True
REGIME_TICKER          = "^NSEI"

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
COOLDOWN_FILE          = "cooldown.csv"   # tracks recent exits for re-entry gate

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
