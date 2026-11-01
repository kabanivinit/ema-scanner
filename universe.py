"""Builds the list of symbols to scan (Nifty 500, S&P 500, Nasdaq 100 download live; others come from config.yaml)."""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from .config import ROOT
from .seed_lists import nasdaq100_seed
from .tv import load_winners

log = logging.getLogger("scanner")
CACHE_DIR = ROOT / "universe_cache"
UA = {"User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/124.0 Safari/537.36"), "Accept": "*/*"}
NSE_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


@dataclass
class Instrument:
    symbol: str                       # our id: the Yahoo Finance symbol (TradingView name for MCX contracts)
    name: str
    market: str                       # NSE | US | CRYPTO | FUT | MCX
    universes: list = field(default_factory=list)
    tv: str = ""                      # TradingView symbol for the clickable link
    currency: str = ""
    tfs: set = field(default_factory=set)
    feed: list = field(default_factory=list)   # TradingView symbols to download from, best guess first
    optional: bool = False            # True = never counted against data coverage (symbol may not exist on the feed)

    @property
    def display(self) -> str:
        s = self.symbol
        if ":" in s or s.startswith("^") or s.endswith("=F"):
            return self.name
        if s.endswith(".NS"):
            return s[:-3]
        if s.endswith("-USD"):
            base = s[:-4]
            return self.name if any(c.isdigit() for c in base) else base
        return s


# ---------------------------------------------------------------- downloads
def _http_get(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def fetch_nifty500() -> pd.DataFrame:
    last = None
    for url in NSE_URLS:
        try:
            df = pd.read_csv(io.StringIO(_http_get(url)))
            cols = {str(c).strip().lower(): c for c in df.columns}
            sym = cols["symbol"]
            name = cols.get("company name", sym)
            return pd.DataFrame({"symbol": df[sym].astype(str).str.strip() + ".NS",
                                 "name": df[name].astype(str).str.strip()})
        except Exception as e:                                  # noqa: BLE001
            last = e
    raise RuntimeError(f"NSE download failed ({str(last)[:120]})")


def _find_table(html: str, wants):
    """First table having a column named like one of `wants`. Returns (table, columns, which)."""
    wants = [wants] if isinstance(wants, str) else list(wants)
    for t in pd.read_html(io.StringIO(html)):
        cols = {(" ".join(map(str, c)) if isinstance(c, tuple) else str(c)).strip().lower(): c for c in t.columns}
        for w in wants:
            if w in cols:
                return t, cols, w
    raise ValueError(f"no table with a '{wants[0]}' column")


def _wiki_table(url: str, want: str):
    t, cols, _ = _find_table(_http_get(url), want)
    return t, cols


def _us_symbols(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper().str.replace(".", "-", regex=False)


def fetch_sp500() -> pd.DataFrame:
    t, cols = _wiki_table(SP500_URL, "symbol")
    return pd.DataFrame({"symbol": _us_symbols(t[cols["symbol"]]),
                         "name": t[cols.get("security", cols["symbol"])].astype(str)})


NDX_SOURCES = [NDX_URL, "https://stockanalysis.com/list/nasdaq-100-stocks/"]


def fetch_nasdaq100() -> pd.DataFrame:
    """Tries each web page in turn (Wikipedia first); the first usable table wins."""
    last = None
    for url in NDX_SOURCES:
        try:
            t, cols, w = _find_table(_http_get(url), ["ticker", "symbol"])
            name = cols.get("company") or cols.get("company name") or cols.get("security") or cols[w]
            df = pd.DataFrame({"symbol": _us_symbols(t[cols[w]]), "name": t[name].astype(str)})
            df = df[df["symbol"].str.fullmatch(r"[A-Z][A-Z0-9-]{0,5}")]
            if len(df) >= 20:
                return df
            last = ValueError(f"only {len(df)} rows from {url}")
        except Exception as e:                                  # noqa: BLE001
            last = e
    raise RuntimeError(f"Nasdaq-100 pages did not give a usable table ({str(last)[:100]})")


# ---------------------------------------------------------------- cache
def _meta() -> dict:
    p = CACHE_DIR / "_meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        return {}


def _save_meta(meta: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "_meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True), encoding="utf-8")


def _load_list(name: str, fetch_fn, refresh_days: int, seed=None):
    """Returns (DataFrame[symbol, name], note). Uses a saved copy while it is fresh."""
    path = CACHE_DIR / f"{name}.csv"
    meta = _meta()
    stamp = meta.get(name)
    if path.exists() and not stamp:                       # a file the user uploaded by hand
        return pd.read_csv(path, dtype=str, keep_default_na=False), "your uploaded file"
    if stamp and path.exists():
        try:
            if (date.today() - date.fromisoformat(stamp)).days < refresh_days:
                return pd.read_csv(path, dtype=str, keep_default_na=False), f"saved copy from {stamp}"
        except ValueError:
            pass
    try:
        df = fetch_fn()
        df = df.dropna().drop_duplicates("symbol")
        if len(df) < 20:
            raise ValueError(f"only {len(df)} rows came back")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        meta[name] = date.today().isoformat()
        _save_meta(meta)
        return df, "downloaded fresh list"
    except Exception as e:                                      # noqa: BLE001
        if path.exists():
            return (pd.read_csv(path, dtype=str, keep_default_na=False),
                    f"download failed ({str(e)[:80]}) - using saved copy from {stamp or 'earlier'}")
        if seed is not None:
            return seed(), f"download failed ({str(e)[:70]}) - using the copy built into the scanner"
        raise RuntimeError(f"could not download the list and no saved copy exists ({str(e)[:120]})") from e


# ---------------------------------------------------------------- TradingView names
US_EXCHANGES = ("NASDAQ", "NYSE", "AMEX")
CRYPTO_VENUES = ("BINANCE:{b}USDT", "COINBASE:{b}USD", "KRAKEN:{b}USD", "BITSTAMP:{b}USD")


def _as_list(v) -> list:
    if not v:
        return []
    return [str(x).strip() for x in v] if isinstance(v, (list, tuple)) else [str(v).strip()]


def crypto_base(symbol: str, item: dict) -> str:
    return str(item.get("base") or (symbol[:-4] if symbol.endswith("-USD") else symbol)).strip().upper()


def tv_nse(symbol: str) -> str:
    return "NSE:" + re.sub(r"[&-]", "_", symbol[:-3] if symbol.endswith(".NS") else symbol)


def tv_us(symbol: str) -> str:
    return symbol.replace("-", ".")


def tv_link(tv: str) -> str:
    return "https://www.tradingview.com/chart/?symbol=" + quote(tv, safe="")


# ---------------------------------------------------------------- build
def _norm_nse(df: pd.DataFrame) -> pd.DataFrame:
    """Accepts our saved format or the raw NSE file (Company Name, Industry, Symbol, ...)."""
    df = df.rename(columns={c: str(c).strip().lower() for c in df.columns})
    if "company name" in df.columns and "name" not in df.columns:
        df = df.rename(columns={"company name": "name"})
    if "name" not in df.columns:
        df["name"] = df["symbol"]
    sym = df["symbol"].astype(str).str.strip()
    df["symbol"] = sym.where(sym.str.endswith(".NS"), sym + ".NS")
    return df[["symbol", "name"]]


def _rows_for(u: dict, cfg: dict):
    src = u.get("source")
    days = int(cfg["universe_refresh_days"])
    if src == "nse_nifty500":
        df, note = _load_list("nifty500", fetch_nifty500, days)
        df = _norm_nse(df)
        return [{"symbol": r.symbol, "name": r.name, "tv": tv_nse(r.symbol)} for r in df.itertuples()], note
    if src == "wiki_sp500":
        df, note = _load_list("sp500", fetch_sp500, days)
        return [{"symbol": r.symbol, "name": r.name, "tv": tv_us(r.symbol)} for r in df.itertuples()], note
    if src == "wiki_nasdaq100":
        df, note = _load_list("nasdaq100", fetch_nasdaq100, days, seed=nasdaq100_seed)
        return [{"symbol": r.symbol, "name": r.name, "tv": tv_us(r.symbol)} for r in df.itertuples()], note
    if src == "list":
        rows = []
        market = u.get("market")
        for item in cfg["lists"][u["list"]]:
            sym = str(item["symbol"]).strip()
            feed = _as_list(item.get("feed"))
            if not feed and market == "CRYPTO":
                feed = [t.format(b=crypto_base(sym, item)) for t in CRYPTO_VENUES]
            elif not feed and market in ("NSE", "MCX") and ":" in str(item.get("tv") or ""):
                feed = [str(item["tv"]).strip()]
            tv = str(item.get("tv") or "") or (f"{crypto_base(sym, item)}USD" if sym.endswith("-USD") else "")
            rows.append({"symbol": sym, "name": str(item.get("name", sym)), "tv": tv, "feed": feed,
                         "optional": bool(item.get("optional", False))})
        return rows, "from config.yaml"
    raise ValueError(f"unknown source '{src}'")


def build(cfg: dict, only=None):
    """Returns (instruments, notes). `notes[universe_key]` says how each list was obtained or why it failed."""
    insts: dict[str, Instrument] = {}
    notes: dict[str, str] = {}
    source = cfg.get("data_source", "auto")
    for ukey, u in cfg["universes"].items():
        if not u.get("enabled", True) or (only and ukey not in only):
            continue
        if u.get("sources") and source not in u["sources"]:       # e.g. MCX needs TradingView, global proxies need Yahoo
            continue
        try:
            rows, note = _rows_for(u, cfg)
        except Exception as e:                                  # noqa: BLE001
            notes[ukey] = f"FAILED: {e}"
            log.error("Universe %s failed: %s", ukey, e)
            continue
        tfs = set(u.get("timeframes") or cfg["timeframes"])
        for r in rows:
            inst = insts.get(r["symbol"])
            optional = bool(r.get("optional"))
            if inst is None:
                feed = list(r.get("feed") or [])
                if not feed and u["market"] == "NSE" and ":" in r["tv"]:
                    feed = [r["tv"]]
                inst = insts[r["symbol"]] = Instrument(
                    symbol=r["symbol"], name=r["name"], market=u["market"],
                    tv=r["tv"], currency=u.get("currency", ""), feed=feed, optional=optional)
            else:
                inst.optional = inst.optional and optional
            if ukey not in inst.universes:
                inst.universes.append(ukey)
            inst.tfs |= tfs
        notes[ukey] = f"{len(rows)} symbols ({note})"
    winners = load_winners(CACHE_DIR / "tv_symbols.json") if source != "yahoo" else {}
    for inst in insts.values():
        if inst.market == "US":                                  # which exchange? Nasdaq-100 names are on NASDAQ
            order = US_EXCHANGES if "nasdaq100" in inst.universes else ("NYSE", "NASDAQ", "AMEX")
            inst.feed = [f"{ex}:{tv_us(inst.symbol)}" for ex in order]
        if winners.get(inst.symbol):                             # exact link once we know where the data came from
            inst.tv = winners[inst.symbol]
    return list(insts.values()), notes
