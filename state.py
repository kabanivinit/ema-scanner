"""Remembers what was already alerted, so each crossover is sent once."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import ROOT

DEFAULT_PATH = ROOT / "state" / "state.json"


def empty_state() -> dict:
    return {"version": 1, "created": None, "checkpoints": {}, "sent": {}, "health_last": 0}


def load_state(path=None) -> dict:
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return empty_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        return empty_state()
    base = empty_state()
    base.update(data)
    return base


def save_state(state: dict, path=None) -> None:
    p = Path(path) if path else DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def is_sent(state: dict, sig) -> bool:
    return sig.key in state["sent"].get(sig.tf, {})


def mark_sent(state: dict, sig) -> None:
    state["sent"].setdefault(sig.tf, {})[sig.key] = int(sig.bar_end.timestamp())


def prune(state: dict, now, cfg: dict) -> None:
    """Forget alerts older than the longest time they could still be re-detected."""
    for tf, keys in list(state["sent"].items()):
        keep_seconds = (cfg["max_alert_age_minutes"].get(tf, 1440) + 60) * 60
        limit = int(now.timestamp()) - keep_seconds
        state["sent"][tf] = {k: v for k, v in keys.items() if v >= limit}
