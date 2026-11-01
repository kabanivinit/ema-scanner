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
    symbol: str                       # Yahoo Finance symbol
    name: str
    market: str                       # NSE | US | CRYPTO | FUT
    universes: list = field(default_factory=list)
    tv: str = ""                      # TradingView symbol for the clickable link
    currency: str = ""
    tfs: set = field(default_factory=set)

    @property
    def display(self) -> str:
        s = self.symbol
        if s.startswith("^") or s.endswith("=F"):
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


def _wiki_table(url: str, want: str):
    html = _http_get(url)
    for t in pd.read_html(io.StringIO(html)):
        cols = {(" ".join(map(str, c)) if isinstance(c, tuple) else str(c)).strip().lower(): c for c in t.columns}
        if want in cols:
            return t, cols
    raise ValueError(f"no table with a '{want}' column")


def _us_symbols(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper().str.replace(".", "-", regex=False)


def fetch_sp500() -> pd.DataFrame:
    t, cols = _wiki_table(SP500_URL, "symbol")
    return pd.DataFrame({"symbol": _us_symbols(t[cols["symbol"]]),
                         "name": t[cols.get("security", cols["symbol"])].astype(str)})


def fetch_nasdaq100() -> pd.DataFrame:
    try:
        t, cols = _wiki_table(NDX_URL, "ticker")
        sym = cols["ticker"]
    except ValueError:
        t, cols = _wiki_table(NDX_URL, "symbol")
        sym = cols["symbol"]
    name = cols.get("company", sym)
    return pd.DataFrame({"symbol": _us_symbols(t[sym]), "name": t[name].astype(str)})


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


def _load_list(name: str, fetch_fn, refresh_days: int):
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
        raise RuntimeError(f"could not download the list and no saved copy exists ({str(e)[:120]})") from e


# ---------------------------------------------------------------- TradingView links
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
        df, note = _load_list("nasdaq100", fetch_nasdaq100, days)
        return [{"symbol": r.symbol, "name": r.name, "tv": tv_us(r.symbol)} for r in df.itertuples()], note
    if src == "list":
        rows = []
        for item in cfg["lists"][u["list"]]:
            sym = str(item["symbol"]).strip()
            tv = item.get("tv") or (f"{sym[:-4]}USD" if sym.endswith("-USD") else "")
            rows.append({"symbol": sym, "name": str(item.get("name", sym)), "tv": tv})
        return rows, "from config.yaml"
    raise ValueError(f"unknown source '{src}'")


def build(cfg: dict, only=None):
    """Returns (instruments, notes). `notes[universe_key]` says how each list was obtained or why it failed."""
    insts: dict[str, Instrument] = {}
    notes: dict[str, str] = {}
    for ukey, u in cfg["universes"].items():
        if not u.get("enabled", True) or (only and ukey not in only):
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
            if inst is None:
                inst = insts[r["symbol"]] = Instrument(
                    symbol=r["symbol"], name=r["name"], market=u["market"],
                    tv=r["tv"], currency=u.get("currency", ""))
            if ukey not in inst.universes:
                inst.universes.append(ukey)
            inst.tfs |= tfs
        notes[ukey] = f"{len(rows)} symbols ({note})"
    return list(insts.values()), notes
