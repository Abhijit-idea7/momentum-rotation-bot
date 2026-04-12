"""
data_feed.py
------------
Fetches daily OHLCV price data from Yahoo Finance for NSE-listed stocks.
Used by the momentum scorer to compute all metrics.
"""

import logging
import time

import pandas as pd
import yfinance as yf

from config import MIN_HISTORY_BARS, NIFTY200_UNIVERSE, REGIME_MA_PERIOD, REGIME_TICKER

logger = logging.getLogger(__name__)

_BATCH_SIZE = 40   # yfinance handles ~40 tickers per batch efficiently


def _ns(symbol: str) -> str:
    """Return Yahoo Finance ticker string for NSE."""
    return f"{symbol}.NS"


def fetch_daily_history(symbol: str, period: str = "2y") -> pd.DataFrame | None:
    """
    Fetch 2 years of daily OHLCV for a single symbol.
    Returns clean DataFrame or None on failure.
    """
    for attempt in range(3):
        try:
            df = yf.Ticker(_ns(symbol)).history(interval="1d", period=period, auto_adjust=True)
            if df.empty or len(df) < MIN_HISTORY_BARS:
                logger.warning(f"{symbol}: insufficient data ({len(df)} bars)")
                return None
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df
        except Exception as e:
            logger.warning(f"{symbol}: fetch error attempt {attempt + 1} — {e}")
            time.sleep(2)
    logger.error(f"{symbol}: all fetch attempts failed.")
    return None


def fetch_universe_prices(symbols: list[str] = None, period: str = "2y") -> pd.DataFrame:
    """
    Batch download close prices for the full universe.
    Returns a DataFrame with dates as index and symbols as columns.
    Drops symbols with >40% missing data.
    """
    if symbols is None:
        symbols = NIFTY200_UNIVERSE

    tickers = [_ns(s) for s in symbols]
    logger.info(f"Downloading {len(tickers)} tickers (period={period})…")

    all_dfs = []
    for i in range(0, len(tickers), _BATCH_SIZE):
        batch = tickers[i: i + _BATCH_SIZE]
        for attempt in range(3):
            try:
                raw = yf.download(
                    batch,
                    period=period,
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    group_by="ticker",
                )
                # yfinance returns multi-level columns when multiple tickers
                if isinstance(raw.columns, pd.MultiIndex):
                    closes = raw.xs("Close", level=1, axis=1)
                else:
                    closes = raw[["Close"]]
                    closes.columns = batch

                closes.columns = [c.replace(".NS", "") for c in closes.columns]
                all_dfs.append(closes)
                break
            except Exception as e:
                logger.warning(f"Batch download error attempt {attempt + 1}: {e}")
                time.sleep(3)

    if not all_dfs:
        logger.error("All batch downloads failed.")
        return pd.DataFrame()

    prices = pd.concat(all_dfs, axis=1)
    prices.index = pd.to_datetime(prices.index).tz_localize(None)

    # Drop symbols with >40% missing data
    threshold = int(0.60 * len(prices))
    prices = prices.dropna(axis=1, thresh=threshold)
    prices = prices.ffill().bfill()

    logger.info(f"Universe: {len(prices.columns)} stocks with sufficient data retained")
    return prices


def get_current_price(symbol: str) -> float | None:
    """Fetch the latest available close price for a symbol."""
    try:
        df = yf.Ticker(_ns(symbol)).history(period="5d", interval="1d", auto_adjust=True)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"{symbol}: price fetch error — {e}")
        return None


def get_regime_signal(lookback: int = None) -> bool:
    """
    Returns True (bull regime) if Nifty 50 is above its regime MA.
    Returns False (bear regime) → new buys are blocked.
    lookback defaults to REGIME_MA_PERIOD from config (100D).
    """
    if lookback is None:
        lookback = REGIME_MA_PERIOD
    try:
        period = "2y" if lookback > 200 else "1y"
        df = yf.Ticker(REGIME_TICKER).history(period=period, interval="1d", auto_adjust=True)
        if len(df) < lookback:
            logger.warning("Insufficient regime data — defaulting to bull.")
            return True
        ma = df["Close"].iloc[-lookback:].mean()
        current = float(df["Close"].iloc[-1])
        is_bull = current > ma
        logger.info(
            f"Market regime: Nifty={current:.0f}  {lookback}D-MA={ma:.0f}  "
            f"→ {'BULL ✓' if is_bull else 'BEAR — new buys blocked'}"
        )
        return is_bull
    except Exception as e:
        logger.warning(f"Regime check error: {e} — defaulting to bull.")
        return True
