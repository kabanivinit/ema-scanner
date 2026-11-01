"""Turn raw Yahoo candles into bars with exact start/end times (UTC), like TradingView builds them."""
from __future__ import annotations

import pandas as pd

from .markets import Market, local_to_utc, open_offset

COLS = ["open", "high", "low", "close", "volume"]


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS + ["start", "end"])


def clean(df):
    """Lower-case OHLCV, drop blanks/duplicates, sort by time. Returns None if unusable."""
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "close" not in df.columns:
        return None
    for c in ("open", "high", "low"):
        if c not in df.columns:
            df[c] = df["close"]
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[COLS]
    df = df[~df.index.isna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["close"])
    return df if len(df) else None


def intraday_bars(df, m: Market, minutes: int) -> pd.DataFrame:
    """Native intraday candles -> bars with `start`/`end` (end clipped to the session close)."""
    df = clean(df)
    if df is None:
        return empty_frame()
    idx = pd.DatetimeIndex(df.index)
    start_utc = idx.tz_convert("UTC") if idx.tz is not None else local_to_utc(idx, m)
    loc = pd.Series(start_utc.tz_convert(m.tz).tz_localize(None))       # wall-clock at the exchange
    length = pd.Timedelta(minutes=m.length_min)
    step = pd.Timedelta(minutes=minutes)
    open_today = loc.dt.normalize() + pd.Timedelta(hours=m.open_h, minutes=m.open_m)
    sess = open_today.where(loc >= open_today, open_today - pd.Timedelta(days=1))
    valid = ((loc - sess) < length).to_numpy()                            # drop stray out-of-session bars
    sess_end = sess + length
    nominal = loc + step
    end_naive = nominal.where(nominal <= sess_end, sess_end)
    out = df.reset_index(drop=True)
    out["start"] = pd.Series(start_utc)
    out["end"] = pd.Series(start_utc) + (end_naive - loc)
    out["_loc"] = loc
    out["_sess"] = sess
    return out[valid].reset_index(drop=True)


def block_bars(df, m: Market, minutes: int = 240) -> pd.DataFrame:
    """Build 4h bars from 1h candles, anchored on the session open (like TradingView)."""
    base = intraday_bars(df, m, 60)
    if base.empty:
        return empty_frame()
    step = pd.Timedelta(minutes=minutes)
    length = pd.Timedelta(minutes=m.length_min)
    base["_blk"] = ((base["_loc"] - base["_sess"]) // step).astype("int64")
    agg = (
        base.groupby(["_sess", "_blk"], sort=True)
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
             close=("close", "last"), volume=("volume", "sum"),
             f_start=("start", "min"), f_loc=("_loc", "min"), l_end=("end", "max"))
        .reset_index()
    )
    b_start = agg["_sess"] + agg["_blk"] * step
    sess_end = agg["_sess"] + length
    nominal = b_start + step
    b_end = nominal.where(nominal <= sess_end, sess_end)
    agg["end"] = agg["f_start"] + (b_end - agg["f_loc"])
    agg["start"] = agg["f_start"] - (agg["f_loc"] - b_start)
    complete = agg["l_end"] >= agg["end"]            # last hour of the block must be present
    return agg.loc[complete, COLS + ["start", "end"]].reset_index(drop=True)


def daily_bars(df, m: Market) -> pd.DataFrame:
    df = clean(df)
    if df is None:
        return empty_frame()
    idx = pd.DatetimeIndex(df.index)
    lab = (idx.tz_convert(m.tz).tz_localize(None) if idx.tz is not None else idx).normalize()
    out = df.reset_index(drop=True)
    out["_label"] = pd.Series(lab)
    out = out.drop_duplicates("_label", keep="last").sort_values("_label").reset_index(drop=True)
    s_start = out["_label"] + open_offset(m)
    s_end = s_start + pd.Timedelta(minutes=m.length_min)
    out["start"] = pd.Series(local_to_utc(pd.DatetimeIndex(s_start), m))
    out["end"] = pd.Series(local_to_utc(pd.DatetimeIndex(s_end), m))
    return out.dropna(subset=["end"]).reset_index(drop=True)


def weekly_bars(d: pd.DataFrame, m: Market) -> pd.DataFrame:
    """Weekly bars built from daily bars (Mon-Fri for stocks, Mon-Sun for crypto)."""
    if d.empty:
        return empty_frame()
    wk = (d["_label"] - pd.to_timedelta(d["_label"].dt.weekday, unit="D")).rename("_wk")
    agg = (
        d.groupby(wk, sort=True)
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
             close=("close", "last"), volume=("volume", "sum"))
        .reset_index()
    )
    last_day = pd.Timedelta(days=max(m.label_days))
    length = pd.Timedelta(minutes=m.length_min)
    agg["start"] = pd.Series(local_to_utc(pd.DatetimeIndex(agg["_wk"] + open_offset(m)), m))
    agg["end"] = pd.Series(local_to_utc(pd.DatetimeIndex(agg["_wk"] + last_day + open_offset(m) + length), m))
    return agg.dropna(subset=["end"]).reset_index(drop=True)


def frame_for(tf: str, raw_df, m: Market) -> pd.DataFrame:
    if tf == "15m":
        return intraday_bars(raw_df, m, 15)
    if tf == "1h":
        return intraday_bars(raw_df, m, 60)
    if tf == "4h":
        return block_bars(raw_df, m, 240)
    d = daily_bars(raw_df, m)
    return d if tf == "1d" else weekly_bars(d, m)


def closed_only(frame: pd.DataFrame, now: pd.Timestamp, settle_minutes: int) -> pd.DataFrame:
    """Keep only bars that finished (plus a small settle delay) before `now`."""
    if frame.empty:
        return frame
    return frame[frame["end"] <= now - pd.Timedelta(minutes=settle_minutes)].reset_index(drop=True)
