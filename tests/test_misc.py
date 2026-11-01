import pandas as pd
import pytest

from scanner import alerts, data, telegram, universe
from scanner.config import ConfigError, load_config
from scanner.engine import Signal
from scanner.universe import Instrument
from synth import intraday_raw
from datetime import date


def mk(symbol, name, market="NSE", unis=("nifty500",), cur="₹", tv=None):
    return Instrument(symbol=symbol, name=name, market=market, universes=list(unis),
                      tv=name if tv is None else tv, currency=cur, tfs={"15m"})


def sig(i, tf="15m", d="bull", end="2026-10-30 09:30", close=100.0):
    t = pd.Timestamp(end, tz="Asia/Kolkata").tz_convert("UTC")
    return Signal(i, tf, d, t, close, close * 1.002, close)


# ------------------------------------------------------------------ alerts
def test_messages_are_chunked_under_telegram_limit_and_lose_nothing():
    cfg = load_config()
    sigs = [sig(mk(f"S{n:03d}.NS", f"S{n:03d}", tv=f"NSE:S{n:03d}")) for n in range(150)]
    msgs = alerts.build_messages(sigs, cfg)
    assert len(msgs) >= 4 and all(len(m.text) < 4096 for m in msgs)
    assert sorted(s.key for m in msgs for s in m.signals) == sorted(s.key for s in sigs)
    assert all(m.text.startswith("🟢 <b>BULLISH CROSS · 15M</b>") for m in msgs)


def test_bullish_before_bearish_and_timeframes_in_order_and_html_escaped():
    cfg = load_config()
    a = mk("AAA.NS", "AAA")
    b = mk("MMM", "MMM", market="US", unis=("sp500", "nasdaq100"), cur="$")
    sigs = [sig(a, d="bear"), sig(a, tf="1h", end="2026-10-30 10:15"), sig(b, tf="1d", end="2026-10-30 21:30"),
            sig(a, d="bull")]
    msgs = alerts.build_messages(sigs, cfg)
    heads = [m.text.splitlines()[0] for m in msgs]
    assert heads[0].startswith("🟢") and "15M" in heads[0]
    assert "BEARISH CROSS · 15M" in heads[1] and "1H" in heads[2] and "1D" in heads[3]
    daily = msgs[3].text
    assert "Nasdaq 100 + S&amp;P 500" in daily and "Daily close 30 Oct 2026" in daily
    assert 'href="https://www.tradingview.com/chart/?symbol=MMM"' in daily
    assert "$100.00" in daily and "(+0.20%)" in daily


def test_links_can_be_switched_off_and_names_are_escaped():
    cfg = load_config()
    cfg["alerts"]["tradingview_links"] = False
    text = alerts.build_messages([sig(mk("M&M.NS", "M&M"))], cfg)[0].text
    assert "href" not in text and "M&amp;M" in text


# ------------------------------------------------------------------ telegram
def test_rate_limit_is_respected_and_token_never_leaks():
    calls, sleeps = [], []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    def post(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            return R(429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 3}})
        return R(200, {"ok": True})

    tg = telegram.Telegram("111:TOKEN", "1", post=post, sleep=sleeps.append)
    assert tg.send("hi") and sleeps == [4]

    def boom(url, **kw):
        raise RuntimeError(f"HTTPSConnectionPool: failed for {url}")

    tg2 = telegram.Telegram("111:TOKEN", "1", post=boom, sleep=lambda s: None)
    assert not tg2.send("hi") and "111:TOKEN" not in tg2.last_error


def test_html_parse_error_falls_back_to_plain_text():
    seen = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    def post(url, json=None, **kw):
        seen.append(dict(json))
        if "parse_mode" in json:
            return R(400, {"ok": False, "description": "Bad Request: can't parse entities"})
        return R(200, {"ok": True})

    tg = telegram.Telegram("1:T", "1", post=post, sleep=lambda s: None)
    assert tg.send("<b>hello</b> <a href='x'>y</a>") and seen[-1]["text"] == "hello y"


# ------------------------------------------------------------------ yahoo parsing
def _two():
    a = intraday_raw("NSE", 15, date(2026, 10, 30), 1, lambda n: list(range(1, n + 1)))
    b = a * 2
    return a, b


def test_split_handles_ticker_first_and_price_first_layouts_and_single_ticker():
    a, b = _two()
    ticker_first = pd.concat({"AAA.NS": a, "BBB.NS": b}, axis=1)
    price_first = ticker_first.swaplevel(axis=1).sort_index(axis=1)
    for raw in (ticker_first, price_first):
        out = data._split(raw, ["AAA.NS", "BBB.NS", "CCC.NS"])
        assert set(out) == {"AAA.NS", "BBB.NS"} and out["BBB.NS"]["Close"].iloc[-1] == b["Close"].iloc[-1]
    assert set(data._split(a, ["AAA.NS"])) == {"AAA.NS"}
    assert data._split(pd.DataFrame(), ["AAA.NS"]) == {}


# ------------------------------------------------------------------ universes
NSE_CSV = "Company Name,Industry,Symbol,Series,ISIN Code\n" + "\n".join(
    f"Company {i},Industry,SYM{i},EQ,INE{i:05d}" for i in range(30)) + "\nMahindra & Mahindra,Auto,M&M,EQ,INE1\n"
SP_HTML = ("<table><tr><th>Symbol</th><th>Security</th></tr>" +
           "".join(f"<tr><td>T{i}</td><td>Name {i}</td></tr>" for i in range(30)) +
           "<tr><td>BRK.B</td><td>Berkshire</td></tr></table>")
NDX_HTML = ("<table><tr><th>Ticker</th><th>Company</th></tr>" +
            "".join(f"<tr><td>N{i}</td><td>Name {i}</td></tr>" for i in range(30)) + "</table>")


def test_downloaders_parse_nse_and_wikipedia(monkeypatch):
    monkeypatch.setattr(universe, "_http_get", lambda url: NSE_CSV)
    nse = universe.fetch_nifty500()
    assert len(nse) == 31 and "M&M.NS" in set(nse["symbol"]) and universe.tv_nse("M&M.NS") == "NSE:M_M"
    monkeypatch.setattr(universe, "_http_get", lambda url: SP_HTML)
    assert "BRK-B" in set(universe.fetch_sp500()["symbol"])
    monkeypatch.setattr(universe, "_http_get", lambda url: NDX_HTML)
    assert len(universe.fetch_nasdaq100()) == 30


def test_list_cache_is_used_when_download_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(universe, "_http_get", lambda url: NSE_CSV)
    df, note = universe._load_list("nifty500", universe.fetch_nifty500, 7)
    assert "downloaded" in note and len(df) == 31
    df, note = universe._load_list("nifty500", universe.fetch_nifty500, 7)
    assert "saved copy" in note                                          # fresh -> no download
    monkeypatch.setattr(universe, "_http_get", lambda url: (_ for _ in ()).throw(RuntimeError("blocked")))
    df, note = universe._load_list("nifty500", universe.fetch_nifty500, 0)   # stale + blocked -> fallback
    assert "download failed" in note and len(df) == 31
    monkeypatch.setattr(universe, "CACHE_DIR", tmp_path / "empty")
    with pytest.raises(RuntimeError):
        universe._load_list("nifty500", universe.fetch_nifty500, 0)


def test_build_merges_overlapping_universes_and_uses_config_lists():
    cfg = load_config()
    insts, notes = universe.build(cfg, only={"crypto", "commodities", "indian_indices"})
    by = {i.symbol: i for i in insts}
    assert by["BTC-USD"].market == "CRYPTO" and by["BTC-USD"].tv == "BTCUSD" and by["BTC-USD"].display == "BTC"
    assert by["UNI7083-USD"].display == "Uniswap"
    assert by["GC=F"].market == "FUT" and by["GC=F"].tv == "MCX:GOLD1!"
    assert by["^NSEI"].market == "NSE" and by["^NSEI"].display == "Nifty 50"
    assert all(i.tfs == {"15m", "1h", "4h", "1d", "1w"} for i in insts) and all(
        not v.startswith("FAILED") for v in notes.values())
    cfg["universes"]["dup"] = dict(cfg["universes"]["crypto"], label="Dup", timeframes=["1d"])
    insts, _ = universe.build(cfg, only={"crypto", "dup"})
    btc = [i for i in insts if i.symbol == "BTC-USD"]
    assert len(btc) == 1 and btc[0].universes == ["crypto", "dup"]


# ------------------------------------------------------------------ config
def test_default_config_is_valid_and_covers_all_six_groups():
    cfg = load_config()
    assert cfg["ema"] == {"short": 10, "long": 20}
    assert cfg["timeframes"] == ["15m", "1h", "4h", "1d", "1w"]
    assert set(cfg["universes"]) == {"nifty500", "indian_indices", "nasdaq100", "sp500", "crypto", "commodities"}


def test_config_mistakes_are_explained(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text("timeframes: ['2h']\nuniverses: {a: {market: NSE, source: list, list: x}}\nlists: {x: []}\n")
    with pytest.raises(ConfigError, match="unknown timeframe"):
        load_config(bad)
    bad.write_text("ema: {short: 30, long: 20}\n")
    with pytest.raises(ConfigError, match="smaller"):
        load_config(bad)
    bad.write_text("ema: [unclosed\n")
    with pytest.raises(ConfigError, match="formatting mistake"):
        load_config(bad)


# ------------------------------------------------------------------ yahoo throttling
class _FakeYF:
    """Stands in for yfinance: logs 'Too Many Requests' the way yfinance does, for the first N calls."""
    def __init__(self, throttled_calls, frame):
        self.left, self.frame, self.calls = throttled_calls, frame, 0

    def download(self, tickers, **kw):
        import logging
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            logging.getLogger("yfinance").error("['AAA.NS']: Too Many Requests. Rate limited. Try after a while.")
            return pd.DataFrame()
        return pd.concat({t: self.frame for t in tickers}, axis=1)


YC = {"threads": 2, "batch_size": 2, "pause_seconds": 0, "retries": 1, "max_seconds": 60,
      "periods": {"15m": "30d", "1h": "90d", "1d": "3y"}}


def test_throttling_is_detected_waited_out_and_retried():
    a, _ = _two()
    sleeps = []
    fy = _FakeYF(1, a)
    got = data._download(fy, ["AAA.NS", "BBB.NS"], "15m", "30d", YC, sleeps.append)
    assert set(got) == {"AAA.NS", "BBB.NS"} and 45 in sleeps and fy.calls == 2


def test_persistent_throttling_stops_the_run_instead_of_hammering_yahoo(monkeypatch):
    a, _ = _two()
    fy = _FakeYF(99, a)
    sleeps = []
    assert data._download(fy, ["AAA.NS"], "15m", "30d", YC, sleeps.append) is None
    import sys, types
    mod = types.ModuleType("yfinance")
    mod.download = fy.download
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    out, failed = data.fetch_yahoo(["A.NS", "B.NS", "C.NS", "D.NS"], "15m", YC, sleep=lambda s: None)
    assert out == {} and failed == ["A.NS", "B.NS", "C.NS", "D.NS"] and fy.calls <= 4   # gave up quickly


def test_fetch_yahoo_collects_good_symbols_and_reports_missing(monkeypatch):
    import sys, types
    a, _ = _two()

    class Y:
        def download(self, tickers, **kw):
            good = [t for t in tickers if t != "BAD.NS"]
            return pd.concat({t: a for t in good}, axis=1) if good else pd.DataFrame()

    mod = types.ModuleType("yfinance")
    mod.download = Y().download
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    out, failed = data.fetch_yahoo(["AAA.NS", "BAD.NS", "CCC.NS"], "15m", YC, sleep=lambda s: None)
    assert set(out) == {"AAA.NS", "CCC.NS"} and failed == ["BAD.NS"]


def test_hand_uploaded_nse_file_is_accepted_as_is(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "CACHE_DIR", tmp_path)
    (tmp_path / "nifty500.csv").write_text(NSE_CSV, encoding="utf-8")        # raw NSE format, no stamp
    monkeypatch.setattr(universe, "_http_get", lambda url: (_ for _ in ()).throw(RuntimeError("must not download")))
    rows, note = universe._rows_for(load_config()["universes"]["nifty500"], load_config())
    assert note == "your uploaded file" and len(rows) == 31
    assert rows[0]["symbol"] == "SYM0.NS" and {"symbol": "M&M.NS", "name": "Mahindra & Mahindra", "tv": "NSE:M_M"} in rows
