"""Exchange sessions and bar timing (so we never judge a half-finished candle)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

TF_MINUTES = {"15m": 15, "1h": 60, "4h": 240}
BASE_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "1h", "1d": "1d", "1w": "1d"}
TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}


@dataclass(frozen=True)
class Market:
    key: str
    tz: str
    open_h: int
    open_m: int
    length_min: int          # session length in minutes
    start_prev_day: bool     # session starts the evening before its label date
    label_days: tuple        # weekdays (Mon=0) that carry a session label
    benchmark: str           # Yahoo symbol used to check data is ready


MARKETS = {
    # NSE equities + Indian indices: 09:15-15:30 IST
    "NSE": Market("NSE", "Asia/Kolkata", 9, 15, 375, False, (0, 1, 2, 3, 4), "^NSEI"),
    # US equities: 09:30-16:00 New York
    "US": Market("US", "America/New_York", 9, 30, 390, False, (0, 1, 2, 3, 4), "SPY"),
    # Crypto: 24x7, days run 00:00-24:00 UTC
    "CRYPTO": Market("CRYPTO", "UTC", 0, 0, 1440, False, (0, 1, 2, 3, 4, 5, 6), "BTC-USD"),
    # Global futures used as proxies for MCX: Sun-Fri, session 18:00 -> 17:00 New York
    "FUT": Market("FUT", "America/New_York", 18, 0, 1380, True, (0, 1, 2, 3, 4), "GC=F"),
}


def open_offset(m: Market) -> pd.Timedelta:
    off = pd.Timedelta(hours=m.open_h, minutes=m.open_m)
    return off - pd.Timedelta(days=1) if m.start_prev_day else off


def local_to_utc(naive: pd.DatetimeIndex, m: Market) -> pd.DatetimeIndex:
    return naive.tz_localize(m.tz, ambiguous="NaT", nonexistent="shift_forward").tz_convert("UTC")


def session_for(m: Market, d: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(start, end) in UTC of the session carrying label date `d`."""
    start = pd.Timestamp(d.year, d.month, d.day) + open_offset(m)
    end = start + pd.Timedelta(minutes=m.length_min)
    loc = lambda t: t.tz_localize(m.tz, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")
    return loc(start), loc(end)


def bar_ends(m: Market, d: date, minutes: int) -> list[pd.Timestamp]:
    """End time of every intraday bar of session `d` (last bar clipped to the close)."""
    start, end = session_for(m, d)
    step = pd.Timedelta(minutes=minutes)
    out, t = [], start + step
    while t < end:
        out.append(t)
        t += step
    out.append(end)
    return out


def week_end(m: Market, d: date) -> pd.Timestamp:
    monday = d - timedelta(days=d.weekday())
    return session_for(m, monday + timedelta(days=max(m.label_days)))[1]


def expected_latest_end(m: Market, tf: str, now: pd.Timestamp, settle_minutes: int):
    """Calendar-based end time of the newest bar that should be closed (and settled) by `now`.
    Ignores exchange holidays - a probe against the benchmark symbol handles those."""
    cutoff = now - pd.Timedelta(minutes=settle_minutes)
    today = now.tz_convert(m.tz).date()
    best = None
    if tf == "1w":
        monday0 = today - timedelta(days=today.weekday())
        for w in range(0, 6):
            e = week_end(m, monday0 - timedelta(weeks=w))
            if e <= cutoff and (best is None or e > best):
                best = e
        return best
    for off in range(-1, 10):
        d = today - timedelta(days=off)
        if d.weekday() not in m.label_days:
            continue
        ends = [session_for(m, d)[1]] if tf == "1d" else bar_ends(m, d, TF_MINUTES[tf])
        for e in ends:
            if e <= cutoff and (best is None or e > best):
                best = e
    return best
