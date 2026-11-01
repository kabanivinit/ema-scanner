"""Core loop: decide what is due, fetch data, evaluate crossovers."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from . import bars, signals
from .markets import BASE_INTERVAL, MARKETS, expected_latest_end

log = logging.getLogger("scanner")


@dataclass
class Result:
    inst: object
    tf: str
    summary: dict | None
    reason: str = ""


@dataclass
class Signal:
    inst: object
    tf: str
    direction: str                 # "bull" | "bear"
    bar_end: pd.Timestamp
    close: float
    ema_s: float
    ema_l: float

    @property
    def key(self) -> str:
        return f"{self.inst.symbol}|{self.tf}|{int(self.bar_end.timestamp())}|{self.direction}"


@dataclass
class Collected:
    results: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)   # (market, interval) -> {"requested": n, "failed": [symbols]}


@dataclass
class ScanOutcome:
    signals: list
    collected: Collected
    checkpoints: dict
    health: list
    skipped: list
    wanted: dict


def market_tfs(cfg, instruments, market) -> list:
    return [tf for tf in cfg["timeframes"]
            if any(tf in i.tfs for i in instruments if i.market == market)]


def evaluate(raw_df, market_key, tf, cfg, now):
    m = MARKETS[market_key]
    frame = bars.frame_for(tf, raw_df, m)
    if frame.empty:
        return None, "no usable candles"
    closed = bars.closed_only(frame, now, cfg["settle_minutes"][tf])
    if len(closed) < cfg["min_bars"]:
        return None, f"only {len(closed)} closed candles (need {cfg['min_bars']})"
    return signals.analyse(closed, cfg["ema"]["short"], cfg["ema"]["long"], cfg["scan_last_bars"][tf]), ""


def collect(cfg, instruments, wanted, now, fetcher) -> Collected:
    """wanted = {market: [timeframes]}. Downloads each needed interval once per market."""
    out = Collected()
    for market, tfs in wanted.items():
        insts = [i for i in instruments if i.market == market]
        raw = {}
        for itv in sorted({BASE_INTERVAL[tf] for tf in tfs}):
            syms = [i.symbol for i in insts
                    if any(BASE_INTERVAL[tf] == itv and tf in i.tfs for tf in tfs)]
            if not syms:
                continue
            data, failed = fetcher(syms, itv)
            raw[itv] = data
            out.stats[(market, itv)] = {"requested": len(syms), "failed": list(failed)}
            log.info("%s %s: %d/%d symbols downloaded", market, itv, len(syms) - len(failed), len(syms))
        for tf in tfs:
            itv = BASE_INTERVAL[tf]
            for i in insts:
                if tf not in i.tfs:
                    continue
                df = raw.get(itv, {}).get(i.symbol)
                if df is None:
                    out.results.append(Result(i, tf, None, "no data from Yahoo"))
                    continue
                try:
                    summary, why = evaluate(df, market, tf, cfg, now)
                except Exception as e:                          # noqa: BLE001
                    summary, why = None, f"error: {str(e)[:80]}"
                out.results.append(Result(i, tf, summary, why))
    return out


def probe_ready(cfg, market, due, now, fetcher) -> dict:
    """Is the newest expected bar actually available? (Handles holidays and Yahoo delays with 1 request.)"""
    m = MARKETS[market]
    ready, cache = {}, {}
    for tf, expected in due.items():
        itv = BASE_INTERVAL[tf]
        if itv not in cache:
            data, _ = fetcher([m.benchmark], itv)
            cache[itv] = data.get(m.benchmark)
        df = cache[itv]
        if df is None:
            ready[tf] = True                    # cannot probe -> go ahead, coverage check protects us
            continue
        closed = bars.closed_only(bars.frame_for(tf, df, m), now, cfg["settle_minutes"][tf])
        ready[tf] = (not closed.empty) and closed["end"].max() >= expected
    return ready


def signals_from(results, cfg, now) -> list:
    want = cfg["alerts"]["directions"]
    out = []
    for r in results:
        if not r.summary:
            continue
        max_age = pd.Timedelta(minutes=cfg["max_alert_age_minutes"][r.tf])
        for s in r.summary["recent"]:
            if (want == "bullish" and s["dir"] != "bull") or (want == "bearish" and s["dir"] != "bear"):
                continue
            if now - s["bar_end"] > max_age:
                continue
            out.append(Signal(r.inst, r.tf, s["dir"], s["bar_end"], s["close"], s["ema_s"], s["ema_l"]))
    return out


def scan_once(cfg, instruments, state, now, fetcher) -> ScanOutcome:
    wanted, expected, skipped, health = {}, {}, [], []
    for market in sorted({i.market for i in instruments}):
        m = MARKETS[market]
        due = {}
        for tf in market_tfs(cfg, instruments, market):
            exp = expected_latest_end(m, tf, now, cfg["settle_minutes"][tf])
            if exp is None or state["checkpoints"].get(f"{market}|{tf}") == exp.isoformat():
                continue
            due[tf] = exp
        if not due:
            continue
        ready = probe_ready(cfg, market, due, now, fetcher)
        go = [tf for tf in due if ready.get(tf)]
        skipped += [f"{market} {tf}" for tf in due if not ready.get(tf)]
        if go:
            wanted[market] = go
            expected[market] = {tf: due[tf] for tf in go}
    coll = collect(cfg, instruments, wanted, now, fetcher)
    checkpoints = {}
    for market, tfs in wanted.items():
        for tf in tfs:
            st = coll.stats.get((market, BASE_INTERVAL[tf]))
            if not st:
                continue
            got = st["requested"] - len(st["failed"])
            if got / max(1, st["requested"]) >= cfg["alerts"]["min_data_coverage"]:
                checkpoints[f"{market}|{tf}"] = expected[market][tf].isoformat()
            else:
                health.append(f"{market} {tf}: Yahoo returned data for only {got} of {st['requested']} symbols")
    return ScanOutcome(signals_from(coll.results, cfg, now), coll, checkpoints, health, skipped, wanted)
