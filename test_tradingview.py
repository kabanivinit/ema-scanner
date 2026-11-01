"""TradingView data source - tested against a pretend TradingView (nothing here touches the internet)."""
import json
from datetime import date

import pandas as pd
import pytest

from scanner import bars, cli, data, engine, telegram, tv, universe
from scanner.config import ConfigError, load_config
from scanner.markets import MARKETS, session_for, settle_for
from scanner.universe import Instrument
from synth import intraday_raw, vshape

NSE, US = MARKETS["NSE"], MARKETS["US"]
FRI = date(2026, 10, 30)


# ===================================================================== a pretend TradingView
def good(cs, rows, interval="15"):
    return [tv.encode("timescale_update", [cs, {"sds_1": {"s": rows}}, {"index": 0}]),
            tv.encode("series_completed", [cs, "sds_1", "s1", "sds_sym_1", interval])]


def flat_rows(n=80, step=900, start=1_700_000_000, price=100.0):
    return [{"i": k, "v": [start + k * step, price, price + 1, price - 1, price, 1000]} for k in range(n)]


class FakeWS:
    """Reads what the scanner sends and answers like the real feed (queued frames, then 'connection closed')."""

    def __init__(self, net):
        self.net, self.sent, self.out, self.sessions, self.closed = net, [], [], {}, False

    def settimeout(self, t):
        pass

    def send(self, text):
        self.sent.append(text)
        for kind, msg in tv.decode(text):
            if kind != "msg":
                continue
            m, p = msg["m"], msg["p"]
            if m == "resolve_symbol":
                self.sessions[p[0]] = {"symbol": json.loads(p[2][1:])["symbol"]}
            elif m == "create_series":
                info = self.sessions[p[0]]
                info.update(interval=p[4], n=p[5])
                self.net.asked.append((info["symbol"], p[4], p[5]))
                self.out.extend(self.net.answer(p[0], info))
            elif m == "quote_add_symbols":
                mode = self.net.modes.get(p[1])
                self.out.append(tv.encode("qsd", [p[0], {"n": p[1], "s": "ok",
                                                          "v": {"update_mode": mode, "lp_time": 1_700_000_000}}]))

    def recv(self):
        if not self.out:
            raise ConnectionError("closed")
        return self.out.pop(0)

    def close(self):
        self.closed = True


class Net:
    """routes: TradingView symbol -> 'ok' | 'bad' | 'broken' | 'empty' | 'silent' | list of those (one per request)."""

    def __init__(self, routes=None, rows=None, modes=None, rows_fn=None):
        self.routes, self.rows, self.modes, self.rows_fn = dict(routes or {}), rows or flat_rows(), modes or {}, rows_fn
        self.conns, self.asked = [], []

    def connect(self):
        ws = FakeWS(self)
        self.conns.append(ws)
        return ws

    def answer(self, cs, info):
        kind = self.routes.get(info["symbol"], "ok")
        if isinstance(kind, list):
            kind = kind.pop(0) if len(kind) > 1 else kind[0]
        if kind == "ok":
            rows = self.rows_fn(info["symbol"], info["interval"]) if self.rows_fn else self.rows
            return good(cs, rows, info["interval"])
        if kind == "bad":
            return [tv.encode("symbol_error", [cs, "sds_sym_1", "invalid symbol"])]
        if kind == "broken":
            return [tv.encode("series_error", [cs, "sds_1", "boom"])]
        if kind == "empty":
            return [tv.encode("series_completed", [cs, "sds_1", "s1", "sds_sym_1", info["interval"]])]
        return []                                              # silent


TC = {"bars": {"15m": 50, "1h": 50, "1d": 50}, "batch_size": 3, "pause_seconds": 0.5, "timeout_seconds": 5,
      "retries": 1, "max_seconds": 100, "stop_after_failed_batches": 3}


def make_feed(net, tvmap, tmp_path=None, today=None, clock=None, **over):
    sleeps = []
    f = tv.TradingViewFeed(dict(TC, **over), tvmap, cache_path=(tmp_path / "tv.json") if tmp_path else None,
                           connect=net.connect, sleep=sleeps.append, today=today or (lambda: date(2026, 11, 1)),
                           clock=clock)
    f.sleeps = sleeps
    return f


# ===================================================================== wire format
def test_wire_format_round_trip_with_several_messages_in_one_frame():
    body = json.dumps({"m": "set_auth_token", "p": ["unauthorized_user_token"]}, separators=(",", ":"))
    assert tv.encode("set_auth_token", ["unauthorized_user_token"]) == f"~m~{len(body)}~m~{body}"
    two = tv.encode("a", [1]) + tv.encode("b", [2]) + "~m~4~m~~h~7" + "~m~5~m~oops!"
    assert tv.decode(two) == [("msg", {"m": "a", "p": [1]}), ("msg", {"m": "b", "p": [2]}), ("hb", "~h~7")]


def test_candle_rows_become_a_sorted_utc_frame_and_bad_rows_are_skipped():
    store = {}
    tv.merge_rows(store, [{"i": 1, "v": [200, 2, 3, 1, 2.5, 10]}, {"i": 0, "v": [100, 1, 2, 0.5, 1.5]},
                          {"i": 2, "v": [None, 1, 1, 1, 1]}, {"i": 3, "v": [300, "x", 1, 1, 1]}, "junk",
                          {"i": 4, "v": [200, 2, 3, 1, 2.75, 12]}])               # later row for the same time wins
    df = tv.to_frame(store)
    assert list(df.index.astype("int64") // 10 ** 9) == [100, 200] and str(df.index.tz) == "UTC"
    assert df["close"].tolist() == [1.5, 2.75] and df["volume"].tolist() == [0.0, 12.0]
    assert tv.to_frame({}) is None


# ===================================================================== one connection
def test_run_batch_reads_candles_reports_each_failure_kind_and_echoes_heartbeats():
    net = Net({"B": "bad", "C": "broken", "D": "silent", "E": "empty"})
    orig = net.answer
    net.answer = lambda cs, info: (["~m~4~m~~h~1"] if info["symbol"] == "A" else []) + orig(cs, info)
    res = tv.run_batch(net.connect, ["A", "B", "C", "D", "E"], "15", 50, 5)
    assert list(res.frames) == ["A"] and len(res.frames["A"]) == 80
    assert list(res.invalid) == ["B"] and "boom" in res.failed["C"] and res.failed["E"] == "no candles"
    assert "D" in res.failed and res.error                                      # never answered; connection ended
    ws = net.conns[0]
    assert "~m~4~m~~h~1" in ws.sent and ws.closed                                  # heartbeat echoed, socket closed
    first = tv.decode(ws.sent[0])[0][1]
    assert first == {"m": "set_auth_token", "p": ["unauthorized_user_token"]}   # never logged in
    assert not any("sessionid" in s or "password" in s.lower() for s in ws.sent)
    assert ("A", "15", 50) in net.asked


def test_a_protocol_error_aborts_the_batch_with_a_readable_reason():
    net = Net()
    net.answer = lambda cs, info: [tv.encode("protocol_error", ["session limit"])]
    res = tv.run_batch(net.connect, ["A", "B"], "15", 50, 5)
    assert not res.frames and "refused" in res.error and set(res.failed) == {"A", "B"}


def test_default_connect_tries_each_origin_then_explains(monkeypatch):
    import websocket
    seen = []

    def fake(url, origin=None, timeout=None, **kw):
        seen.append(origin)
        if origin == tv.ORIGINS[0]:
            raise RuntimeError("403 Forbidden")
        return "connected"

    monkeypatch.setattr(websocket, "create_connection", fake)
    assert tv.default_connect() == "connected" and seen == list(tv.ORIGINS)
    monkeypatch.setattr(websocket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
    with pytest.raises(tv.TVError, match="blocked"):
        tv.default_connect()


# ===================================================================== the feed
def test_symbols_are_downloaded_in_small_batches_with_pauses():
    net = Net()
    syms = [f"S{i}" for i in range(7)]
    f = make_feed(net, {s: [f"NSE:{s}"] for s in syms})
    out, failed = f.fetch(syms, "15m")
    assert set(out) == set(syms) and failed == []
    assert len(net.conns) == 3 and f.sleeps == [0.5, 0.5]                   # 3+3+1 symbols, rest between batches
    assert all(n == 50 for _, _, n in net.asked) and {iv for _, iv, _ in net.asked} == {"15"}


def test_each_base_interval_is_requested_with_its_own_resolution_and_candle_count():
    net = Net()
    f = make_feed(net, {"X": ["NSE:X"]}, bars={"15m": 11, "1h": 22, "1d": 33})
    for itv in ("15m", "1h", "1d"):
        f.fetch(["X"], itv)
    assert net.asked == [("NSE:X", "15", 11), ("NSE:X", "60", 22), ("NSE:X", "1D", 33)]
    assert f.fetch(["X"], "5m") == ({}, ["X"])                                  # an interval we never use is refused


def test_interval_names_match_what_tradingview_expects():
    assert tv.INTERVAL == {"15m": "15", "1h": "60", "1d": "1D"}


def test_second_choice_symbol_is_tried_and_remembered(tmp_path):
    net = Net({"NYSE:AAA": "bad"})
    f = make_feed(net, {"AAA": ["NYSE:AAA", "NASDAQ:AAA"], "BBB": ["NYSE:BBB"]}, tmp_path)
    out, failed = f.fetch(["AAA", "BBB"], "15m")
    assert set(out) == {"AAA", "BBB"} and failed == [] and f.winners["AAA"] == "NASDAQ:AAA"
    assert json.loads((tmp_path / "tv.json").read_text())["winners"]["AAA"] == "NASDAQ:AAA"
    net2 = Net()
    f2 = make_feed(net2, {"AAA": ["NYSE:AAA", "NASDAQ:AAA"]}, tmp_path)           # next run: straight to the winner
    f2.fetch(["AAA"], "15m")
    assert [s for s, _, _ in net2.asked] == ["NASDAQ:AAA"]
    assert universe.load_winners(tmp_path / "tv.json") == {"AAA": "NASDAQ:AAA", "BBB": "NYSE:BBB"}


def test_unknown_symbols_are_remembered_for_two_days_but_only_when_the_feed_is_healthy(tmp_path):
    day = [date(2026, 11, 1)]
    net = Net({"NSE:NOPE": "bad"})
    f = make_feed(net, {"OK": ["NSE:OK"], "NOPE": ["NSE:NOPE"]}, tmp_path, today=lambda: day[0])
    out, failed = f.fetch(["OK", "NOPE"], "15m")
    assert set(out) == {"OK"} and failed == ["NOPE"] and f.invalid == {"NOPE"}
    n = len(net.asked)
    f.fetch(["OK", "NOPE"], "15m")                                              # remembered -> not asked again
    assert [s for s, _, _ in net.asked[n:]] == ["NSE:OK"] and f.invalid == {"NOPE"}
    day[0] = date(2026, 11, 4)                                                  # three days later: ask again
    f.fetch(["NOPE"], "15m")
    assert net.asked[-1][0] == "NSE:NOPE"


def test_unknown_symbol_answers_are_not_trusted_when_nothing_else_works():
    net = Net({f"NSE:S{i}": "bad" for i in range(6)})                           # a bad day: everything "unknown"
    f = make_feed(net, {f"S{i}": [f"NSE:S{i}"] for i in range(6)}, batch_size=2)
    out, failed = f.fetch([f"S{i}" for i in range(6)], "15m")
    assert out == {} and len(failed) == 6 and not f.invalid and not f.bad       # nobody blacklisted
    assert f.down                                                               # 3 empty batches in a row -> switched off


def test_silent_failures_are_retried_once_then_left_to_the_backup():
    net = Net({"NSE:FLAKY": ["silent", "ok"], "NSE:DEAD": "silent"})
    f = make_feed(net, {"FLAKY": ["NSE:FLAKY"], "DEAD": ["NSE:DEAD"], "FINE": ["NSE:FINE"]})
    out, failed = f.fetch(["FLAKY", "DEAD", "FINE"], "15m")
    assert set(out) == {"FLAKY", "FINE"} and failed == ["DEAD"] and not f.invalid
    assert [s for s, _, _ in net.asked].count("NSE:DEAD") == 2 and not f.down


def test_three_silent_batches_in_a_row_switch_tradingview_off_for_the_run():
    net = Net({f"NSE:S{i}": "silent" for i in range(5)})
    f = make_feed(net, {f"S{i}": [f"NSE:S{i}"] for i in range(5)}, batch_size=1)
    out, failed = f.fetch([f"S{i}" for i in range(5)], "15m")
    assert out == {} and f.down and len(net.conns) == 3 and 5 in f.sleeps          # gave up after 3 batches
    assert f.fetch(["S0"], "15m") == ({}, ["S0"]) and len(net.conns) == 3          # and stays quiet


def test_time_budget_stops_further_downloads():
    t = [0.0]
    net = Net()
    f = make_feed(net, {f"S{i}": [f"NSE:S{i}"] for i in range(9)}, clock=lambda: t[0], max_seconds=100)
    f.sleep = lambda s: t.__setitem__(0, t[0] + 1000)                              # the pause "takes" a long time
    out, failed = f.fetch([f"S{i}" for i in range(9)], "15m")
    assert len(out) == 3 and len(failed) == 6 and len(net.conns) == 1


def test_symbols_without_a_tradingview_name_are_left_for_the_backup():
    net = Net()
    out, failed = make_feed(net, {"A": ["NSE:A"]}).fetch(["A", "NOMAP"], "15m")
    assert set(out) == {"A"} and failed == ["NOMAP"] and len(net.asked) == 1


def test_scanner_benchmarks_have_tradingview_names():
    for m in MARKETS.values():
        assert m.benchmark in tv.BENCH_TV, m.key


def test_data_delay_modes_are_read_from_tradingview():
    assert [tv.delay_minutes(x) for x in ("delayed_streaming_900", "streaming", "endofday", None)] == [15, 0, None, None]
    net = Net(modes={"NSE:NIFTY": "delayed_streaming_900", "BINANCE:BTCUSDT": "streaming"})
    got = make_feed(net, {}).quote_modes(["NSE:NIFTY", "BINANCE:BTCUSDT"])
    assert got["NSE:NIFTY"]["update_mode"] == "delayed_streaming_900" and got["BINANCE:BTCUSDT"]["update_mode"] == "streaming"


# ===================================================================== TradingView + Yahoo together
def inst(sym, feed, market="NSE", optional=False):
    return Instrument(symbol=sym, name=sym, market=market, universes=["u"], tv=sym, currency="", tfs={"15m"},
                      feed=feed, optional=optional)


def test_auto_mode_asks_yahoo_only_for_what_tradingview_could_not_give():
    net = Net({"NSE:BBB": "bad", "MCX:GOLD1!": "bad"})
    asked = []
    yahoo = lambda syms, itv: (asked.append(list(syms)) or {s: flat_rows_frame() for s in syms}, [])
    insts = [inst("AAA.NS", ["NSE:AAA"]), inst("BBB.NS", ["NSE:BBB"]), inst("CCC.NS", []),
             inst("MCX:GOLD1!", ["MCX:GOLD1!"], market="MCX")]
    cfg = load_config()
    f = data.Fetcher(cfg, insts, tv=make_feed(net, {i.symbol: i.feed for i in insts}), yahoo=yahoo)
    out, failed = f(["AAA.NS", "BBB.NS", "CCC.NS", "MCX:GOLD1!"], "15m")
    assert set(out) == {"AAA.NS", "BBB.NS", "CCC.NS"} and failed == ["MCX:GOLD1!"]
    assert asked == [["BBB.NS", "CCC.NS"]]                                         # MCX names never go to Yahoo
    assert f.served == {"tradingview": 1, "yahoo": 2} and f.invalid == {"MCX:GOLD1!"}


def flat_rows_frame():
    return tv.to_frame({r["v"][0]: tuple(r["v"][1:6]) for r in flat_rows()})


def test_tradingview_only_and_yahoo_only_modes_do_not_mix():
    insts = [inst("AAA.NS", ["NSE:AAA"]), inst("BBB.NS", ["NSE:BBB"])]
    calls = []
    yahoo = lambda syms, itv: (calls.append(syms) or {s: flat_rows_frame() for s in syms}, [])
    cfg = dict(load_config(), data_source="tradingview")
    f = data.Fetcher(cfg, insts, tv=make_feed(Net({"NSE:BBB": "silent"}), {"AAA.NS": ["NSE:AAA"], "BBB.NS": ["NSE:BBB"]}),
                     yahoo=yahoo)
    out, failed = f(["AAA.NS", "BBB.NS"], "15m")
    assert set(out) == {"AAA.NS"} and failed == ["BBB.NS"] and calls == []         # no backup in this mode
    cfg = dict(load_config(), data_source="yahoo")
    f = data.Fetcher(cfg, insts, yahoo=yahoo)
    out, failed = f(["AAA.NS", "BBB.NS"], "15m")
    assert f.tv is None and set(out) == {"AAA.NS", "BBB.NS"} and calls == [["AAA.NS", "BBB.NS"]]


def test_an_outage_is_reported_once_and_the_backup_keeps_alerts_flowing():
    net = Net({f"NSE:S{i}": "silent" for i in range(4)})
    insts = [inst(f"S{i}.NS", [f"NSE:S{i}"]) for i in range(4)]
    yahoo = lambda syms, itv: ({s: flat_rows_frame() for s in syms}, [])
    f = data.Fetcher(load_config(), insts, tv=make_feed(net, {i.symbol: i.feed for i in insts}, batch_size=1), yahoo=yahoo)
    for _ in range(2):
        out, failed = f([i.symbol for i in insts], "15m")
        assert len(out) == 4 and failed == []
    assert len(f.events) == 1 and "not reachable" in f.events[0] and "Yahoo backup" in f.events[0]


def test_optional_and_unknown_symbols_never_count_against_data_coverage():
    cfg = load_config()
    insts = [inst("A.NS", []), inst("B.NS", []), inst("OPT.NS", [], optional=True), inst("GONE.NS", [])]

    class F:
        invalid = {"GONE.NS"}
        served = {"tradingview": 2}

        def __call__(self, syms, itv):
            return {s: flat_rows_frame() for s in ("A.NS", "B.NS")}, [s for s in syms if s not in ("A.NS", "B.NS")]

    coll = engine.collect(cfg, insts, {"NSE": ["15m"]}, pd.Timestamp("2026-10-30 12:00", tz="UTC"), F())
    st = coll.stats[("NSE", "15m")]
    assert st["requested"] == 2 and st["failed"] == [] and coll.invalid == {"GONE.NS"} and coll.served["tradingview"] == 2


# ===================================================================== waiting for the delayed feed
def test_settle_time_includes_the_feed_delay_of_the_chosen_source():
    cfg = load_config()
    assert settle_for(cfg, "NSE", "15m") == 16 and settle_for(cfg, "NSE", "1d") == 30 and settle_for(cfg, "CRYPTO", "15m") == 1
    assert settle_for(dict(cfg, data_source="yahoo"), "NSE", "15m") == 1
    assert settle_for(dict(cfg, data_source="tradingview"), "US", "4h") == 17


def test_a_candle_is_not_judged_until_the_delayed_feed_has_finished_it():
    raw = intraday_raw("NSE", 15, FRI, 30, lambda n: vshape(n))
    ends = bars.intraday_bars(raw, NSE, 15)["end"]
    last = ends.iloc[-1]
    cfg = load_config()
    early, _ = engine.evaluate(raw, "NSE", "15m", cfg, last + pd.Timedelta(minutes=5))     # TradingView: 15 min behind
    late, _ = engine.evaluate(raw, "NSE", "15m", cfg, last + pd.Timedelta(minutes=17))
    yahoo, _ = engine.evaluate(raw, "NSE", "15m", dict(cfg, data_source="yahoo"), last + pd.Timedelta(minutes=5))
    assert early["last_end"] == ends.iloc[-2] and late["last_end"] == last and yahoo["last_end"] == last


# ===================================================================== TradingView candles -> our bars
def as_tradingview(df):
    """Yahoo-shaped test candles -> what TradingView would deliver (UTC index of candle open times)."""
    out = df.rename(columns=str.lower).drop(columns=["adj close"])
    out.index = out.index.tz_convert("UTC")
    return out


def test_tradingview_candles_make_the_same_bars_as_the_rest_of_the_scanner():
    for market, minutes in (("NSE", 15), ("US", 15), ("MCX", 15), ("CRYPTO", 15)):
        m = MARKETS[market]
        raw = intraday_raw(market, minutes, FRI, 20, lambda n: vshape(n))
        a, b = bars.frame_for("15m", raw, m), bars.frame_for("15m", as_tradingview(raw), m)
        assert a["end"].tolist() == b["end"].tolist() and a["close"].tolist() == b["close"].tolist(), market
    raw = intraday_raw("NSE", 60, FRI, 60, lambda n: vshape(n))
    h = bars.frame_for("4h", as_tradingview(raw), NSE)
    assert not h.empty and h["end"].dt.tz_convert("Asia/Kolkata").dt.strftime("%H:%M").isin(["13:15", "15:30"]).all()


def test_daily_candles_stamped_at_the_session_open_get_the_right_trading_day():
    for market in ("NSE", "US", "MCX", "CRYPTO"):
        m = MARKETS[market]
        days = [d for d in pd.bdate_range(end=pd.Timestamp(FRI), periods=120).date]
        idx = pd.DatetimeIndex([session_for(m, d)[0] for d in days])                # TradingView stamps the session open
        df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=idx)
        d = bars.daily_bars(df, m)
        assert [x.date() for x in d["_label"]] == days, market
        assert d["end"].iloc[-1] == session_for(m, days[-1])[1]


# ===================================================================== config, universes, setup check
def test_data_source_must_be_one_of_the_three_choices(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text("data_source: nasdaq\nuniverses: {a: {market: NSE, source: list, list: x}}\nlists: {x: []}\n")
    with pytest.raises(ConfigError, match="data_source"):
        load_config(bad)
    assert load_config()["data_source"] == "auto"


def test_universe_feed_names_follow_each_markets_naming_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "CACHE_DIR", tmp_path)
    rows = {"nse_nifty500": [{"symbol": "M&M.NS", "name": "M&M", "tv": "NSE:M_M"}],
            "wiki_nasdaq100": [{"symbol": "AAPL", "name": "Apple", "tv": "AAPL"}],
            "wiki_sp500": [{"symbol": "AAPL", "name": "Apple", "tv": "AAPL"}, {"symbol": "BRK-B", "name": "Berkshire", "tv": "BRK.B"},
                           {"symbol": "KO", "name": "Coca-Cola", "tv": "KO"}]}
    monkeypatch.setattr(universe, "_rows_for", lambda u, cfg: (rows[u["source"]], "test"))
    cfg = load_config()
    insts, _ = universe.build(cfg, only={"nifty500", "nasdaq100", "sp500"})
    by = {i.symbol: i for i in insts}
    assert by["M&M.NS"].feed == ["NSE:M_M"]
    assert by["AAPL"].feed == ["NASDAQ:AAPL", "NYSE:AAPL", "AMEX:AAPL"] and by["AAPL"].universes == ["nasdaq100", "sp500"]
    assert by["BRK-B"].feed == ["NYSE:BRK.B", "NASDAQ:BRK.B", "AMEX:BRK.B"] and by["KO"].feed[0] == "NYSE:KO"
    (tmp_path / "tv_symbols.json").write_text(json.dumps({"winners": {"KO": "NYSE:KO"}}))     # link uses the known exchange
    insts, _ = universe.build(cfg, only={"sp500"})
    assert {i.symbol: i for i in insts}["KO"].tv == "NYSE:KO" and {i.symbol: i for i in insts}["BRK-B"].tv == "BRK.B"


def test_crypto_mcx_and_index_feed_names(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "CACHE_DIR", tmp_path)
    insts, notes = universe.build(load_config(), only={"crypto", "commodities", "indian_indices"})
    by = {i.symbol: i for i in insts}
    assert by["BTC-USD"].feed[:2] == ["BINANCE:BTCUSDT", "COINBASE:BTCUSD"]
    assert by["SUI20947-USD"].feed[0] == "BINANCE:SUIUSDT"                     # 'base' replaces Yahoo's numbered id
    assert by["MCX:GOLD1!"].market == "MCX" and by["MCX:GOLD1!"].feed == ["MCX:GOLD1!"] and by["MCX:GOLD1!"].display == "Gold"
    assert by["^NSEI"].feed == ["NSE:NIFTY"] and by["NSE:CNX100"].optional and not by["^NSEI"].optional
    assert len([i for i in insts if i.market == "MCX"]) == 19 and all(not v.startswith("FAILED") for v in notes.values())


def _doctor_net():
    """TradingView with believable candles for the benchmark symbols."""
    def rows_fn(sym, interval):
        market = {"NSE:NIFTY": "NSE", "AMEX:SPY": "US", "BINANCE:BTCUSDT": "CRYPTO", "MCX:GOLD1!": "MCX"}.get(sym, "NSE")
        m = MARKETS[market]
        if interval == "1D":
            days = [d for d in pd.bdate_range(end=pd.Timestamp(FRI), periods=300).date]
            idx = pd.DatetimeIndex([session_for(m, d)[0] for d in days])
            n = len(idx)
        else:
            minutes = 15 if interval == "15" else 60
            df = as_tradingview(intraday_raw(market, minutes, FRI, 40, lambda k: vshape(k)))
            return [{"i": k, "v": [int(t.timestamp()), r.open, r.high, r.low, r.close, r.volume]}
                    for k, (t, r) in enumerate(df.iterrows())]
        return [{"i": k, "v": [int(t.timestamp()), 100 + k * 0.1, 101 + k * 0.1, 99 + k * 0.1, 100 + k * 0.1, 10]}
                for k, t in enumerate(idx)]
    return Net(modes={"NSE:NIFTY": "delayed_streaming_900", "AMEX:SPY": "delayed_streaming_900",
                      "BINANCE:BTCUSDT": "streaming", "MCX:GOLD1!": "delayed_streaming_900"}, rows_fn=rows_fn)


def test_setup_check_reports_candles_delays_and_a_working_sample(capsys):
    cfg = load_config()
    insts = [inst("^NSEI", ["NSE:NIFTY"])] + [inst(f"A{k}.NS", [f"NSE:A{k}"]) for k in range(4)] + [inst("NOPE.NS", ["NSE:NOPE"])]
    net = _doctor_net()
    net.routes["NSE:NOPE"] = "bad"
    now = pd.Timestamp("2026-10-30 16:10", tz="Asia/Kolkata").tz_convert("UTC")
    fetcher = data.Fetcher(dict(cfg, data_source="tradingview"), insts,
                           tv=make_feed(net, {i.symbol: i.feed for i in insts}, batch_size=5))
    lines = []
    ok = cli._check_feed("TradingView", fetcher, cfg, insts, now, lambda lvl, msg: lines.append((lvl, msg)),
                         fail="WARN", backup=True)
    text = "\n".join(f"[{lvl}] {msg}" for lvl, msg in lines)
    assert ok is True
    assert "[PASS] TradingView NSE 15m:" in text and "[PASS] TradingView NSE 1d:" in text
    assert "NSE: TradingView data mode 'delayed_streaming_900' = 15 min delay (matches config)" in text
    assert "[PASS] TradingView sample test: data for 5 of 6" in text and "does not recognise: NOPE.NS" in text
    assert "newest day candle is labelled 30 Oct" in text


def test_setup_check_warns_when_the_real_delay_differs_from_the_setting():
    cfg = load_config()
    insts = [inst("^NSEI", ["NSE:NIFTY"])]
    net = _doctor_net()
    net.modes["NSE:NIFTY"] = "streaming"
    fetcher = data.Fetcher(dict(cfg, data_source="tradingview"), insts, tv=make_feed(net, {"^NSEI": ["NSE:NIFTY"]}))
    lines = []
    cli._check_delays(fetcher, cfg, ["NSE"], lambda lvl, msg: lines.append((lvl, msg)))
    assert lines[0][0] == "WARN" and "feed_delay_minutes -> tradingview -> NSE: 0" in lines[0][1]


def test_setup_check_fails_only_when_both_sources_are_down(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config()
    insts = [inst("^NSEI", ["NSE:NIFTY"])]
    monkeypatch.setattr(cli, "build", lambda c, only=None: (insts, {}))
    dead_tv = lambda: data.Fetcher(dict(cfg, data_source="tradingview"), insts,
                                   tv=make_feed(Net({"NSE:NIFTY": "silent"}), {"^NSEI": ["NSE:NIFTY"]}, stop_after_failed_batches=1))
    live_tv = lambda: data.Fetcher(dict(cfg, data_source="tradingview"), insts,
                                   tv=make_feed(_doctor_net(), {"^NSEI": ["NSE:NIFTY"]}))
    dead_y = lambda: data.Fetcher(dict(cfg, data_source="yahoo"), insts, yahoo=lambda s, i: ({}, list(s)))
    live_y = lambda: data.Fetcher(dict(cfg, data_source="yahoo"), insts,
                                  yahoo=lambda s, i: ({x: flat_rows_frame() for x in s}, []))
    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)

    def run(tv_f, y_f):
        monkeypatch.setattr(cli, "make_fetcher", lambda c, i=None: tv_f() if c["data_source"] == "tradingview" else y_f())
        capsys.readouterr()
        cli.main(["doctor", "--now", pd.Timestamp("2026-10-30 16:10", tz="Asia/Kolkata").tz_convert("UTC").isoformat()])
        return capsys.readouterr().out

    both_down = run(dead_tv, dead_y)
    assert "[FAIL] Neither TradingView nor Yahoo returned usable data" in both_down
    tv_down = run(dead_tv, live_y)
    assert "[WARN] TradingView is not working from here - the scanner will use the Yahoo backup" in tv_down
    assert "Neither TradingView nor Yahoo" not in tv_down
    assert "Neither" not in run(live_tv, dead_y)
