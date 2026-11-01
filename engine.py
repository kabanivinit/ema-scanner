"""Core loop: decide what is due, fetch data, evaluate crossovers."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from . import bars, signals
from .markets import BASE_INTERVAL, MARKETS, expected_latest_end, settle_for

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
    invalid: set = field(default_factory=set)   # symbols the data feed does not recognise
    served: dict = field(default_factory=dict)  # {"tradingview": n, "yahoo": n} candle sets delivered


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
    closed = bars.closed_only(frame, now, settle_for(cfg, market_key, tf))
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
            # symbols the feed says do not exist, and 'optional' ones, never count against data coverage
            bad = set(getattr(fetcher, "invalid", None) or ())
            out.invalid |= bad
            skip = bad | {i.symbol for i in insts if getattr(i, "optional", False)}
            required = [s for s in syms if s not in skip]
            out.stats[(market, itv)] = {"requested": len(required), "failed": [s for s in failed if s not in skip]}
            log.info("%s %s: %d/%d symbols downloaded", market, itv, len(syms) - len(failed), len(syms))
        for tf in tfs:
            itv = BASE_INTERVAL[tf]
            for i in insts:
                if tf not in i.tfs:
                    continue
                df = raw.get(itv, {}).get(i.symbol)
                if df is None:
                    out.results.append(Result(i, tf, None, "no data from the data feed"))
                    continue
                try:
                    summary, why = evaluate(df, market, tf, cfg, now)
                except Exception as e:                          # noqa: BLE001
                    summary, why = None, f"error: {str(e)[:80]}"
                out.results.append(Result(i, tf, summary, why))
    out.served = dict(getattr(fetcher, "served", None) or {})
    return out


def probe_ready(cfg, market, due, now, fetcher) -> dict:
    """Is the newest expected bar actually available? (Handles holidays and feed delays with 1 request.)"""
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
        closed = bars.closed_only(bars.frame_for(tf, df, m), now, settle_for(cfg, market, tf))
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
            exp = expected_latest_end(m, tf, now, settle_for(cfg, market, tf))
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
            if st["requested"] == 0 or got / st["requested"] >= cfg["alerts"]["min_data_coverage"]:
                checkpoints[f"{market}|{tf}"] = expected[market][tf].isoformat()
            else:
                health.append(f"{market} {tf}: the data feed returned candles for only {got} of {st['requested']} symbols")
    return ScanOutcome(signals_from(coll.results, cfg, now), coll, checkpoints, health, skipped, wanted)
