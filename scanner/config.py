"""Loads config.yaml (with safe defaults) and explains mistakes in plain English."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
VALID_TFS = ("15m", "1h", "4h", "1d", "1w")
VALID_MARKETS = ("NSE", "US", "CRYPTO", "FUT")

DEFAULTS = {
    "ema": {"short": 10, "long": 20},
    "timeframes": list(VALID_TFS),
    "min_bars": 60,
    "display_timezone": "Asia/Kolkata",
    "universe_refresh_days": 7,
    "alerts": {
        "directions": "both",
        "tradingview_links": True,
        "max_lines_per_message": 40,
        "min_data_coverage": 0.80,
        "health_alert_hours": 6,
    },
    "scan_last_bars": {"15m": 3, "1h": 2, "4h": 2, "1d": 2, "1w": 2},
    "settle_minutes": {"15m": 1, "1h": 2, "4h": 2, "1d": 15, "1w": 15},
    "max_alert_age_minutes": {"15m": 180, "1h": 600, "4h": 1500, "1d": 4320, "1w": 14400},
    "report": {"fresh_bars": {"15m": 4, "1h": 4, "4h": 3, "1d": 3, "1w": 2}, "max_rows_per_table": 40},
    "yahoo": {
        "batch_size": 50, "pause_seconds": 2.0, "threads": 4, "retries": 2, "max_seconds": 900,
        "periods": {"15m": "30d", "1h": "90d", "1d": "3y"},
    },
    "universes": {},
    "lists": {},
}


class ConfigError(Exception):
    """Raised with a message a non-coder can act on."""


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("universes", "lists"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _validate(cfg: dict) -> None:
    e = cfg["ema"]
    if not (isinstance(e["short"], int) and isinstance(e["long"], int) and 1 <= e["short"] < e["long"]):
        raise ConfigError("config.yaml: ema short must be a whole number smaller than ema long (e.g. 10 and 20).")
    bad = [t for t in cfg["timeframes"] if t not in VALID_TFS]
    if bad:
        raise ConfigError(f"config.yaml: unknown timeframe(s) {bad}. Allowed: {', '.join(VALID_TFS)}.")
    if cfg["alerts"]["directions"] not in ("both", "bullish", "bearish"):
        raise ConfigError("config.yaml: alerts -> directions must be both, bullish or bearish.")
    for key, u in cfg["universes"].items():
        if u.get("market") not in VALID_MARKETS:
            raise ConfigError(f"config.yaml: universe '{key}' has market '{u.get('market')}'. Allowed: {', '.join(VALID_MARKETS)}.")
        bad = [t for t in (u.get("timeframes") or []) if t not in VALID_TFS]
        if bad:
            raise ConfigError(f"config.yaml: universe '{key}' has unknown timeframe(s) {bad}.")
        if u.get("source") == "list" and u.get("list") not in cfg["lists"]:
            raise ConfigError(f"config.yaml: universe '{key}' points to list '{u.get('list')}' which does not exist under 'lists'.")
    if not any(u.get("enabled", True) for u in cfg["universes"].values()):
        raise ConfigError("config.yaml: every universe is switched off (enabled: false) - nothing to scan.")


def load_config(path=None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    if not p.exists():
        raise ConfigError(f"Cannot find {p.name}. It must sit in the main folder of the project.")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark else ""
        raise ConfigError(f"config.yaml has a formatting mistake{where}. Check the spaces at the start of lines "
                          f"and that every value after a colon has a space before it.") from exc
    cfg = _merge(DEFAULTS, raw)
    _validate(cfg)
    return cfg
