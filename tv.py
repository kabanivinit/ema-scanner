"""TradingView candles through its (unofficial) chart feed.

This is the same anonymous connection a visitor's web browser opens on tradingview.com.
No account, password or cookie is ever used.

* It is NOT an official API. TradingView can change or block it at any time - the Yahoo backup then takes over.
* Free anonymous data is DELAYED (about 15 minutes for NSE / US / MCX; crypto is live). The scanner waits for
  that delay (config: feed_delay_minutes) before it judges a candle.
"""
from __future__ import annotations

import json
import logging
import random
import re
import string
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger("scanner")

WS_URL = "wss://data.tradingview.com/socket.io/websocket"
ORIGINS = ("https://www.tradingview.com", "https://data.tradingview.com")
TOKEN = "unauthorized_user_token"                     # TradingView's name for "not logged in"
INTERVAL = {"15m": "15", "1h": "60", "1d": "1D"}      # our base intervals -> TradingView resolutions
COLS = ["open", "high", "low", "close", "volume"]
BAD_DAYS = 2                                          # a symbol TradingView does not know is left alone this long

# The symbols the scanner itself needs for its "is the newest candle available yet?" check,
# written the way TradingView names them (first = preferred).
BENCH_TV = {
    "^NSEI": ["NSE:NIFTY"],
    "SPY": ["AMEX:SPY", "NYSE:SPY"],
    "BTC-USD": ["BINANCE:BTCUSDT", "COINBASE:BTCUSD"],
    "GC=F": ["COMEX:GC1!"],
    "MCX:GOLD1!": ["MCX:GOLD1!"],
}


class TVError(Exception):
    """Raised with a message that is safe to show (never contains secrets)."""


def _short(e, n=140) -> str:
    return (str(e) or type(e).__name__)[:n]


# ---------------------------------------------------------------- wire format
_SPLIT = re.compile(r"~m~\d+~m~")


def wrap(payload: str) -> str:
    return f"~m~{len(payload)}~m~{payload}"


def encode(func: str, params: list) -> str:
    return wrap(json.dumps({"m": func, "p": params}, separators=(",", ":")))


def decode(raw: str) -> list:
    """One websocket text frame -> [("hb", "~h~12") | ("msg", {...}), ...]. A frame may hold several messages."""
    out = []
    for chunk in _SPLIT.split(raw):
        if not chunk:
            continue
        if chunk.startswith("~h~"):
            out.append(("hb", chunk))
            continue
        try:
            msg = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(msg, dict):
            out.append(("msg", msg))
    return out


def merge_rows(store: dict, rows) -> None:
    """Adds TradingView candle rows [{"i": n, "v": [time, o, h, l, c, volume]}] to store {time: (o, h, l, c, v)}."""
    for r in rows or []:
        v = r.get("v") if isinstance(r, dict) else None
        if not isinstance(v, (list, tuple)) or len(v) < 5 or v[0] is None:
            continue
        try:
            t = int(v[0])
            o, h, lo, c = (float(x) for x in v[1:5])
        except (TypeError, ValueError):
            continue
        vol = 0.0
        if len(v) > 5 and v[5] is not None:
            try:
                vol = float(v[5])
            except (TypeError, ValueError):
                vol = 0.0
        store[t] = (o, h, lo, c, vol)


def to_frame(store: dict):
    """{time: (o, h, l, c, v)} -> DataFrame indexed by candle OPEN time (UTC), or None when empty."""
    if not store:
        return None
    ts = sorted(store)
    idx = pd.to_datetime(ts, unit="s", utc=True).as_unit("ns")      # same time unit on every pandas version
    return pd.DataFrame([store[t] for t in ts], columns=COLS, index=idx)


def _chart_rows(msg) -> list:
    """Candle rows inside a timescale_update / du message (the series id inside varies, so look at every value)."""
    p = msg.get("p") or []
    rows = []
    if len(p) > 1 and isinstance(p[1], dict):
        for val in p[1].values():
            if isinstance(val, dict) and isinstance(val.get("s"), list):
                rows.extend(val["s"])
    return rows


# ---------------------------------------------------------------- connection
def default_connect(timeout: float = 20.0):
    """Opens one anonymous connection (tries each allowed 'origin' header in turn)."""
    try:
        import websocket
    except ImportError as e:
        raise TVError("the 'websocket-client' package is not installed (pip install -r requirements.txt)") from e
    last = None
    for origin in ORIGINS:
        try:
            return websocket.create_connection(WS_URL, origin=origin, timeout=timeout)
        except Exception as e:                                  # noqa: BLE001
            last = e
    raise TVError(f"cannot open the TradingView feed ({_short(last)})")


def _send(ws, func, params) -> None:
    ws.send(encode(func, params))


def _is_timeout(e) -> bool:
    return isinstance(e, TimeoutError) or type(e).__name__ in ("WebSocketTimeoutException", "timeout")


def _close(ws) -> None:
    try:
        ws.close()
    except Exception:                                           # noqa: BLE001
        pass


def _session_id(prefix: str, taken) -> str:
    while True:
        sid = prefix + "".join(random.choices(string.ascii_lowercase, k=12))
        if sid not in taken:
            return sid


def _recv_frames(ws, deadline, clock):
    """Yields decoded messages until the deadline; raises TVError when the connection ends."""
    while True:
        left = deadline - clock()
        if left <= 0:
            return
        try:
            ws.settimeout(max(0.5, min(left, 10.0)))
        except Exception:                                       # noqa: BLE001
            pass
        try:
            raw = ws.recv()
        except Exception as e:                                  # noqa: BLE001
            if _is_timeout(e):
                continue
            raise TVError(f"connection ended ({_short(e)})") from e
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if not raw:
            raise TVError("connection closed by TradingView")
        for kind, msg in decode(raw):
            if kind == "hb":
                ws.send(wrap(msg))                              # keep-alive: echo the heartbeat back
            else:
                yield msg


class BatchResult:
    def __init__(self):
        self.frames, self.invalid, self.failed = {}, {}, {}
        self.error = ""


def run_batch(connect, symbols, interval: str, n_bars: int, timeout: float, clock=time.monotonic) -> BatchResult:
    """One connection, one chart session per symbol, all requested together."""
    res = BatchResult()
    if not symbols:
        return res
    ws = connect()
    sessions, stores, done, bad, broke = {}, {}, set(), {}, {}
    try:
        _send(ws, "set_auth_token", [TOKEN])
        _send(ws, "set_locale", ["en", "US"])
        for sym in dict.fromkeys(symbols):
            cs = _session_id("cs_", sessions)
            sessions[cs], stores[cs] = sym, {}
            _send(ws, "chart_create_session", [cs, ""])
            _send(ws, "switch_timezone", [cs, "exchange"])
            _send(ws, "resolve_symbol", [cs, "sds_sym_1",
                                         "=" + json.dumps({"symbol": sym, "adjustment": "splits", "session": "regular"})])
            _send(ws, "create_series", [cs, "sds_1", "s1", "sds_sym_1", interval, int(n_bars), ""])
        pending = set(sessions)
        try:
            for msg in _recv_frames(ws, clock() + timeout, clock):
                m, p = msg.get("m"), msg.get("p") or []
                if m == "protocol_error":
                    raise TVError(f"TradingView refused the request ({_short(p)})")
                cs = p[0] if p and isinstance(p[0], str) else None
                if cs not in sessions:
                    continue
                if m in ("timescale_update", "du"):
                    merge_rows(stores[cs], _chart_rows(msg))
                elif m == "series_completed":
                    done.add(cs)
                    pending.discard(cs)
                elif m == "symbol_error":
                    bad[cs] = _short(p[1:])
                    pending.discard(cs)
                elif m in ("series_error", "critical_error"):
                    broke[cs] = _short(p[1:])
                    pending.discard(cs)
                if not pending:
                    break
        except TVError as e:
            res.error = str(e)
    finally:
        _close(ws)
    for cs, sym in sessions.items():
        if cs in bad:
            res.invalid[sym] = bad[cs]
        elif cs in broke:
            res.failed[sym] = broke[cs]
        elif cs in done:
            df = to_frame(stores[cs])
            if df is None:
                res.failed[sym] = "no candles"
            else:
                res.frames[sym] = df
        else:
            res.failed[sym] = res.error or "no reply in time"
    return res


# ---------------------------------------------------------------- the feed
def _load_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:                                           # noqa: BLE001
        return {}


def load_winners(path) -> dict:
    """{our symbol: the TradingView symbol that worked last time} - used to make alert links exact."""
    if not path:
        return {}
    w = _load_json(path).get("winners")
    return dict(w) if isinstance(w, dict) else {}


def delay_minutes(update_mode):
    """'delayed_streaming_900' -> 15, 'streaming' -> 0, anything else -> None (unknown)."""
    if not update_mode:
        return None
    m = re.search(r"delayed_streaming_(\d+)", str(update_mode))
    if m:
        return round(int(m.group(1)) / 60)
    if str(update_mode) in ("streaming", "realtime", "real_time"):
        return 0
    return None


class TradingViewFeed:
    """fetch(symbols, interval) -> ({symbol: DataFrame}, [symbols without data]).

    `tvmap` = {our symbol: [TradingView symbols to try, best first]}. The one that works is remembered
    (cache_path), and symbols TradingView does not know are left alone for BAD_DAYS days.
    """

    def __init__(self, tcfg, tvmap, cache_path=None, connect=None, sleep=None, clock=None, today=None):
        self.tcfg = tcfg
        self.cands = {k: list(v) for k, v in {**BENCH_TV, **(tvmap or {})}.items() if v}
        self.connect = connect or default_connect
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.today = today or date.today
        self.cache_path = Path(cache_path) if cache_path else None
        data = _load_json(self.cache_path) if self.cache_path else {}
        self.winners = dict(data.get("winners") or {})
        self.bad = dict(data.get("bad") or {})
        self.down = ""                       # reason TradingView is unusable for the rest of this run
        self.invalid = set()                 # symbols TradingView says do not exist
        self.healthy = False                 # True once any candles arrived (so "unknown symbol" can be trusted)
        self.deadline = None
        self._streak = 0
        self._dirty = False

    # ---- helpers
    def _is_bad(self, s) -> bool:
        stamp = self.bad.get(s)
        if not stamp or s in BENCH_TV:
            return False
        try:
            return self.today() - date.fromisoformat(stamp) < timedelta(days=BAD_DAYS)
        except ValueError:
            return False

    def _pick(self, s, tried):
        w = self.winners.get(s)
        if w and w in self.cands[s] and w not in tried:
            return w
        for c in self.cands[s]:
            if c not in tried:
                return c
        return None

    def save(self) -> None:
        if not (self.cache_path and self._dirty):
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps({"winners": self.winners, "bad": self.bad}, indent=1, sort_keys=True),
                                       encoding="utf-8")
            self._dirty = False
        except OSError as e:
            log.warning("Could not save the TradingView symbol memory: %s", e)

    def _one_batch(self, tv_syms, interval):
        tc = self.tcfg
        try:
            return run_batch(self.connect, tv_syms, INTERVAL[interval], int(tc["bars"][interval]),
                             float(tc["timeout_seconds"]), self.clock)
        except Exception as e:                                  # noqa: BLE001
            res = BatchResult()
            res.error = _short(e)
            for s in tv_syms:
                res.failed[s] = res.error
            return res

    # ---- main entry
    def fetch(self, symbols, interval):
        symbols = list(dict.fromkeys(symbols))
        out = {}
        if self.down or interval not in INTERVAL:
            return out, symbols
        tc = self.tcfg
        todo = []
        for s in symbols:
            if s not in self.cands:
                continue                                         # no TradingView name: left for the backup source
            if self._is_bad(s):
                self.invalid.add(s)
                continue
            todo.append(s)
        tried = {s: set() for s in todo}
        misses = {s: 0 for s in todo}
        size = max(1, int(tc["batch_size"]))
        retries = int(tc["retries"])
        if self.deadline is None:
            self.deadline = self.clock() + float(tc["max_seconds"])
        rounds = max((len(self.cands[s]) for s in todo), default=0) + retries
        for _ in range(rounds):
            if not todo or self.down:
                break
            picks = {s: self._pick(s, tried[s]) for s in todo}
            for s in [s for s, c in picks.items() if c is None]:       # every candidate was rejected
                self._give_up_invalid(s)
                todo.remove(s)
            active = [s for s in todo if picks[s]]
            nxt = []
            for i in range(0, len(active), size):
                chunk = active[i:i + size]
                if self.clock() > self.deadline:
                    log.warning("TradingView time budget used up - %d symbols left for the backup", len(active) - i)
                    nxt.extend(active[i:])
                    break
                by_tv = {}
                for s in chunk:
                    by_tv.setdefault(picks[s], []).append(s)
                res = self._one_batch(list(by_tv), interval)
                trust = self.healthy or bool(res.frames)
                for tv_sym, df in res.frames.items():
                    for s in by_tv[tv_sym]:
                        out[s] = df
                        if self.winners.get(s) != tv_sym:
                            self.winners[s] = tv_sym
                            self._dirty = True
                        self.bad.pop(s, None)
                if res.frames:
                    self.healthy = True
                for tv_sym in res.invalid:
                    for s in by_tv.get(tv_sym, []):
                        if trust:
                            tried[s].add(tv_sym)
                            nxt.append(s)
                        else:
                            misses[s] += 1
                            if misses[s] <= retries:
                                nxt.append(s)
                for tv_sym in res.failed:
                    for s in by_tv.get(tv_sym, []):
                        misses[s] += 1
                        if misses[s] <= retries:
                            nxt.append(s)
                progress = len(res.frames) + (len(res.invalid) if trust else 0)
                if progress == 0:
                    self._streak += 1
                    if self._streak >= int(tc["stop_after_failed_batches"]):
                        self.down = res.error or "no answer from TradingView"
                        log.warning("TradingView looks unreachable (%s) - switching to the backup for this run", self.down)
                        break
                    self.sleep(5)
                else:
                    self._streak = 0
                if i + size < len(active):
                    self.sleep(float(tc["pause_seconds"]))
            todo = [s for s in dict.fromkeys(nxt) if s not in out]
        for s in todo:                                          # ran out of rounds with candidates all rejected
            if s not in out and self._pick(s, tried[s]) is None:
                self._give_up_invalid(s)
        self.save()
        return out, [s for s in symbols if s not in out]

    def _give_up_invalid(self, s) -> None:
        self.invalid.add(s)
        if s not in BENCH_TV and self.healthy:
            self.bad[s] = self.today().isoformat()
            self.winners.pop(s, None)
            self._dirty = True

    # ---- setup check only
    def quote_modes(self, tv_symbols, timeout: float = 15.0) -> dict:
        """{tv_symbol: {"update_mode": 'delayed_streaming_900', "lp_time": epoch}} - best effort, setup check only."""
        found = {}
        ws = self.connect()
        try:
            qs = _session_id("qs_", ())
            _send(ws, "set_auth_token", [TOKEN])
            _send(ws, "set_locale", ["en", "US"])
            _send(ws, "quote_create_session", [qs])
            _send(ws, "quote_set_fields", [qs, "update_mode", "lp", "lp_time", "short_name", "exchange"])
            for s in tv_symbols:
                _send(ws, "quote_add_symbols", [qs, s, {"flags": ["force_permission"]}])
            want = set(tv_symbols)
            try:
                for msg in _recv_frames(ws, self.clock() + timeout, self.clock):
                    if msg.get("m") != "qsd":
                        continue
                    p = msg.get("p") or []
                    if len(p) > 1 and isinstance(p[1], dict) and isinstance(p[1].get("v"), dict):
                        name, v = p[1].get("n"), p[1]["v"]
                        slot = found.setdefault(name, {})
                        for k in ("update_mode", "lp_time"):
                            if v.get(k) is not None:
                                slot[k] = v[k]
                        if want <= {n for n, d in found.items() if d.get("update_mode")}:
                            break
            except TVError:
                pass
        finally:
            _close(ws)
        return found
