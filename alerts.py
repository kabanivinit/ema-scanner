"""Builds the Telegram messages (grouped, so 40 crossovers = 1 message, not 40)."""
from __future__ import annotations

import html
from dataclasses import dataclass

from .fmt import bar_label, price
from .universe import tv_link


@dataclass
class Message:
    text: str
    signals: list


def _ukey(inst, cfg):
    order = list(cfg["universes"].keys())
    keys = sorted(inst.universes, key=lambda k: order.index(k) if k in order else 99)
    label = " + ".join(cfg["universes"][k].get("label", k) for k in keys)
    return (order.index(keys[0]) if keys and keys[0] in order else 99, label)


def _header(tf, direction, label, cfg) -> str:
    s, l = cfg["ema"]["short"], cfg["ema"]["long"]
    if direction == "bull":
        head = f"🟢 <b>BULLISH CROSS · {tf.upper()}</b>\nEMA {s} (green) crossed above EMA {l} (red)"
    else:
        head = f"🔴 <b>BEARISH CROSS · {tf.upper()}</b>\nEMA {s} (green) crossed below EMA {l} (red)"
    return f"{head}\n🕒 {html.escape(label)}"


def _line(sig, cfg) -> str:
    inst = sig.inst
    name = html.escape(inst.display)
    if cfg["alerts"]["tradingview_links"] and inst.tv:
        name = f'<a href="{html.escape(tv_link(inst.tv), quote=True)}">{name}</a>'
    gap = (sig.ema_s / sig.ema_l - 1.0) * 100.0 if sig.ema_l else 0.0
    return f"• {name}  {html.escape(inst.currency)}{price(sig.close)}  ({gap:+.2f}%)"


def build_messages(signals, cfg) -> list:
    groups = {}
    for s in signals:
        label = bar_label(s.tf, s.bar_end, s.inst.market, cfg["display_timezone"])
        groups.setdefault((s.tf, s.direction, label), []).append(s)
    tf_order = {tf: i for i, tf in enumerate(cfg["timeframes"])}
    max_lines = int(cfg["alerts"]["max_lines_per_message"])
    out = []
    ordered = sorted(groups.items(),
                     key=lambda kv: (tf_order.get(kv[0][0], 9), kv[0][1] != "bull", min(x.bar_end for x in kv[1])))
    for (tf, direction, label), sigs in ordered:
        head = _header(tf, direction, label, cfg)
        sections = {}
        for s in sigs:
            sections.setdefault(_ukey(s.inst, cfg), []).append(s)
        text, batch, lines = head, [], 0
        for (_, ulabel), items in sorted(sections.items()):
            items.sort(key=lambda x: x.inst.display)
            sec = f"\n\n<b>{html.escape(ulabel)}</b> ({len(items)})"
            opened = False
            for s in items:
                line = _line(s, cfg)
                if batch and (lines >= max_lines or len(text) + len(line) + len(sec) > 3800):
                    out.append(Message(text, batch))
                    text, batch, lines, opened = head + "\n(continued)", [], 0, False
                if not opened:
                    text += sec
                    opened = True
                text += "\n" + line
                batch.append(s)
                lines += 1
        if batch:
            out.append(Message(text, batch))
    return out


def live_message(cfg, instruments, notes) -> str:
    s, l = cfg["ema"]["short"], cfg["ema"]["long"]
    lines = [f"✅ <b>EMA {s}/{l} scanner is live</b>",
             f"Timeframes: {', '.join(cfg['timeframes'])}", ""]
    for k, u in cfg["universes"].items():
        if not u.get("enabled", True) or k not in notes:
            continue
        n = sum(1 for i in instruments if k in i.universes)
        bad = notes.get(k, "").startswith("FAILED")
        lines.append(f"{'⚠️' if bad else '•'} {html.escape(u.get('label', k))}: "
                     f"{'could not load' if bad else str(n) + ' symbols'}")
    lines += ["", "Alerts start from the next closed candle. Crossovers already visible right now were "
                  "recorded silently so you are not flooded."]
    return "\n".join(lines)


def health_message(problems) -> str:
    return "⚠️ <b>Scanner data problem</b>\n" + "\n".join(f"• {html.escape(p)}" for p in problems) + \
           "\nAlerts for these are paused and will retry automatically."
