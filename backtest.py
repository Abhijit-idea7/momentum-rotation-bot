"""
backtest.py
-----------
Momentum Delivery Bot — Daily Signal Backtest.

Simulates the exact same logic as main.py across historical data.

Exit rules (identical to live bot):
  1. Hard stop    — down ≥ hard_stop% from entry              [always active, day 1+]
  2. Profit target— up  ≥ profit_target%                       [always active]
  3. Momentum fade— composite < floor                          [after min_hold_days]
                    floor = bear_floor in bear regime, composite_floor in bull
  4. Trend break  — ROC20 < trend_roc AND price < 50D MA by trend_ma  [after min_hold_days]
  (SIGNAL_SHORT removed — MOMENTUM_FADE at 1.5 already covers composite-based exits)

Entry rules:
  • STRONG signal only (ENTRY_QUALITY_FILTER)
  • GOOD or OK entry type
  • Not already held + slot available + bull regime
  • Not on re-entry cooldown (cooldown_days after last exit)

Usage:
  python backtest.py
  python backtest.py --start 2021-01-01 --end 2024-12-31
  python backtest.py --stop 0.07 --target 0.20 --min-hold 10 --cooldown 20
  python backtest.py --no-regime-filter

Outputs (GitHub Actions artifacts):
  backtest_results.csv     — daily NAV
  backtest_trades.csv      — every BUY/SELL with hold days, reason, P&L
  backtest_performance.csv — metrics vs Nifty 50
"""

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from config import (
    BEAR_COMPOSITE_FLOOR,
    ENTRY_QUALITY_FILTER,
    EXIT_COMPOSITE_FLOOR,
    HARD_STOP_PCT,
    MIN_HISTORY_BARS,
    MIN_HOLD_DAYS,
    NIFTY200_UNIVERSE,
    PROFIT_TARGET_PCT,
    REENTRY_COOLDOWN_DAYS,
    REGIME_MA_PERIOD,
    TREND_BREAK_MA_PCT,
    TREND_BREAK_ROC20,
)
from momentum_scorer import _score_single

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backtest")

BENCHMARK_TICKER = "^NSEI"
TRANSACTION_COST = 0.0025    # 0.25% one-way: brokerage + STT + slippage
INITIAL_CAPITAL  = 1_500_000
RISK_FREE_RATE   = 0.065


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
    rows = []
    for sym in prices_df.columns:
        series = prices_df[sym].iloc[:bar_idx + 1].dropna()
        s = _score_single(sym, series)
        if s:
            rows.append({
                "symbol":     s.symbol,
                "composite":  s.composite,
                "accel":      s.acceleration,
                "roc20":      s.roc20,
                "vs_ma50":    s.price_vs_ma50,
                "signal":     s.signal,
                "entry_type": s.entry_type,
                "quality":    s.quality,
                "price":      s.current_price,
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("symbol")
    df.sort_values("composite", ascending=False, inplace=True)
    df["rank"] = range(1, len(df) + 1)
    return df


def regime_bull(bench: pd.Series, bar_idx: int, ma_period: int = None) -> bool:
    if ma_period is None:
        ma_period = REGIME_MA_PERIOD
    if bench is None or bar_idx < ma_period:
        return True
    window = bench.iloc[max(0, bar_idx - ma_period): bar_idx + 1].dropna()
    return float(bench.iloc[bar_idx]) > float(window.mean()) if len(window) >= 50 else True


# ── Exit evaluation ───────────────────────────────────────────────────────────

def eval_exit(
    entry_price:     float,
    entry_date:      pd.Timestamp,
    current_date:    pd.Timestamp,
    current_price:   float,
    score_row,                      # pd.Series or None
    is_bull:         bool,
    hard_stop:       float,
    profit_target:   float,
    composite_floor: float,
    bear_floor:      float,
    min_hold_days:   int,
    trend_roc:       float = None,
    trend_ma:        float = None,
) -> str | None:
    if trend_roc is None:
        trend_roc = TREND_BREAK_ROC20
    if trend_ma is None:
        trend_ma = TREND_BREAK_MA_PCT

    if score_row is None:
        return "DATA_GAP"

    days_held = (current_date - entry_date).days
    loss_pct  = (entry_price - current_price) / entry_price
    gain_pct  = (current_price - entry_price) / entry_price

    # Always active
    if loss_pct >= hard_stop:
        return "HARD_STOP"
    if gain_pct >= profit_target:
        return "PROFIT_TARGET"

    # Soft exits after minimum hold
    if days_held < min_hold_days:
        return None

    floor = composite_floor if is_bull else bear_floor
    if score_row["composite"] < floor:
        return "MOMENTUM_FADE"

    if score_row["roc20"] < trend_roc and score_row["vs_ma50"] < trend_ma:
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
    bear_floor:      float,
    min_hold_days:   int,
    cooldown_days:   int,
    use_regime:      bool,
    regime_ma:       int   = None,
    trend_roc:       float = None,
    trend_ma:        float = None,
) -> dict:
    if regime_ma is None:
        regime_ma = REGIME_MA_PERIOD
    if trend_roc is None:
        trend_roc = TREND_BREAK_ROC20
    if trend_ma is None:
        trend_ma = TREND_BREAK_MA_PCT
    cash      = initial_capital
    holdings  = {}   # symbol → {shares, entry_price, entry_date (Timestamp)}
    cooldowns = {}   # symbol → exit_date (Timestamp)
    nav_daily = {}
    trades    = []

    total_days = len(prices_df)

    for bar_idx, date in enumerate(prices_df.index):
        if bar_idx < MIN_HISTORY_BARS:
            nav_daily[date] = initial_capital
            continue

        if bar_idx % 60 == 0:
            logger.info(f"  {bar_idx}/{total_days} bars ({date.date()})…")

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
        bench_idx = (bench.index.get_loc(date)
                     if (bench is not None and date in bench.index)
                     else bar_idx)
        is_bull = regime_bull(bench, bench_idx, regime_ma) if use_regime else True

        # ── Exits ─────────────────────────────────────────────────────────────
        to_sell = []
        for sym, pos in list(holdings.items()):
            cur_p = day_prices.get(sym)
            if not cur_p or np.isnan(cur_p):
                to_sell.append((sym, "DATA_GAP"))
                continue
            row = scores.loc[sym] if sym in scores.index else None
            rsn = eval_exit(
                entry_price     = pos["entry_price"],
                entry_date      = pos["entry_date"],
                current_date    = date,
                current_price   = cur_p,
                score_row       = row,
                is_bull         = is_bull,
                hard_stop       = hard_stop,
                profit_target   = profit_target,
                composite_floor = composite_floor,
                bear_floor      = bear_floor,
                min_hold_days   = min_hold_days,
                trend_roc       = trend_roc,
                trend_ma        = trend_ma,
            )
            if rsn:
                to_sell.append((sym, rsn))

        for sym, rsn in to_sell:
            cur_p = day_prices.get(sym) or holdings[sym]["entry_price"]
            if np.isnan(cur_p):
                cur_p = holdings[sym]["entry_price"]
            pos      = holdings.pop(sym)
            proceeds = pos["shares"] * cur_p * (1 - TRANSACTION_COST)
            cash    += proceeds
            pnl_inr  = round((cur_p - pos["entry_price"]) * pos["shares"], 2)
            pnl_pct  = round((cur_p - pos["entry_price"]) / pos["entry_price"] * 100, 2)
            hold_days = (date - pos["entry_date"]).days
            trades.append({
                "date":        date.date().isoformat(),
                "symbol":      sym,
                "action":      "SELL",
                "price":       round(cur_p, 2),
                "shares":      pos["shares"],
                "value_inr":   round(proceeds, 0),
                "entry_price": pos["entry_price"],
                "hold_days":   hold_days,
                "reason":      rsn,
                "composite":   scores.loc[sym, "composite"] if sym in scores.index else 0,
                "pnl_inr":     pnl_inr,
                "pnl_pct":     pnl_pct,
            })
            cooldowns[sym] = date   # start cooldown clock

        # ── Entries ───────────────────────────────────────────────────────────
        slots = portfolio_size - len(holdings)
        if slots > 0 and is_bull and not scores.empty:
            alloc_each = port_value / portfolio_size
            eligible = scores[
                (scores["signal"] == "LONG") &
                (scores["quality"].isin(ENTRY_QUALITY_FILTER)) &
                (scores["entry_type"].isin(["GOOD", "OK"]))
            ]
            new_buys = []
            for sym in eligible.index:
                if len(new_buys) >= slots:
                    break
                if sym in holdings:
                    continue
                # Cooldown check
                if sym in cooldowns:
                    days_since_exit = (date - cooldowns[sym]).days
                    if days_since_exit < cooldown_days:
                        continue
                new_buys.append(sym)

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
                    "entry_date":  date,
                }
                cooldowns.pop(sym, None)   # clear stale cooldown
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

        # ── Revalue + record NAV ──────────────────────────────────────────────
        port_value = cash
        for sym, pos in holdings.items():
            p = day_prices.get(sym)
            if p and not np.isnan(p):
                port_value += pos["shares"] * p
        nav_daily[date] = port_value

    return {
        "nav":    pd.Series(nav_daily),
        "trades": pd.DataFrame(trades) if trades else pd.DataFrame(),
    }


# ── Performance metrics ───────────────────────────────────────────────────────

def calc_metrics(series: pd.Series, name: str) -> dict:
    if len(series) < 2:
        return {"Strategy": name}
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


def print_trade_stats(trades: pd.DataFrame) -> None:
    sells = trades[trades["action"] == "SELL"]
    buys  = trades[trades["action"] == "BUY"]
    n     = len(sells)
    if n == 0:
        print("  No closed trades.")
        return
    wins      = (sells["pnl_inr"] > 0).sum()
    total_pnl = sells["pnl_inr"].sum()
    avg_hold  = sells["hold_days"].mean()
    reasons   = sells["reason"].value_counts()

    print(f"  Total BUY trades     : {len(buys)}")
    print(f"  Total SELL trades    : {n}")
    print(f"  Win rate (sells)     : {wins}/{n} ({wins/n:.0%})")
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
    p = argparse.ArgumentParser(description="Momentum Delivery — Daily Backtest")
    p.add_argument("--start",          default="2020-01-01")
    p.add_argument("--end",            default="2024-12-31")
    p.add_argument("--portfolio-size", default=15,                   type=int)
    p.add_argument("--capital",        default=INITIAL_CAPITAL,      type=float)
    p.add_argument("--stop",           default=HARD_STOP_PCT,        type=float)
    p.add_argument("--target",         default=PROFIT_TARGET_PCT,    type=float)
    p.add_argument("--floor",          default=EXIT_COMPOSITE_FLOOR, type=float)
    p.add_argument("--bear-floor",     default=BEAR_COMPOSITE_FLOOR, type=float)
    p.add_argument("--min-hold",       default=MIN_HOLD_DAYS,        type=int)
    p.add_argument("--cooldown",       default=REENTRY_COOLDOWN_DAYS,type=int)
    p.add_argument("--no-regime-filter", action="store_true")
    p.add_argument("--regime-ma",   default=REGIME_MA_PERIOD,  type=int,
                   help="Regime MA period in days (default %(default)s)")
    p.add_argument("--trend-roc",   default=TREND_BREAK_ROC20, type=float,
                   help="ROC20 threshold for TREND_BREAK exit (default %(default)s%%)")
    p.add_argument("--trend-ma",    default=TREND_BREAK_MA_PCT, type=float,
                   help="Price vs 50D MA threshold for TREND_BREAK exit (default %(default)s%%)")
    return p.parse_args()


def main():
    args       = parse_args()
    use_regime = not args.no_regime_filter
    sep = "=" * 68

    print(sep)
    print("  MOMENTUM DELIVERY BOT — DAILY SIGNAL BACKTEST")
    print(f"  Period          : {args.start}  →  {args.end}")
    print(f"  Universe        : Nifty 200 ({len(NIFTY200_UNIVERSE)} stocks)")
    print(f"  Portfolio size  : {args.portfolio_size} (equal-weight slots)")
    print(f"  Entry filter    : {', '.join(ENTRY_QUALITY_FILTER)} signals, GOOD or OK entry")
    print(f"  Hard stop       : {args.stop:.0%}  |  Profit target : {args.target:.0%}")
    print(f"  Composite floor : {args.floor} (bull)  /  {args.bear_floor} (bear)")
    print(f"  Min hold days   : {args.min_hold}  |  Cooldown : {args.cooldown} days")
    print(f"  Regime filter   : {'ON (Nifty ' + str(args.regime_ma) + 'D MA)' if use_regime else 'OFF'}")
    print(f"  Trend break     : ROC20 < {args.trend_roc}%  AND  vs_MA50 < {args.trend_ma}%")
    print(f"  Transaction cost: {TRANSACTION_COST*100:.2f}% per trade")
    print(f"  Initial capital : ₹{args.capital:,.0f}")
    print(sep)

    prices_df, bench = download_prices(NIFTY200_UNIVERSE, args.start, args.end)
    if prices_df.empty:
        logger.error("No price data. Exiting.")
        sys.exit(1)

    logger.info(f"Running daily scan over {len(prices_df)} trading days…")
    result = run_backtest(
        prices_df       = prices_df,
        bench           = bench,
        portfolio_size  = args.portfolio_size,
        initial_capital = args.capital,
        hard_stop       = args.stop,
        profit_target   = args.target,
        composite_floor = args.floor,
        bear_floor      = args.bear_floor,
        min_hold_days   = args.min_hold,
        cooldown_days   = args.cooldown,
        use_regime      = use_regime,
        regime_ma       = args.regime_ma,
        trend_roc       = args.trend_roc,
        trend_ma        = args.trend_ma,
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
    sm = calc_metrics(nav, "Momentum Delivery (Daily)")
    bm = calc_metrics(bench_aligned, "Nifty 50 (Benchmark)") if bench_aligned is not None else None

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

    # ── Trade stats ───────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("  TRADE STATISTICS")
    print(sep)
    print_trade_stats(trades)

    # ── Save ──────────────────────────────────────────────────────────────────
    nav.reset_index().rename(columns={"index": "date", 0: "nav"}).to_csv(
        "backtest_results.csv", index=False
    )
    if not trades.empty:
        trades.to_csv("backtest_trades.csv", index=False)
    rows = [sm]
    if bm:
        rows.append(bm)
    pd.DataFrame(rows).to_csv("backtest_performance.csv", index=False)

    print(f"\n  Saved: backtest_results.csv | backtest_trades.csv | backtest_performance.csv")
    print(f"\n{sep}")
    print("  BACKTEST COMPLETE")
    print(sep)


if __name__ == "__main__":
    main()
