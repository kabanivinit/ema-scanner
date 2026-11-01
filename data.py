"""Where candles come from: TradingView first, Yahoo Finance as backup (config: data_source).

The engine only ever calls   fetcher(symbols, interval) -> ({symbol: DataFrame}, [symbols without data])
so the two sources are interchangeable.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

from .tv import TradingViewFeed
from .universe import CACHE_DIR

log = logging.getLogger("scanner")


def _split(raw, batch):
    """Cut a multi-ticker download into {symbol: DataFrame}; copes with both column layouts."""
    out = {}
    if raw is None or len(raw) == 0:
        return out
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        l0, l1 = set(cols.get_level_values(0)), set(cols.get_level_values(1))
        for t in batch:
            if t in l0:
                out[t] = raw[t]
            elif t in l1:
                out[t] = raw.xs(t, axis=1, level=1)
    elif len(batch) == 1:
        out[batch[0]] = raw
    return {t: d for t, d in out.items() if d is not None and len(d.dropna(how="all")) > 0}


class _Catch(logging.Handler):
    """Collects yfinance's own log lines so we can notice 'Too Many Requests'."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:                                       # noqa: BLE001
            pass


def _is_throttle(text: str) -> bool:
    t = text.lower()
    return "too many" in t or "rate limit" in t or "ratelimit" in t


def _download(yf, batch, interval, period, ycfg, sleep):
    """One batch. Returns {symbol: df}, or None if Yahoo keeps throttling us."""
    for _ in range(2):
        catcher = _Catch()
        ylog = logging.getLogger("yfinance")
        ylog.addHandler(catcher)
        raw, problem = None, ""
        try:
            raw = yf.download(
                tickers=batch, period=period, interval=interval, group_by="ticker",
                auto_adjust=False, prepost=False, progress=False, timeout=30,
                threads=int(ycfg["threads"]) if len(batch) > 1 else False,
            )
        except Exception as e:                                  # noqa: BLE001
            problem = str(e)
            log.warning("Yahoo error (%s): %s", interval, problem[:200])
        finally:
            ylog.removeHandler(catcher)
        if _is_throttle(" ".join(catcher.lines) + " " + problem):
            log.warning("Yahoo is rate-limiting us - pausing 45 seconds")
            sleep(45)
            continue
        return _split(raw, batch)
    return None


def fetch_yahoo(symbols, interval, ycfg, sleep=None):
    """Returns ({symbol: DataFrame}, [symbols that failed])."""
    import yfinance as yf

    sleep = sleep or (lambda s: time.sleep(s))
    period = ycfg["periods"][interval]
    size = max(1, int(ycfg["batch_size"]))
    deadline = time.time() + float(ycfg["max_seconds"])
    todo = list(dict.fromkeys(symbols))
    out, stop = {}, False
    for attempt in range(int(ycfg["retries"]) + 1):
        if not todo or stop:
            break
        remaining = []
        for i in range(0, len(todo), size):
            batch = todo[i:i + size]
            if stop or time.time() > deadline:
                remaining.extend(batch)
                continue
            got = _download(yf, batch, interval, period, ycfg, sleep)
            if got is None:
                stop = True
                remaining.extend(batch)
                continue
            for t in batch:
                if t in got:
                    out[t] = got[t]
                else:
                    remaining.append(t)
            sleep(float(ycfg["pause_seconds"]))
        todo = remaining
        if todo and attempt < int(ycfg["retries"]) and not stop:
            sleep(5 * (attempt + 1))
    return out, todo


class Fetcher:
    """data_source: tradingview = TradingView only | yahoo = Yahoo only | auto = TradingView, then Yahoo for gaps."""

    def __init__(self, cfg, instruments=(), tv=None, yahoo=None, clock=None):
        self.cfg = cfg
        self.mode = cfg["data_source"]
        self.clock = clock or time.monotonic
        self.t0 = self.clock()
        self.invalid = set()               # symbols TradingView does not know (result of the latest call)
        self.invalid_all = set()           # ... over the whole run
        self.served = {"tradingview": 0, "yahoo": 0}
        self.events = []                   # plain-English notes for the health alert
        self._yahoo = yahoo
        self._noted = set()
        self.tv = None
        if self.mode != "yahoo":
            tvmap = {i.symbol: list(i.feed) for i in instruments if getattr(i, "feed", None)}
            self.tv = tv or TradingViewFeed(cfg["tradingview"], tvmap, cache_path=CACHE_DIR / "tv_symbols.json")

    def _note(self, key, text):
        if key not in self._noted:
            self._noted.add(key)
            self.events.append(text)
            log.warning(text)

    def _yahoo_fetch(self, symbols, interval):
        if self._yahoo:
            return self._yahoo(symbols, interval)
        y = dict(self.cfg["yahoo"])
        left = float(self.cfg["run_budget_seconds"]) - (self.clock() - self.t0)
        y["max_seconds"] = max(30.0, min(float(y["max_seconds"]), left))
        return fetch_yahoo(symbols, interval, y)

    def __call__(self, symbols, interval):
        symbols = list(dict.fromkeys(symbols))
        out, failed = {}, symbols
        self.invalid = set()
        if self.tv is not None:
            got, failed = self.tv.fetch(symbols, interval)
            out.update(got)
            self.served["tradingview"] += len(got)
            if self.tv.down:
                tail = "using the Yahoo backup" if self.mode == "auto" else "no data this run"
                self._note("down", f"TradingView feed not reachable ({self.tv.down}) - {tail}.")
        if self.mode in ("auto", "yahoo") and failed:
            ysyms = [s for s in failed if ":" not in s]          # TradingView-style names cannot be asked of Yahoo
            if ysyms:
                got, _ = self._yahoo_fetch(ysyms, interval)
                out.update(got)
                self.served["yahoo"] += len(got)
                failed = [s for s in failed if s not in out]
        if self.tv is not None:
            self.invalid = self.tv.invalid & set(failed)
            self.invalid_all |= self.invalid
        return out, failed


def make_fetcher(cfg, instruments=None):
    return Fetcher(cfg, instruments or ())
