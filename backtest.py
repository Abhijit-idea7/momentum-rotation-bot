"""
backtest.py
-----------
Historical backtest for the Momentum Delivery Strategy — Daily Signal Mode.

Simulates the exact same logic as main.py but across historical data:
  • Each trading day: check signal-driven exits for held positions,
    then scan for new STRONG/MODERATE LONG entries
  • No calendar forcing — exits and entries are 100% signal-driven
  • NAV is computed daily

Exit criteria (identical to live bot):
  1. Composite  < EXIT_COMPOSITE_FLOOR (1.5)   → momentum fading
  2. Signal = SHORT                             → direction reversal
  3. ROC20 < 0 AND price < 50D MA              → trend break
  4. Position down ≥ HARD_STOP_PCT (8%)        → cut loss
  5. Position up   ≥ PROFIT_TARGET_PCT (18%)   → harvest

Entry criteria:
  • LONG signal + STRONG or MODERATE quality + GOOD or OK entry type
  • Not already held + slot available + bull regime

Usage:
  python backtest.py
  python backtest.py --start 2021-01-01 --end 2024-12-31
  python backtest.py --portfolio-size 20 --stop 0.10 --target 0.20
  python backtest.py --no-regime-filter

Output (GitHub Actions artifacts):
  backtest_results.csv    — daily NAV log
  backtest_trades.csv     — every BUY / SELL with reason and P&L
  backtest_performance.csv— summary metrics vs Nifty 50
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from config import (
    ENTRY_QUALITY_FILTER,
    EXIT_COMPOSITE_FLOOR,
    HARD_STOP_PCT,
    MIN_HISTORY_BARS,
    NIFTY200_UNIVERSE,
    PROFIT_TARGET_PCT,
)
from momentum_scorer import _score_single

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backtest")

BENCHMARK_TICKER = "^NSEI"
TRANSACTION_COST = 0.0025    # 0.25% one-way: brokerage 0.05% + STT 0.1% + slippage 0.1%
INITIAL_CAPITAL  = 1_500_000
RISK_FREE_RATE   = 0.065     # India 10Y Gsec


# ── Data ─────────────────────────────────────────────────────────────────────

def download_prices(symbols: list[str], start: str, end: str):
    tickers = [f"{s}.NS" for s in symbols] + [BENCHMARK_TICKER]
    logger.info(f"Downloading {len(tickers)} tickers {start} → {end}…")

    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=True)

    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    closes.index = pd.to_datetime(closes.index).tz_localize(None)

    bench  = closes[BENCHMARK_TICKER].dropna() if BENCHMARK_TICKER in closes.columns else None
    stocks = closes.drop(columns=[BENCHMARK_TICKER], errors="ignore").copy()
    stocks.columns = [c.replace(".NS", "") for c in stocks.columns]

    threshold = int(0.60 * len(stocks))
    stocks = stocks.dropna(axis=1, thresh=threshold).ffill().bfill()
    logger.info(f"Retained {len(stocks.columns)} stocks after quality filter")
    return stocks, bench


# ── Per-bar scoring ───────────────────────────────────────────────────────────

def score_bar(prices_df: pd.DataFrame, bar_idx: int) -> pd.DataFrame:
    """Score every stock at a given bar index. Returns sorted DataFrame."""
    rows = []
    for sym in prices_df.columns:
        series = prices_df[sym].iloc[:bar_idx + 1].dropna()
        s = _score_single(sym, series)
        if s:
            rows.append({
                "symbol":    s.symbol,
                "composite": s.composite,
                "accel":     s.acceleration,
                "roc20":     s.roc20,
                "vs_ma50":   s.price_vs_ma50,
                "signal":    s.signal,
                "entry_type":s.entry_type,
                "quality":   s.quality,
                "price":     s.current_price,
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("symbol")
    df.sort_values("composite", ascending=False, inplace=True)
    df["rank"] = range(1, len(df) + 1)
    return df


def regime_bull(bench: pd.Series, bar_idx: int, ma_period: int = 200) -> bool:
    if bench is None or bar_idx < ma_period:
        return True
    window = bench.iloc[max(0, bar_idx - ma_period): bar_idx + 1].dropna()
    return float(bench.iloc[bar_idx]) > float(window.mean()) if len(window) >= 50 else True


# ── Exit evaluation ───────────────────────────────────────────────────────────

def exit_reason(
    entry_price: float,
    current_price: float,
    score_row,           # pd.Series from scores df (or None)
    hard_stop: float,
    profit_target: float,
    composite_floor: float,
) -> str | None:
    if score_row is None:
        return "DATA_GAP"

    loss_pct = (entry_price - current_price) / entry_price
    gain_pct = (current_price - entry_price) / entry_price

    if loss_pct >= hard_stop:
        return f"HARD_STOP"
    if gain_pct >= profit_target:
        return "PROFIT_TARGET"
    if score_row["signal"] == "SHORT":
        return "SIGNAL_SHORT"
    if score_row["composite"] < composite_floor:
        return "MOMENTUM_FADE"
    if score_row["roc20"] < 0 and score_row["vs_ma50"] < 0:
        return "TREND_BREAK"
    return None


# ── Backtest engine ───────────────────────────────────────────────────────────

def run_backtest(
    prices_df:       pd.DataFrame,
    bench:           pd.Series,
    portfolio_size:  int,
    initial_capital: float,
    hard_stop:       float,
    profit_target:   float,
    composite_floor: float,
    use_regime:      bool,
) -> dict:
    cash      = initial_capital
    holdings  = {}    # symbol → {"shares": int, "entry_price": float, "entry_date": str}
    nav_daily = {}
    trades    = []
    scan_log  = []

    total_days = len(prices_df)

    for bar_idx, date in enumerate(prices_df.index):
        if bar_idx < MIN_HISTORY_BARS:
            nav_daily[date] = initial_capital
            continue

        if bar_idx % 60 == 0:
            logger.info(f"  Progress: {bar_idx}/{total_days} bars ({date.date()})…")

        day_prices = prices_df.iloc[bar_idx]

        # ── Value portfolio ───────────────────────────────────────────────────
        port_value = cash
        for sym, pos in holdings.items():
            p = day_prices.get(sym)
            if p and not np.isnan(p):
                port_value += pos["shares"] * p

        # ── Score today ───────────────────────────────────────────────────────
        scores = score_bar(prices_df, bar_idx)

        # ── Regime ───────────────────────────────────────────────────────────
        bench_idx = bench.index.get_loc(date) if (bench is not None and date in bench.index) else bar_idx
        is_bull = regime_bull(bench, bench_idx) if use_regime else True

        # ── Check exits ───────────────────────────────────────────────────────
        to_sell = []
        for sym, pos in list(holdings.items()):
            cur_p = day_prices.get(sym)
            if not cur_p or np.isnan(cur_p):
                to_sell.append((sym, "DATA_GAP"))
                continue
            row   = scores.loc[sym] if sym in scores.index else None
            rsn   = exit_reason(
                pos["entry_price"], cur_p, row,
                hard_stop, profit_target, composite_floor,
            )
            if rsn:
                to_sell.append((sym, rsn))

        for sym, rsn in to_sell:
            cur_p = day_prices.get(sym, holdings[sym]["entry_price"])
            if np.isnan(cur_p):
                cur_p = holdings[sym]["entry_price"]
            pos      = holdings.pop(sym)
            proceeds = pos["shares"] * cur_p * (1 - TRANSACTION_COST)
            cash    += proceeds
            pnl_inr  = round((cur_p - pos["entry_price"]) * pos["shares"], 2)
            pnl_pct  = round((cur_p - pos["entry_price"]) / pos["entry_price"] * 100, 2) if pos["entry_price"] else 0
            trades.append({
                "date":        date.date().isoformat(),
                "symbol":      sym,
                "action":      "SELL",
                "price":       round(cur_p, 2),
                "shares":      pos["shares"],
                "value_inr":   round(proceeds, 0),
                "entry_price": pos["entry_price"],
                "hold_days":   (date - pd.Timestamp(pos["entry_date"])).days,
                "reason":      rsn,
                "composite":   scores.loc[sym, "composite"] if sym in scores.index else 0,
                "pnl_inr":     pnl_inr,
                "pnl_pct":     pnl_pct,
            })

        # ── Check entries ─────────────────────────────────────────────────────
        slots = portfolio_size - len(holdings)
        if slots > 0 and is_bull and not scores.empty:
            alloc_each = port_value / portfolio_size

            eligible = scores[
                (scores["signal"] == "LONG") &
                (scores["quality"].isin(ENTRY_QUALITY_FILTER)) &
                (scores["entry_type"].isin(["GOOD", "OK"]))
            ]

            new_buys = [s for s in eligible.index if s not in holdings][:slots]

            for sym in new_buys:
                cur_p = day_prices.get(sym)
                if not cur_p or np.isnan(cur_p) or cur_p <= 0:
                    continue
                cost_ps = cur_p * (1 + TRANSACTION_COST)
                shares  = int(alloc_each / cost_ps)
                if shares < 1:
                    continue
                cash -= shares * cost_ps
                holdings[sym] = {
                    "shares":      shares,
                    "entry_price": cur_p,
                    "entry_date":  date.isoformat(),
                }
                trades.append({
                    "date":        date.date().isoformat(),
                    "symbol":      sym,
                    "action":      "BUY",
                    "price":       round(cur_p, 2),
                    "shares":      shares,
                    "value_inr":   round(shares * cost_ps, 0),
                    "entry_price": cur_p,
                    "hold_days":   0,
                    "reason":      "SIGNAL_ENTRY",
                    "composite":   scores.loc[sym, "composite"],
                    "pnl_inr":     0,
                    "pnl_pct":     0,
                })

        # ── Revalue and record NAV ────────────────────────────────────────────
        port_value = cash
        for sym, pos in holdings.items():
            p = day_prices.get(sym)
            if p and not np.isnan(p):
                port_value += pos["shares"] * p

        nav_daily[date] = port_value

        if to_sell or (slots > 0 and is_bull):
            scan_log.append({
                "date":       date.date().isoformat(),
                "nav":        round(port_value, 0),
                "holdings":   len(holdings),
                "regime":     "BULL" if is_bull else "BEAR",
                "sells":      len(to_sell),
                "buys":       len([t for t in trades if t["date"] == date.date().isoformat() and t["action"] == "BUY"]),
                "portfolio":  ", ".join(holdings.keys()),
            })

    return {
        "nav":    pd.Series(nav_daily),
        "trades": pd.DataFrame(trades) if trades else pd.DataFrame(),
        "log":    pd.DataFrame(scan_log) if scan_log else pd.DataFrame(),
    }


# ── Performance metrics ───────────────────────────────────────────────────────

def metrics(series: pd.Series, name: str) -> dict:
    if len(series) < 2:
        return {"Strategy": name}
    # Use daily returns, annualise with 252 trading days
    ret      = series.pct_change().dropna()
    n_years  = (series.index[-1] - series.index[0]).days / 365.25
    total    = (series.iloc[-1] / series.iloc[0]) - 1
    cagr     = (1 + total) ** (1 / n_years) - 1 if n_years > 0 else 0
    vol      = ret.std() * np.sqrt(252)
    sharpe   = (cagr - RISK_FREE_RATE) / vol if vol > 0 else 0
    roll_max = series.cummax()
    dd       = (series - roll_max) / roll_max
    max_dd   = dd.min()
    calmar   = cagr / abs(max_dd) if max_dd < 0 else 0
    # Monthly win rate
    monthly  = series.resample("ME").last().pct_change().dropna()
    wins     = (monthly > 0).sum()
    return {
        "Strategy":     name,
        "Total Return": f"{total:.1%}",
        "CAGR":         f"{cagr:.1%}",
        "Volatility":   f"{vol:.1%}",
        "Sharpe":       f"{sharpe:.2f}",
        "Max Drawdown": f"{max_dd:.1%}",
        "Calmar":       f"{calmar:.2f}",
        "Monthly Win":  f"{wins}/{len(monthly)} ({wins/len(monthly):.0%})" if len(monthly) else "N/A",
    }


def trade_stats(trades: pd.DataFrame, portfolio_size: int) -> None:
    if trades.empty:
        print("  No trades.")
        return
    sells       = trades[trades["action"] == "SELL"]
    buys        = trades[trades["action"] == "BUY"]
    n_sells     = len(sells)
    win_sells   = (sells["pnl_inr"] > 0).sum() if n_sells else 0
    total_pnl   = sells["pnl_inr"].sum() if n_sells else 0
    avg_hold    = sells["hold_days"].mean() if n_sells else 0

    # Reason breakdown
    reasons = sells["reason"].value_counts() if n_sells else pd.Series(dtype=int)

    print(f"  Total BUY trades     : {len(buys)}")
    print(f"  Total SELL trades    : {n_sells}")
    if n_sells:
        print(f"  Win rate (sells)     : {win_sells}/{n_sells} ({win_sells/n_sells:.0%})")
        print(f"  Total realised P&L   : ₹{total_pnl:+,.0f}")
        print(f"  Avg hold (days)      : {avg_hold:.1f}")
        print(f"  Exit reason breakdown:")
        for rsn, cnt in reasons.items():
            print(f"    {rsn:<22}: {cnt}")
        best  = sells.loc[sells["pnl_inr"].idxmax()]
        worst = sells.loc[sells["pnl_inr"].idxmin()]
        print(f"  Best trade           : {best['symbol']} ₹{best['pnl_inr']:+,.0f} ({best['pnl_pct']:+.1f}%) [{best['reason']}]")
        print(f"  Worst trade          : {worst['symbol']} ₹{worst['pnl_inr']:+,.0f} ({worst['pnl_pct']:+.1f}%) [{worst['reason']}]")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Momentum Delivery Bot — Daily Backtest")
    p.add_argument("--start",          default="2020-01-01")
    p.add_argument("--end",            default="2024-12-31")
    p.add_argument("--portfolio-size", default=15,          type=int)
    p.add_argument("--capital",        default=1_500_000,   type=float)
    p.add_argument("--stop",           default=HARD_STOP_PCT,    type=float, help="Hard stop % (default 0.08)")
    p.add_argument("--target",         default=PROFIT_TARGET_PCT, type=float, help="Profit target % (default 0.18)")
    p.add_argument("--floor",          default=EXIT_COMPOSITE_FLOOR, type=float, help="Composite exit floor (default 1.5)")
    p.add_argument("--no-regime-filter", action="store_true",  help="Disable Nifty 200D MA regime filter")
    return p.parse_args()


def main():
    args      = parse_args()
    use_regime = not args.no_regime_filter
    sep = "=" * 68

    print(sep)
    print("  MOMENTUM DELIVERY BOT — DAILY SIGNAL BACKTEST")
    print(f"  Period          : {args.start}  →  {args.end}")
    print(f"  Universe        : Nifty 200 ({len(NIFTY200_UNIVERSE)} stocks)")
    print(f"  Portfolio size  : {args.portfolio_size} (equal-weight slots)")
    print(f"  Entry filter    : STRONG or MODERATE quality, GOOD or OK entry")
    print(f"  Hard stop       : {args.stop:.0%}")
    print(f"  Profit target   : {args.target:.0%}")
    print(f"  Composite floor : {args.floor:.1f}")
    print(f"  Regime filter   : {'ON (Nifty 200D MA)' if use_regime else 'OFF'}")
    print(f"  Transaction cost: {TRANSACTION_COST*100:.2f}% per trade")
    print(f"  Initial capital : ₹{args.capital:,.0f}")
    print(sep)

    # ── Download ──────────────────────────────────────────────────────────────
    prices_df, bench = download_prices(NIFTY200_UNIVERSE, args.start, args.end)
    if prices_df.empty:
        logger.error("No price data. Exiting.")
        sys.exit(1)

    # ── Run ───────────────────────────────────────────────────────────────────
    logger.info(f"Running daily scan across {len(prices_df)} trading days…")
    result = run_backtest(
        prices_df       = prices_df,
        bench           = bench,
        portfolio_size  = args.portfolio_size,
        initial_capital = args.capital,
        hard_stop       = args.stop,
        profit_target   = args.target,
        composite_floor = args.floor,
        use_regime      = use_regime,
    )
    nav    = result["nav"]
    trades = result["trades"]

    # ── Benchmark ─────────────────────────────────────────────────────────────
    bench_aligned = None
    if bench is not None and len(nav) > 0:
        bd = [d for d in nav.index if d in bench.index]
        if bd:
            bv = bench.reindex(bd).dropna()
            bench_aligned = bv / bv.iloc[0] * args.capital

    # ── Metrics ───────────────────────────────────────────────────────────────
    sm = metrics(nav, "Momentum Delivery (Daily)")
    bm = metrics(bench_aligned, "Nifty 50 (Benchmark)") if bench_aligned is not None else None

    print(f"\n{sep}")
    print("  STRATEGY PERFORMANCE")
    print(sep)
    for k, v in sm.items():
        print(f"  {k:<22}: {v}")

    if bm:
        print(f"\n{sep}")
        print("  BENCHMARK — NIFTY 50")
        print(sep)
        for k, v in bm.items():
            print(f"  {k:<22}: {v}")

    # ── Annual returns ────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("  ANNUAL RETURNS")
    print(sep)
    print(f"  {'Year':<8} {'Strategy':>12} {'Nifty 50':>12} {'Alpha':>10}")
    print("  " + "-" * 46)
    for yr in sorted(nav.index.year.unique()):
        s_yr = nav[nav.index.year == yr]
        if len(s_yr) < 2:
            continue
        sr    = (s_yr.iloc[-1] / s_yr.iloc[0]) - 1
        b_str = a_str = "N/A"
        if bench_aligned is not None:
            b_yr = bench_aligned[bench_aligned.index.year == yr]
            if len(b_yr) >= 2:
                br    = (b_yr.iloc[-1] / b_yr.iloc[0]) - 1
                b_str = f"{br:+.1%}"
                a_str = f"{sr - br:+.1%}"
        print(f"  {yr:<8} {sr:>+12.1%} {b_str:>12} {a_str:>10}")

    # ── Trade statistics ──────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("  TRADE STATISTICS")
    print(sep)
    trade_stats(trades, args.portfolio_size)

    # ── Save results ──────────────────────────────────────────────────────────
    nav_df = nav.reset_index()
    nav_df.columns = ["date", "nav"]
    nav_df.to_csv("backtest_results.csv", index=False)

    if not trades.empty:
        trades.to_csv("backtest_trades.csv", index=False)

    rows = [sm]
    if bm:
        rows.append(bm)
    pd.DataFrame(rows).to_csv("backtest_performance.csv", index=False)

    print(f"\n  Results saved: backtest_results.csv | backtest_trades.csv | backtest_performance.csv")
    print(f"\n{sep}")
    print("  BACKTEST COMPLETE")
    print(sep)


if __name__ == "__main__":
    main()
