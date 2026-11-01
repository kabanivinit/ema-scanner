"""Small display helpers shared by alerts and reports."""
from __future__ import annotations

import pandas as pd

from .markets import MARKETS


def price(p: float) -> str:
    return f"{p:,.2f}" if abs(p) >= 1 else f"{p:.6g}"


def bar_label(tf: str, end: pd.Timestamp, market_key: str, display_tz: str) -> str:
    """Human label for when a bar closed."""
    if tf in ("15m", "1h", "4h"):
        return end.tz_convert(display_tz).strftime("%d %b %H:%M %Z")
    local = (end - pd.Timedelta(minutes=1)).tz_convert(MARKETS[market_key].tz)
    day = local.strftime("%d %b %Y")
    return f"Daily close {day}" if tf == "1d" else f"Week ending {day}"


def direction_text(direction: str) -> str:
    return "🟢 Bullish ↑" if direction == "bull" else "🔴 Bearish ↓"
