"""Synthetic market data for offline tests (Yahoo-shaped candles)."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from scanner import signals
from scanner.markets import MARKETS, session_for


def business_days(end: date, n: int, label_days=(0, 1, 2, 3, 4)):
    out, d = [], end
    while len(out) < n:
        if d.weekday() in label_days:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def vshape(n, low=80.0, high=120.0, start=100.0):
    """Falls, then rises: gives exactly one bullish EMA cross after the bottom."""
    half = n // 2
    return np.concatenate([np.linspace(start, low, half), np.linspace(low, high, n - half)])


def uptrend(n, start=100.0, end=150.0):
    return np.linspace(start, end, n)


def _frame(idx, close):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"Open": close, "High": close * 1.001, "Low": close * 0.999,
                         "Close": close, "Adj Close": close, "Volume": 1000.0}, index=idx)


def intraday_raw(market_key, minutes, end_day, n_days, closes_fn):
    m = MARKETS[market_key]
    starts = []
    for d in business_days(end_day, n_days, m.label_days):
        s, e = session_for(m, d)
        t = s
        while t < e:
            starts.append(t)
            t += pd.Timedelta(minutes=minutes)
    idx = pd.DatetimeIndex(starts).tz_convert(m.tz)
    return _frame(idx, closes_fn(len(idx)))


def daily_raw(market_key, end_day, n_days, closes_fn):
    m = MARKETS[market_key]
    days = business_days(end_day, n_days, m.label_days)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])       # tz-naive, like yfinance daily
    return _frame(idx, closes_fn(len(idx)))


def first_cross(close, short=10, long=20, direction="up", after=60):
    s = pd.Series(close)
    up, dn = signals.cross_flags(signals.pine_ema(s, short), signals.pine_ema(s, long))
    flags = up if direction == "up" else dn
    idx = [i for i in np.flatnonzero(flags.to_numpy()) if i >= after]
    return idx[0] if idx else None
