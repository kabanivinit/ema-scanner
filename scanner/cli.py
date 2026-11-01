"""Commands:  scan | report | doctor | test-telegram"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import pandas as pd

from . import __version__, alerts
from . import bars as bars_mod
from . import report as report_mod
from .config import ROOT, ConfigError, load_config
from .data import make_fetcher
from .engine import collect, market_tfs, scan_once
from .fmt import bar_label
from .markets import MARKETS, expected_latest_end
from .state import is_sent, load_state, mark_sent, prune, save_state
from .telegram import ConsoleSender, Telegram
from .universe import build

log = logging.getLogger("scanner")
PAUSE = 1.1          # seconds between Telegram messages (Telegram allows ~1/sec per chat)


def _now(args) -> pd.Timestamp:
    if getattr(args, "now", None):
        t = pd.Timestamp(args.now)
        return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return pd.Timestamp.now(tz="UTC")


def _only(args):
    return set(args.only.split(",")) if getattr(args, "only", None) else None


def _sender(args):
    if args.dry_run:
        return ConsoleSender()
    tg = Telegram.from_env()
    if tg is None:
        log.error("Telegram is not set up: add the secrets TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
                  "(GitHub > Settings > Secrets and variables > Actions). See section 4 of the guide.")
    return tg


def cmd_scan(args, cfg) -> int:
    now = _now(args)
    sender = _sender(args)
    if sender is None:
        return 2
    state_path = os.environ.get("SCANNER_STATE")
    state = load_state(state_path)
    bootstrap = state["created"] is None
    instruments, notes = build(cfg, _only(args))
    if not instruments:
        sender.send(alerts.health_message([f"{k}: {v}" for k, v in notes.items()] or ["no symbols loaded"]))
        log.error("No symbols could be loaded: %s", notes)
        return 2

    outcome = scan_once(cfg, instruments, state, now, make_fetcher(cfg))
    fresh = [s for s in outcome.signals if not is_sent(state, s)]
    log.info("Evaluated %d results; %d crossover(s) in window, %d new", len(outcome.collected.results),
             len(outcome.signals), len(fresh))

    pause = 0 if args.dry_run else PAUSE
    send_failed = False
    if bootstrap:
        if not sender.send(alerts.live_message(cfg, instruments, notes)):
            log.error("Could not send the first Telegram message: %s", getattr(sender, "last_error", ""))
            return 1
        for s in fresh:
            mark_sent(state, s)
        state["created"] = now.isoformat()
        sent_n = 0
    else:
        sent_n = 0
        for msg in alerts.build_messages(fresh, cfg):
            if sender.send(msg.text):
                for s in msg.signals:
                    mark_sent(state, s)
                sent_n += len(msg.signals)
            else:
                send_failed = True
                log.error("Telegram send failed: %s", getattr(sender, "last_error", ""))
            time.sleep(pause)

    problems = list(outcome.health) + [f"{k}: {v}" for k, v in notes.items() if v.startswith("FAILED")]
    gap = float(cfg["alerts"]["health_alert_hours"]) * 3600
    if problems and now.timestamp() - state["health_last"] > gap:
        if sender.send(alerts.health_message(problems)):
            state["health_last"] = now.timestamp()

    if send_failed:
        log.warning("Some alerts could not be delivered - they will be retried on the next run.")
    else:
        state["checkpoints"].update(outcome.checkpoints)      # only advance when everything was delivered
    prune(state, now, cfg)
    if not args.dry_run:
        save_state(state, state_path)
    log.info("Done: %d alert(s) sent%s. Skipped (not ready): %s", sent_n,
             " (first run: baseline recorded)" if bootstrap else "", outcome.skipped or "none")
    return 1 if send_failed else 0


def cmd_report(args, cfg) -> int:
    now = _now(args)
    instruments, notes = build(cfg, _only(args))
    if not instruments:
        log.error("No symbols could be loaded: %s", notes)
        return 2
    wanted = {m: market_tfs(cfg, instruments, m) for m in sorted({i.market for i in instruments})}
    coll = collect(cfg, instruments, wanted, now, make_fetcher(cfg))
    md, df = report_mod.build_report(coll, instruments, cfg, now, notes)

    out = ROOT / "reports"
    (out / "archive").mkdir(parents=True, exist_ok=True)
    day = now.tz_convert(cfg["display_timezone"]).strftime("%Y-%m-%d")
    (out / "latest.md").write_text(md, encoding="utf-8")
    (out / "archive" / f"{day}.md").write_text(md, encoding="utf-8")
    df.to_csv(out / "latest.csv", index=False)
    log.info("Report written: %s (%d rows in CSV)", out / "latest.md", len(df))

    if not args.no_telegram:
        sender = _sender(args)
        if sender is None:
            return 2
        sender.send(report_mod.summary_text(coll, cfg, now, notes))
        sender.send_document(str(out / "latest.md"), "EMA crossover report (Markdown)")
        sender.send_document(str(out / "latest.csv"), "Full data (open in Excel)")
    return 0


def cmd_test_telegram(args, cfg) -> int:
    sender = _sender(args)
    if sender is None:
        return 2
    ok = sender.send(f"✅ <b>Telegram connected</b>\nEMA {cfg['ema']['short']}/{cfg['ema']['long']} scanner "
                     f"v{__version__} can reach this chat.")
    if not ok:
        log.error("Telegram rejected the message: %s", getattr(sender, "last_error", ""))
        return 1
    log.info("Test message sent.")
    return 0


def cmd_doctor(args, cfg) -> int:
    now = _now(args)
    results = []

    def say(level, msg):
        print(f"[{level}] {msg}", flush=True)
        results.append(level)

    say("INFO", f"Scanner v{__version__} · Python {sys.version.split()[0]} · pandas {pd.__version__}")
    tg = Telegram.from_env()
    if tg is None:
        say("FAIL", "Telegram secrets are missing (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
    else:
        me = tg.get_me()
        say("PASS" if me else "FAIL",
            f"Telegram bot found: @{me.get('username')}" if me else f"Telegram rejected the token: {tg.last_error}")

    instruments, notes = build(cfg, _only(args))
    for k, v in notes.items():
        label = cfg["universes"][k].get("label", k)
        say("FAIL" if v.startswith("FAILED") else "PASS", f"{label}: {v}")

    fetcher = make_fetcher(cfg)
    probe_tf = {"15m": "15m", "1h": "1h", "1d": "1d"}
    for market in sorted({i.market for i in instruments}):
        m = MARKETS[market]
        for itv, tf in probe_tf.items():
            data, _ = fetcher([m.benchmark], itv)
            df = data.get(m.benchmark)
            if df is None:
                say("FAIL", f"{market}: Yahoo returned no {itv} candles for {m.benchmark}")
                continue
            frame = bars_mod.frame_for(tf, df, m)
            closed = bars_mod.closed_only(frame, now, cfg["settle_minutes"][tf])
            exp = expected_latest_end(m, tf, now, cfg["settle_minutes"][tf])
            if closed.empty:
                say("WARN", f"{market} {itv}: {len(df)} candles but none closed yet")
                continue
            last = closed["end"].max()
            tz = cfg["display_timezone"]
            fmt = lambda t: t.tz_convert(tz).strftime("%d %b %H:%M %Z")
            ok = last >= exp
            say("PASS" if ok else "WARN",
                f"{market} {itv}: {len(df)} candles · newest closed candle ended {fmt(last)} · expected {fmt(exp)}"
                + ("" if ok else "  (normal on weekends/holidays or if Yahoo is a few minutes late)"))

    if instruments:
        step = max(1, len(instruments) // 25)
        sample = instruments[::step][:25]
        got, failed = fetcher([i.symbol for i in sample], "1d")
        say("PASS" if len(got) >= 0.8 * len(sample) else "FAIL",
            f"Yahoo sample test: data for {len(got)} of {len(sample)} random symbols"
            + (f" (missing: {', '.join(failed[:8])})" if failed else ""))

    if args.send_test and tg:
        counts = {lvl: results.count(lvl) for lvl in ("PASS", "WARN", "FAIL")}
        tg.send(f"🩺 <b>Setup check</b>: {counts['PASS']} passed, {counts['WARN']} warnings, {counts['FAIL']} failed.\n"
                f"Telegram is connected. Details are in the GitHub run log.")
    fails = results.count("FAIL")
    print(f"\nSUMMARY: {results.count('PASS')} passed, {results.count('WARN')} warnings, {fails} failed", flush=True)
    return 1 if fails else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scanner", description="EMA crossover scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "report", "doctor", "test-telegram"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", help="path to config.yaml")
        sp.add_argument("--dry-run", action="store_true", help="print messages instead of sending; no state saved")
        sp.add_argument("--only", help="comma-separated universe keys, e.g. crypto,indian_indices")
        sp.add_argument("--now", help=argparse.SUPPRESS)
        if name == "report":
            sp.add_argument("--no-telegram", action="store_true")
        if name == "doctor":
            sp.add_argument("--send-test", action="store_true", help="also send a Telegram test message")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"[FAIL] {e}")
        return 2
    return {"scan": cmd_scan, "report": cmd_report, "doctor": cmd_doctor,
            "test-telegram": cmd_test_telegram}[args.cmd](args, cfg)
