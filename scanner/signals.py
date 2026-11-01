"""EMA + crossover maths. Mirrors the Pine script: ta.ema / ta.crossover / ta.crossunder."""
from __future__ import annotations

import numpy as np
import pandas as pd


def pine_ema(src: pd.Series, length: int) -> pd.Series:
    """TradingView ta.ema: alpha = 2/(length+1), seeded with the first value."""
    return src.ewm(alpha=2.0 / (length + 1), adjust=False).mean()


def cross_flags(short: pd.Series, long: pd.Series) -> tuple[pd.Series, pd.Series]:
    """ta.crossover (up) and ta.crossunder (down), evaluated bar by bar."""
    prev_s, prev_l = short.shift(1), long.shift(1)
    up = (short > long) & (prev_s <= prev_l)
    down = (short < long) & (prev_s >= prev_l)
    return up, down


def analyse(closed: pd.DataFrame, short_len: int, long_len: int, scan_last: int) -> dict:
    """`closed` = only fully closed bars, columns: close, end (UTC)."""
    close = closed["close"].astype(float).reset_index(drop=True)
    ends = closed["end"].reset_index(drop=True)
    n = len(close)
    es = pine_ema(close, short_len)
    el = pine_ema(close, long_len)
    up, dn = cross_flags(es, el)

    last_s, last_l = float(es.iloc[-1]), float(el.iloc[-1])
    state = "bull" if last_s > last_l else "bear" if last_s < last_l else "flat"

    crossed = np.flatnonzero((up | dn).to_numpy())
    last_cross_dir = last_cross_end = bars_since = None
    if len(crossed):
        i = int(crossed[-1])
        last_cross_dir = "bull" if bool(up.iloc[i]) else "bear"
        last_cross_end = ends.iloc[i]
        bars_since = n - 1 - i

    recent = []
    for i in range(max(1, n - scan_last), n):
        for flag, direction in ((up, "bull"), (dn, "bear")):
            if bool(flag.iloc[i]):
                recent.append({
                    "dir": direction,
                    "bar_end": ends.iloc[i],
                    "bars_ago": n - 1 - i,
                    "close": float(close.iloc[i]),
                    "ema_s": float(es.iloc[i]),
                    "ema_l": float(el.iloc[i]),
                })
    return {
        "bars": n,
        "last_end": ends.iloc[-1],
        "close": float(close.iloc[-1]),
        "ema_s": last_s,
        "ema_l": last_l,
        "gap_pct": (last_s / last_l - 1.0) * 100.0 if last_l else float("nan"),
        "state": state,
        "last_cross_dir": last_cross_dir,
        "last_cross_end": last_cross_end,
        "bars_since_cross": bars_since,
        "recent": recent,
    }
