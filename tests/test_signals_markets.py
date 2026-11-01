from datetime import date

import numpy as np
import pandas as pd
import pytest

from scanner import bars, signals
from scanner.markets import MARKETS, bar_ends, expected_latest_end, session_for, week_end
from synth import daily_raw, intraday_raw, uptrend, vshape

UTC = "UTC"
NSE, US, CRYPTO = MARKETS["NSE"], MARKETS["US"], MARKETS["CRYPTO"]


def ist(s):
    return pd.Timestamp(s, tz="Asia/Kolkata").tz_convert(UTC)


# ------------------------------------------------------------------ signals
def test_pine_ema_matches_manual_recursion():
    x = pd.Series([10, 11, 12, 11, 13, 15, 14, 16.0])
    a = 2 / (4 + 1)
    manual = [x[0]]
    for v in x[1:]:
        manual.append(a * v + (1 - a) * manual[-1])
    assert np.allclose(signals.pine_ema(x, 4), manual)


def test_cross_flags_match_pine_definition():
    s = pd.Series([1, 2, 3, 2, 1.0])
    l = pd.Series([2, 2, 2, 2, 2.0])
    up, dn = signals.cross_flags(s, l)
    assert up.tolist() == [False, False, True, False, False]
    assert dn.tolist() == [False, False, False, False, True]


def test_equality_semantics_match_pine():
    # Pine: crossover = now strictly above AND previous bar <= ; crossunder = now strictly below AND previous >=
    l = pd.Series([2, 2, 2, 2.0])
    up, dn = signals.cross_flags(pd.Series([1, 2, 3, 3.0]), l)      # touches (=) on bar 1, exceeds on bar 2
    assert up.tolist() == [False, False, True, False] and not dn.any()
    up, dn = signals.cross_flags(pd.Series([1, 2, 2, 1.0]), l)      # touch, stay, then drop below on bar 3
    assert not up.any() and dn.tolist() == [False, False, False, True]


def test_analyse_reports_recent_cross_and_state():
    close = vshape(200)
    fr = pd.DataFrame({"close": close, "end": pd.date_range("2026-01-01", periods=200, freq="15min", tz=UTC)})
    k = None
    up, _ = signals.cross_flags(signals.pine_ema(fr["close"], 10), signals.pine_ema(fr["close"], 20))
    k = int(np.flatnonzero(up.to_numpy())[0])
    out = signals.analyse(fr.iloc[: k + 1], 10, 20, 3)
    assert [r["dir"] for r in out["recent"]] == ["bull"] and out["recent"][0]["bars_ago"] == 0
    assert out["state"] == "bull" and out["bars_since_cross"] == 0
    later = signals.analyse(fr.iloc[: k + 3], 10, 20, 2)       # cross is now 2 bars old, outside window of 2
    assert later["recent"] == [] and later["bars_since_cross"] == 2


# ------------------------------------------------------------------ session maths
def test_nse_bar_ends():
    d = date(2026, 10, 30)
    e15 = bar_ends(NSE, d, 15)
    assert len(e15) == 25 and e15[0] == ist("2026-10-30 09:30") and e15[-1] == ist("2026-10-30 15:30")
    e1h = bar_ends(NSE, d, 60)
    assert [t.tz_convert("Asia/Kolkata").strftime("%H:%M") for t in e1h] == \
        ["10:15", "11:15", "12:15", "13:15", "14:15", "15:15", "15:30"]
    e4h = bar_ends(NSE, d, 240)
    assert [t.tz_convert("Asia/Kolkata").strftime("%H:%M") for t in e4h] == ["13:15", "15:30"]


def test_us_sessions_follow_daylight_saving():
    s1, _ = session_for(US, date(2026, 10, 30))      # EDT (UTC-4)
    s2, _ = session_for(US, date(2026, 11, 2))       # EST (UTC-5), DST ended Sunday 1 Nov
    assert s1 == pd.Timestamp("2026-10-30 13:30", tz=UTC)
    assert s2 == pd.Timestamp("2026-11-02 14:30", tz=UTC)


def test_expected_latest_end_nse():
    fri_eve = ist("2026-10-30 16:00")
    assert expected_latest_end(NSE, "15m", fri_eve, 1) == ist("2026-10-30 15:30")
    assert expected_latest_end(NSE, "4h", fri_eve, 2) == ist("2026-10-30 15:30")
    assert expected_latest_end(NSE, "1d", fri_eve, 15) == ist("2026-10-30 15:30")
    assert expected_latest_end(NSE, "1w", fri_eve, 15) == ist("2026-10-30 15:30")
    # 15:40 - the daily candle has not settled yet (15 min), so Thursday is still the newest
    assert expected_latest_end(NSE, "1d", ist("2026-10-30 15:40"), 15) == ist("2026-10-29 15:30")
    assert expected_latest_end(NSE, "1w", ist("2026-10-30 15:40"), 15) == ist("2026-10-23 15:30")
    # Saturday: still Friday's close
    assert expected_latest_end(NSE, "15m", ist("2026-10-31 12:00"), 1) == ist("2026-10-30 15:30")
    # Monday just after the open
    assert expected_latest_end(NSE, "15m", ist("2026-11-02 09:32"), 1) == ist("2026-11-02 09:30")
    assert expected_latest_end(NSE, "4h", ist("2026-11-02 09:32"), 2) == ist("2026-10-30 15:30")


def test_expected_latest_end_crypto_week():
    now = pd.Timestamp("2026-10-31 19:38", tz=UTC)               # Saturday evening UTC
    assert expected_latest_end(CRYPTO, "4h", now, 2) == pd.Timestamp("2026-10-31 16:00", tz=UTC)
    assert expected_latest_end(CRYPTO, "1d", now, 15) == pd.Timestamp("2026-10-31 00:00", tz=UTC)
    assert expected_latest_end(CRYPTO, "1w", now, 15) == pd.Timestamp("2026-10-26 00:00", tz=UTC)
    assert week_end(CRYPTO, date(2026, 10, 28)) == pd.Timestamp("2026-11-02 00:00", tz=UTC)


# ------------------------------------------------------------------ bar building
def test_intraday_end_is_clipped_at_close():
    raw = intraday_raw("NSE", 60, date(2026, 10, 30), 3, lambda n: uptrend(n))
    fr = bars.intraday_bars(raw, NSE, 60)
    last = fr[fr["start"].dt.tz_convert("Asia/Kolkata").dt.strftime("%H:%M") == "15:15"].iloc[-1]
    assert last["end"] == ist("2026-10-30 15:30")                # 15-minute final candle


def test_closed_only_drops_forming_candle():
    raw = intraday_raw("NSE", 15, date(2026, 10, 30), 3, lambda n: uptrend(n))
    fr = bars.intraday_bars(raw, NSE, 15)
    assert bars.closed_only(fr, ist("2026-10-30 14:44"), 1)["end"].max() == ist("2026-10-30 14:30")
    assert bars.closed_only(fr, ist("2026-10-30 14:46"), 1)["end"].max() == ist("2026-10-30 14:45")


def test_4h_blocks_follow_session_open_and_ohlc():
    close = np.arange(1, 8, dtype=float)                          # 7 hourly bars: 1..7
    raw = intraday_raw("NSE", 60, date(2026, 10, 30), 1, lambda n: close)
    fr = bars.block_bars(raw, NSE, 240)
    assert len(fr) == 2
    first, second = fr.iloc[0], fr.iloc[1]
    assert first["end"] == ist("2026-10-30 13:15") and second["end"] == ist("2026-10-30 15:30")
    assert first["open"] == 1 and first["close"] == 4 and second["open"] == 5 and second["close"] == 7
    assert first["volume"] == 4000 and second["volume"] == 3000


def test_incomplete_4h_block_is_dropped():
    raw = intraday_raw("NSE", 60, date(2026, 10, 30), 1, lambda n: np.arange(1, 8, dtype=float))
    raw = raw.drop(raw.index[3])                                  # remove the 12:15 candle (last of block 1)
    fr = bars.block_bars(raw, NSE, 240)
    assert len(fr) == 1 and fr.iloc[0]["end"] == ist("2026-10-30 15:30")


def test_crypto_4h_blocks_anchor_at_midnight_utc():
    raw = intraday_raw("CRYPTO", 60, date(2026, 10, 30), 2, lambda n: uptrend(n))
    fr = bars.block_bars(raw, CRYPTO, 240)
    assert {t.hour for t in fr["end"]} == {0, 4, 8, 12, 16, 20}
    assert len(fr) == 12


def test_daily_and_weekly_end_times():
    raw = daily_raw("NSE", date(2026, 10, 30), 30, lambda n: uptrend(n))
    d = bars.daily_bars(raw, NSE)
    assert d["end"].iloc[-1] == ist("2026-10-30 15:30")
    w = bars.weekly_bars(d, NSE)
    assert w["end"].iloc[-1] == ist("2026-10-30 15:30")
    # mid-week the current week is still forming
    mid = bars.closed_only(w, ist("2026-10-29 17:00"), 15)
    assert mid["end"].max() == ist("2026-10-23 15:30")


def test_weekly_uses_monday_to_friday_ohlc():
    raw = daily_raw("NSE", date(2026, 10, 30), 10, lambda n: np.arange(1, n + 1, dtype=float))
    w = bars.weekly_bars(bars.daily_bars(raw, NSE), NSE)
    last = w.iloc[-1]
    assert last["open"] == 6 and last["close"] == 10             # Mon 26 Oct .. Fri 30 Oct = bars 6..10
    assert last["high"] == pytest.approx(10 * 1.001) and last["low"] == pytest.approx(6 * 0.999)


def test_tz_naive_intraday_is_treated_as_exchange_local():
    raw = intraday_raw("NSE", 15, date(2026, 10, 30), 2, lambda n: uptrend(n))
    naive = raw.copy()
    naive.index = naive.index.tz_localize(None)
    a = bars.intraday_bars(raw, NSE, 15)["end"]
    b = bars.intraday_bars(naive, NSE, 15)["end"]
    assert a.tolist() == b.tolist()
