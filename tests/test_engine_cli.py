import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from scanner import alerts, bars, cli, engine, report, telegram
from scanner.config import load_config
from scanner.markets import MARKETS
from scanner.universe import Instrument
from synth import daily_raw, first_cross, intraday_raw, uptrend, vshape

NSE = MARKETS["NSE"]
FRI = date(2026, 10, 30)


def inst(symbol, name, market="NSE", unis=("nifty500",), tfs=("15m",), cur="₹", tv=""):
    return Instrument(symbol=symbol, name=name, market=market, universes=list(unis), tv=tv or f"NSE:{name}",
                      currency=cur, tfs=set(tfs))


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["timeframes"] = ["15m"]
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("SCANNER_STATE", str(state_file))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(cli, "PAUSE", 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    box = {"messages": [], "fail": False, "docs": []}

    def fake_post(url, json=None, data=None, files=None, timeout=None):
        if box["fail"]:
            return FakeResp(400, {"ok": False, "description": "Bad Request: chat not found"})
        if url.endswith("sendMessage"):
            box["messages"].append(json["text"])
        elif url.endswith("sendDocument"):
            box["docs"].append(data["caption"])
        return FakeResp(200, {"ok": True, "result": {"username": "testbot"}})

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)

    # data: AAA has a bullish cross at bar k, BBB trends up, ^NSEI is the readiness benchmark
    n_probe = intraday_raw("NSE", 15, FRI, 30, lambda n: np.zeros(n)).shape[0]
    close_aaa = vshape(n_probe)
    k = first_cross(close_aaa)
    data = {
        "AAA.NS": intraday_raw("NSE", 15, FRI, 30, lambda n: close_aaa),
        "BBB.NS": intraday_raw("NSE", 15, FRI, 30, lambda n: uptrend(n)),
        "^NSEI": intraday_raw("NSE", 15, FRI, 30, lambda n: uptrend(n)),
    }
    calls = []

    def fetcher(symbols, interval):
        calls.append((tuple(symbols), interval))
        out = {s: data[s] for s in symbols if s in data and interval == "15m"}
        return out, [s for s in symbols if s not in out]

    monkeypatch.setattr(cli, "make_fetcher", lambda c: fetcher)
    insts = [inst("^NSEI", "Nifty 50", unis=("indian_indices",), cur=""), inst("AAA.NS", "AAA"), inst("BBB.NS", "BBB")]
    monkeypatch.setattr(cli, "build", lambda c, only=None: (insts, {"nifty500": "3 symbols (test)"}))
    ends = bars.intraday_bars(data["AAA.NS"], NSE, 15)["end"]
    return {"cfg": cfg, "box": box, "state": state_file, "k": k, "ends": ends, "calls": calls, "data": data,
            "insts": insts, "fetcher": fetcher}


def run(env, when):
    return cli.main(["scan", "--now", when.isoformat()])


def at(env, offset):
    return env["ends"].iloc[env["k"] + offset] + pd.Timedelta(minutes=2)


def test_first_run_is_silent_baseline_then_one_alert_then_no_duplicates(env):
    msgs = env["box"]["messages"]
    assert run(env, at(env, -6)) == 0
    assert len(msgs) == 1 and "scanner is live" in msgs[0]            # baseline only, no alert flood
    assert run(env, at(env, 0)) == 0
    assert len(msgs) == 2
    alert = msgs[1]
    assert "BULLISH CROSS · 15M" in alert and "AAA" in alert and "BBB" not in alert
    assert "EMA 10 (green) crossed above EMA 20 (red)" in alert
    assert run(env, at(env, 1)) == 0                                    # cross still inside the scan window ...
    assert len(msgs) == 2                                               # ... but already alerted
    saved = json.loads(env["state"].read_text())
    assert any(key.startswith("AAA.NS|15m|") for key in saved["sent"]["15m"])


def test_same_moment_twice_does_nothing_and_fetches_nothing(env):
    run(env, at(env, -6))
    run(env, at(env, 0))
    n_calls = len(env["calls"])
    run(env, at(env, 0))
    assert len(env["box"]["messages"]) == 2 and len(env["calls"]) == n_calls     # checkpoint skip


def test_forming_candle_is_never_used(env):
    run(env, at(env, -6))
    # one minute BEFORE the cross candle closes: no alert even though data already contains that candle
    run(env, env["ends"].iloc[env["k"]] - pd.Timedelta(minutes=1))
    assert len(env["box"]["messages"]) == 1


def test_failed_telegram_does_not_lose_the_alert(env):
    run(env, at(env, -6))
    env["box"]["fail"] = True
    assert run(env, at(env, 0)) == 1                                   # red run so the owner notices
    env["box"]["fail"] = False
    assert run(env, at(env, 0)) == 0                                   # same moment: retried, not skipped
    assert any("AAA" in m and "BULLISH" in m for m in env["box"]["messages"])
    n = len(env["box"]["messages"])
    run(env, at(env, 0))
    assert len(env["box"]["messages"]) == n


def test_low_yahoo_coverage_pauses_checkpoint_and_warns(env, monkeypatch):
    run(env, at(env, -6))
    full = env["fetcher"]

    def thin(symbols, interval):
        out, failed = full(symbols, interval)
        out = {s: v for s, v in out.items() if s in ("^NSEI",)}
        return out, [s for s in symbols if s not in out]

    monkeypatch.setattr(cli, "make_fetcher", lambda c: thin)
    run(env, at(env, 0))
    assert any("data problem" in m.lower() for m in env["box"]["messages"][1:])
    saved = json.loads(env["state"].read_text())
    assert saved["checkpoints"]["NSE|15m"] != at(env, 0).isoformat()   # not advanced -> retried later


def test_not_ready_benchmark_skips_the_whole_market(env):
    run(env, at(env, -6))
    env["calls"].clear()
    monday_open = pd.Timestamp("2026-11-02 09:32", tz="Asia/Kolkata").tz_convert("UTC")
    run(env, monday_open)                                               # data ends Friday -> not ready yet
    assert all(c[0] == ("^NSEI",) for c in env["calls"])               # only the 1-symbol probe was made
    assert len(env["box"]["messages"]) == 1


def test_missing_telegram_secrets_fail_loudly(env, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert run(env, at(env, 0)) == 2


def test_dry_run_prints_and_saves_nothing(env, capsys):
    cli.main(["scan", "--dry-run", "--now", at(env, 0).isoformat()])
    assert "(dry run)" in capsys.readouterr().out
    assert not env["state"].exists() and env["box"]["messages"] == []


def test_test_telegram_command(env):
    assert cli.main(["test-telegram"]) == 0
    assert "Telegram connected" in env["box"]["messages"][-1]
    env["box"]["fail"] = True
    assert cli.main(["test-telegram"]) == 1


# --------------------------------------------------------------------- all timeframes, all markets
def _multi_data(market, end):
    m = MARKETS[market]
    if market == "CRYPTO":
        return {"15m": intraday_raw(market, 15, end, 20, lambda n: vshape(n)),
                "1h": intraday_raw(market, 60, end, 60, lambda n: vshape(n)),
                "1d": daily_raw(market, end, 900, lambda n: vshape(n))}
    return {"15m": intraday_raw(market, 15, end, 30, lambda n: vshape(n)),
            "1h": intraday_raw(market, 60, end, 90, lambda n: vshape(n)),
            "1d": daily_raw(market, end, 800, lambda n: vshape(n))}


@pytest.mark.parametrize("market,sym,now", [
    ("NSE", "XYZ.NS", "2026-10-30 16:10"),
    ("US", "XYZ", "2026-10-30 21:00"),
    ("CRYPTO", "XYZ-USD", "2026-10-31 19:40"),
    ("FUT", "XY=F", "2026-10-30 23:00"),
])
def test_every_market_and_timeframe_evaluates(market, sym, now):
    cfg = load_config()
    tz = {"NSE": "Asia/Kolkata", "US": "Asia/Kolkata", "CRYPTO": "UTC", "FUT": "Asia/Kolkata"}[market]
    t = pd.Timestamp(now, tz=tz).tz_convert("UTC")
    end = {"NSE": FRI, "US": FRI, "CRYPTO": date(2026, 10, 31), "FUT": FRI}[market]
    raw = _multi_data(market, end)
    i = inst(sym, "XYZ", market=market, tfs=("15m", "1h", "4h", "1d", "1w"))
    fetch = lambda syms, itv: ({s: raw[itv] for s in syms}, [])
    coll = engine.collect(cfg, [i], {market: ["15m", "1h", "4h", "1d", "1w"]}, t, fetch)
    assert len(coll.results) == 5
    for r in coll.results:
        assert r.summary is not None, (market, r.tf, r.reason)
        assert r.summary["last_end"] <= t
        assert r.summary["bars"] >= 60


def test_report_and_digest_from_collected_results():
    cfg = load_config()
    cfg["timeframes"] = ["15m", "1d"]
    now = pd.Timestamp("2026-10-30 16:10", tz="Asia/Kolkata").tz_convert("UTC")
    raw = _multi_data("NSE", FRI)
    insts = [inst("XYZ.NS", "XYZ", tfs=("15m", "1d")), inst("MISSING.NS", "MISSING", tfs=("15m", "1d"))]

    def fetch(syms, itv):
        out = {s: raw[itv] for s in syms if s == "XYZ.NS"}
        return out, [s for s in syms if s not in out]

    coll = engine.collect(cfg, insts, {"NSE": ["15m", "1d"]}, now, fetch)
    md, df = report.build_report(coll, insts, cfg, now, {"nifty500": "2 symbols (test)"})
    assert "# EMA 10/20 Crossover Report" in md and "Market breadth" in md and "Data coverage" in md
    assert "MISSING" in md                                              # listed under 'No data from Yahoo'
    assert len(df) == 4 and set(df["state"]) <= {"bull", "bear", "flat", "n/a"}
    assert report.summary_text(coll, cfg, now, {}).startswith("📊")


def test_report_command_writes_files_and_sends_summary_and_documents(env, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["report", "--now", at(env, 0).isoformat()]) == 0
    out = tmp_path / "reports"
    assert (out / "latest.md").exists() and (out / "latest.csv").exists()
    assert len(list((out / "archive").glob("*.md"))) == 1
    md = (out / "latest.md").read_text(encoding="utf-8")
    assert "AAA" in md and "Bullish" in md                              # the fresh bullish cross is listed
    assert len(pd.read_csv(out / "latest.csv")) == 3                    # 3 symbols x 1 timeframe
    assert env["box"]["messages"][-1].startswith("📊") and len(env["box"]["docs"]) == 2
    assert cli.main(["report", "--no-telegram", "--now", at(env, 0).isoformat()]) == 0
