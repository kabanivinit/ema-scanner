"""Markdown + CSV report: market breadth, fresh crossovers, data coverage."""
from __future__ import annotations

import html

import pandas as pd

from .fmt import bar_label, direction_text, price
from .markets import BASE_INTERVAL


def _by_universe(results) -> dict:
    out = {}
    for r in results:
        for k in r.inst.universes:
            out.setdefault(k, []).append(r)
    return out


def _breadth(rs, tf) -> str:
    ok = [r for r in rs if r.tf == tf and r.summary]
    if not ok:
        return "–"
    bull = sum(1 for r in ok if r.summary["state"] == "bull")
    return f"{bull * 100 / len(ok):.0f}% ({bull}/{len(ok)})"


def _fresh(rs, tf, cfg) -> list:
    n = cfg["report"]["fresh_bars"][tf]
    rows = [r for r in rs if r.tf == tf and r.summary
            and r.summary["bars_since_cross"] is not None and r.summary["bars_since_cross"] < n]
    rows.sort(key=lambda r: (r.summary["bars_since_cross"], r.inst.display))
    return rows


def _table(headers, rows) -> list:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def build_report(coll, instruments, cfg, now, notes):
    """Returns (markdown_text, csv_dataframe)."""
    tz = cfg["display_timezone"]
    s, l = cfg["ema"]["short"], cfg["ema"]["long"]
    stamp = now.tz_convert(tz).strftime("%d %b %Y %H:%M %Z")
    unis = [(k, u) for k, u in cfg["universes"].items() if u.get("enabled", True)]
    by_u = _by_universe(coll.results)
    tfs = cfg["timeframes"]
    max_rows = int(cfg["report"]["max_rows_per_table"])

    md = [f"# EMA {s}/{l} Crossover Report", "",
          f"Generated **{stamp}** · closed candles only · EMA {s} = green line, EMA {l} = red line", ""]

    md += ["## 1. Market breadth", "",
           f"Share of symbols where EMA {s} is **above** EMA {l} (bullish) at the last closed candle.", ""]
    rows = []
    for k, u in unis:
        rs = by_u.get(k, [])
        n = len({r.inst.symbol for r in rs})
        rows.append([u.get("label", k), n] + [_breadth(rs, tf) for tf in tfs])
    md += _table(["Universe", "Symbols"] + [t.upper() for t in tfs], rows) + [""]

    fresh_cfg = cfg["report"]["fresh_bars"]
    fresh_desc = ", ".join(t.upper() + ": " + str(fresh_cfg[t]) for t in tfs)
    md += ["## 2. Fresh crossovers", "",
           f"Crossovers that happened within the last few closed candles ({fresh_desc}). Newest first.", ""]
    for k, u in unis:
        rs = by_u.get(k, [])
        md += [f"### {u.get('label', k)}", ""]
        any_rows = False
        for tf in tfs:
            fr = _fresh(rs, tf, cfg)
            if not fr:
                continue
            any_rows = True
            up = sum(1 for r in fr if r.summary["last_cross_dir"] == "bull")
            md += [f"**{tf.upper()}** — {up} bullish, {len(fr) - up} bearish", ""]
            trows = []
            for r in fr[:max_rows]:
                sm = r.summary
                trows.append([r.inst.display, direction_text(sm["last_cross_dir"]),
                              bar_label(tf, sm["last_cross_end"], r.inst.market, tz), sm["bars_since_cross"],
                              f"{r.inst.currency}{price(sm['close'])}", price(sm["ema_s"]), price(sm["ema_l"]),
                              f"{sm['gap_pct']:+.2f}%"])
            md += _table(["Symbol", "Cross", "Candle closed", "Bars ago", "Close", f"EMA {s}", f"EMA {l}", "Gap"], trows)
            if len(fr) > max_rows:
                md += ["", f"_+{len(fr) - max_rows} more - see the CSV._"]
            md += [""]
        if not any_rows:
            md += ["_No fresh crossovers._", ""]

    md += ["## 3. Data coverage", ""]
    rows = []
    for k, u in unis:
        rs = by_u.get(k, [])
        fail = sorted({r.inst.display for r in rs if r.summary is None and r.reason.startswith("no data")})
        short = sorted({r.inst.display for r in rs if r.summary is None and not r.reason.startswith("no data")})
        total = len({r.inst.symbol for r in rs})
        rows.append([u.get("label", k), total, notes.get(k, ""),
                     ", ".join(fail[:25]) + (f" … (+{len(fail) - 25})" if len(fail) > 25 else "") or "none",
                     len(short)])
    md += _table(["Universe", "Symbols", "List source", "No data from Yahoo", "Too little history"], rows) + [""]
    md += ["## 4. Notes", "",
           "- Signals use closed candles only (same as TradingView alert 'once per bar close').",
           "- Commodities are **global futures proxies** (COMEX/NYMEX/ICE), not MCX prices.",
           "- Yahoo data can differ slightly from TradingView's feed; crossovers right on the edge may differ.", ""]

    recs = []
    for r in coll.results:
        base = {"universes": "; ".join(r.inst.universes), "symbol": r.inst.symbol, "name": r.inst.name,
                "timeframe": r.tf}
        if r.summary:
            sm = r.summary
            recs.append({**base, "state": sm["state"], "ema_short": sm["ema_s"], "ema_long": sm["ema_l"],
                         "gap_pct": sm["gap_pct"], "close": sm["close"],
                         "last_cross": sm["last_cross_dir"] or "",
                         "bars_since_cross": sm["bars_since_cross"] if sm["bars_since_cross"] is not None else "",
                         "last_cross_candle_closed": (bar_label(r.tf, sm["last_cross_end"], r.inst.market, tz)
                                                      if sm["last_cross_end"] is not None else ""),
                         "last_candle_closed": bar_label(r.tf, sm["last_end"], r.inst.market, tz), "note": ""})
        else:
            recs.append({**base, "state": "n/a", "note": r.reason})
    return "\n".join(md), pd.DataFrame(recs)


def summary_text(coll, cfg, now, notes) -> str:
    """Short Telegram digest (HTML)."""
    tz = cfg["display_timezone"]
    s, l = cfg["ema"]["short"], cfg["ema"]["long"]
    tfs = cfg["timeframes"]
    by_u = _by_universe(coll.results)
    lines = [f"📊 <b>EMA {s}/{l} report</b> · {now.tz_convert(tz).strftime('%d %b %H:%M %Z')}", ""]
    for k, u in cfg["universes"].items():
        if not u.get("enabled", True):
            continue
        rs = by_u.get(k, [])
        lines.append(f"<b>{html.escape(u.get('label', k))}</b> — bullish share: " +
                     " · ".join(f"{t} {_breadth(rs, t).split(' ')[0]}" for t in tfs))
        fresh = []
        for t in tfs:
            fr = _fresh(rs, t, cfg)
            if fr:
                up = sum(1 for r in fr if r.summary["last_cross_dir"] == "bull")
                fresh.append(f"{t} ↑{up} ↓{len(fr) - up}")
        lines.append("  fresh: " + (" · ".join(fresh) if fresh else "none"))
    lines += ["", "Full report attached."]
    return "\n".join(lines)
