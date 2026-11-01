# EMA 10/20 Crossover Scanner — Guide & Work Log

Prepared 01 Nov 2026 · version 1.1.1 (prices from TradingView) · for a non-coder (no programming needed after the one-time setup)

---

## 0. Status at a glance

| Item | Status |
|---|---|
| Pine script updated to EMA **10 (green)** / **20 (red)** | Done — `ema_crossover.pine` |
| Scanner that applies the same logic to about 1,100 symbols on 15m / 1h / 4h / 1D / 1W | Built and tested offline (83 automated tests passing) |
| **Prices from TradingView** (your change request) | Built. Yahoo Finance stays only as an automatic backup (everything except MCX) |
| Telegram alerts (grouped, de-duplicated, clickable TradingView links) | Built and tested with a simulated Telegram |
| Daily report (Markdown + CSV, also sent to Telegram) | Built and tested |
| **Live report with real prices** | **Not produced yet.** My workspace cannot reach TradingView, Yahoo or Telegram, so no live data was pulled. Your first GitHub run (Section 4) produces it in about 10–15 minutes. |
| MCX commodities and more NSE indices | **Now included** through TradingView (19 MCX contracts, 30 indices). The earlier "Phase 2 broker feed" is no longer needed for this. |
| ⚠ Read Section 8 before relying on it | TradingView's free feed is **unofficial**, **about 15 minutes delayed** for NSE / US / MCX, and TradingView's Terms of Use restrict this kind of use. |

---

## 1. What you asked for → what was built

| You asked | Delivered |
|---|---|
| Run the Pine script logic on 6 groups of securities and report | Python scanner replicating the script exactly (Section 5), plus a daily report |
| Alert on Telegram when Short (10, green) crosses Long (20, red) on 15m, 1h, 4h, 1D, 1W | `1 - EMA Scan` runs every 15 minutes, sends one grouped message per timeframe/direction |
| Markdown file of all work | This file |
| Simple for a non-coder | All settings in one file (`config.yaml`); setup is clicks only; a "Check Setup" button tells you what is wrong in plain words |
| **"Instead of Yahoo, use TradingView"** | Candles now come from TradingView's chart feed. One setting, `data_source` in `config.yaml`, picks the source: `auto` (default: TradingView first, Yahoo as backup), `tradingview` or `yahoo`. |

---

## 2. How it works (one minute)

```
GitHub (free, runs by itself every 15 min)
   │  asks TradingView's chart feed for candles (the same feed its website charts use; no login)
   │  rebuilds 4h and 1W candles exactly like TradingView
   │  waits for the feed's delay (Section 5) so a half-built candle is never judged
   │  computes EMA10 & EMA20, finds crossovers on CLOSED candles only
   │  skips anything already alerted (remembers in a small saved file)
   │  if TradingView cannot be reached: Yahoo backup (all groups except MCX)
   ▼
Telegram message ──► you
Daily 17:10 IST ──► report (.md + .csv) in Telegram and in your GitHub repo
```

TradingView's own alerts cannot do this: one alert covers one symbol on one timeframe, so 1,100 symbols × 5 timeframes is far beyond what alerts allow. That is why the scanner exists. You can still use the Pine script on charts for visual checking.

---

## 3. What is scanned

| # | Your group | What the scanner uses | Count | Source of list | TradingView code |
|---|---|---|---|---|---|
| 1 | Nifty 500 | All constituents | ~500 | Downloaded from NSE, refreshed weekly | `NSE:<symbol>` |
| 2 | Nasdaq 100 | All constituents | ~100 | Downloaded from Wikipedia, weekly | `NASDAQ:`, else `NYSE:`, else `AMEX:` (first that exists) |
| 3 | Crypto | Curated list of major coins | 48 | `config.yaml` (editable) | `BINANCE:XYZUSDT`, else `COINBASE:`, `KRAKEN:`, `BITSTAMP:` (USD) |
| 4 | Commodities (Indian exchanges) | **MCX front-month contracts**: Gold (+Mini), Silver (+Mini, Micro), Crude Oil (+Mini), Natural Gas (+Mini), Copper, Zinc (+Mini), Lead (+Mini), Aluminium (+Mini), Nickel, Mentha Oil, Crude Palm Oil | 19 | `config.yaml` | `MCX:GOLD1!` etc. |
| 5 | S&P 500 | All constituents | ~500 | Downloaded from Wikipedia, weekly | `NYSE:`, else `NASDAQ:`, else `AMEX:` |
| 6 | Indian indices | Nifty 50, Bank, Sensex, Next 50, Midcap 50/100/150, Smallcap 100/250, Nifty 100, 200, 500, Private Bank, Consumption, India VIX and 15 sector indices (IT, Auto, Pharma, FMCG, Metal, Realty, Energy, Infrastructure, PSU Bank, Media, Financial Services, PSE, Commodities, Services, MNC) | 30 | `config.yaml` | `NSE:CNXIT` etc. |

Overlaps (e.g. Apple is in S&P 500 and Nasdaq 100) are fetched once and alerted once, labelled with both groups. Once TradingView has answered for a symbol, the exchange that worked is remembered, so later runs are faster.

**Honest gaps:**
- **Commodities = MCX only.** NCDEX agricultural contracts are not included. Add any you want by copying a line in `config.yaml` (Section 6).
- **MCX "1!" contracts roll over.** The front-month contract is a chain; when it moves to the next month the price can jump, which may cause one false-looking cross around roll-over dates. Eight of the 19 contracts (mostly the Mini/Micro ones) are marked `optional` because I could not confirm their codes.
- **Indian indices: 30, not every one of the 100+.** Add more by copying a line. Nine of the 30 are marked `optional` (code not confirmed) so a wrong code never triggers an alarm.
- **Crypto: 48 major coins, not "all".** Add more by copying a line in `config.yaml`.
- **Yahoo-only mode** (if you ever choose `data_source: yahoo`) replaces MCX with ten global futures (Gold, Silver, Copper, Crude WTI, Brent, Natural Gas, Aluminium, Platinum, Cotton, Soybeans) and drops the 9 extra indices.

---

## 4. One-time setup (about 20 minutes)

### Step A — Create your Telegram bot
1. In Telegram, search **@BotFather** → send `/newbot` → choose a name and a username ending in `bot`.
2. BotFather replies with a **token** (looks like `123456789:ABCdef…`). Keep it private — never paste it in chats or public places.
3. Open your new bot, press **Start**, send "hi".
4. Find your **chat ID**: in a browser open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` (replace `<YOUR_TOKEN>`, keep the word `bot` in front). Look for `"chat":{"id":` followed by a number — that number is your chat ID. (For a group: add the bot to the group, send a message there, reload the page; group IDs start with `-`.)

### Step B — Create the GitHub project
1. Create a free account at github.com (skip if you have one).
2. Click **New repository** → name it `ema-scanner` → choose **Public** → **Create repository**.
   *Why public:* GitHub runs public projects free. A private project has a small monthly free allowance that a 15-minute schedule will exceed. Your Telegram token stays hidden in Secrets (Step C); only the scanner code, settings and reports are visible.
3. Unzip `ema-scanner.zip` on your computer. On the new repository page click **uploading an existing file**, drag **everything inside the unzipped folder** (including the `.github` folder) into the page, then click **Commit changes**.
   - Mac hides folders starting with a dot: in Finder press **Cmd + Shift + .** to show `.github`.
   - If `.github` did not upload: click **Add file → Create new file**, type `.github/workflows/scan.yml` as the file name (typing `/` creates folders), paste the text of `workflow_files_to_paste/scan.yml`, commit. Repeat for `check-setup.yml` and `daily-report.yml`.

### Step C — Add your two secrets
Repository → **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name (exactly) | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from Step A |
| `TELEGRAM_CHAT_ID` | the number from Step A |

No TradingView account, password or key is needed. The scanner never logs in to TradingView.

### Step D — Switch it on (run in this order)
Open the **Actions** tab (click the green button if GitHub asks to enable workflows), choose a workflow on the left, click **Run workflow**.

1. **0 - Check Setup (run this first)** — about 3–5 minutes. The log shows `[PASS]`, `[WARN]` or `[FAIL]` lines in plain words, and Telegram shows a "Setup check" message. It tells you whether TradingView answers from GitHub's servers, whether its newest candles are current, how delayed each market is, and which symbol codes it does not recognise. Good output looks like:
   ```
   [PASS] TradingView NSE 15m: 400 candles · newest usable candle ended ... · expected ...
   [PASS] NSE: TradingView data mode 'delayed_streaming_900' = 15 min delay (matches config)
   [PASS] TradingView sample test: data for 23 of 25 spread-out symbols
   ```
   Fix any `[FAIL]` using Section 10, or send me the log text. (`[WARN]` lines about weekends or holidays are normal.)
2. **2 - Daily Report** — about 10–15 minutes. You receive a summary plus the full report (`.md`) and data (`.csv`) in Telegram. **This is the report you asked for.** Its *Data coverage* table lists any symbol TradingView did not recognise.
3. **1 - EMA Scan** — a few minutes. Telegram shows "scanner is live". From now on it runs by itself every 15 minutes.

The very first scan records crossovers already in progress *silently*, so you are not flooded; alerts begin from the next closed candle.

---

## 5. Exact rules used (same as your Pine script)

- **EMA:** `ta.ema(close, n)` — smoothing 2/(n+1), starting from the first candle. Short = 10 (green), Long = 20 (red).
- **Bullish cross (green above red):** EMA10 is above EMA20 now and was at or below it on the previous candle (`ta.crossover`).
- **Bearish cross:** EMA10 below EMA20 now and at or above it previously (`ta.crossunder`).
- **Closed candles only** (like TradingView "Once Per Bar Close"). A candle still forming is never used, so alerts do not appear and then vanish.
- **Candles are rebuilt like TradingView.** 15m, 1h and 1D candles come straight from TradingView; 4h candles are built from 1h and weekly candles from daily ones.
  - 4h anchors on the session open — NSE: 09:15–13:15 and 13:15–15:30; US: 09:30–13:30 and 13:30–16:00; MCX: 09:00, 13:00, 17:00, 21:00 (last candle ends at the close); crypto: 00:00, 04:00, … UTC.
  - 1D = one exchange session; 1W = Monday–Friday for stocks and MCX, Monday–Sunday for crypto.
  - Short final candles (e.g. NSE 15:15–15:30) end at the close. MCX's close is assumed to be 23:55; in winter (23:30 close) the last candle's alert arrives up to 25 minutes later than it could — the safe direction.
- **Data delay is built in.** TradingView's free, logged-out feed is **about 15 minutes behind for NSE, US stocks and MCX; crypto is live.** The scanner adds that delay to its normal waiting time, so it never judges a candle that is still being built inside the delayed feed. In practice an alert for NSE / US / MCX arrives **roughly 20–35 minutes after the candle closes** (daily and weekly: about 35–60 minutes); crypto about 5–20 minutes. Check Setup asks TradingView what delay it applies and tells you if `feed_delay_minutes` needs changing.
- **Each crossover is alerted once.** A catch-up window (last 2–3 candles) covers a late run, and anything older than the limit in `config.yaml` is never sent.
- **Both directions** are alerted (🟢 bullish, 🔴 bearish). Change in `config.yaml` → `alerts: directions`.

### Alert example (illustrative — made-up symbols and prices)
```
🟢 BULLISH CROSS · 1H
EMA 10 (green) crossed above EMA 20 (red)
🕒 02 Nov 14:45 IST

Nifty 500 (2)
• SAMPLEA  ₹1,234.50  (+0.24%)
• SAMPLEB  ₹98.25  (+0.31%)

Crypto (1)
• BTC  $67,000.00  (+0.30%)
```
The percentage is the gap between EMA10 and EMA20. Symbol names are links that open the chart on TradingView.

### Report layout (`reports/latest.md`, also `latest.csv`)
1. **Market breadth** — % of symbols with EMA10 above EMA20, per group and timeframe.
2. **Fresh crossovers** — recent crosses per group and timeframe, newest first.
3. **Data coverage** — symbols the feed could not provide, how many candle sets TradingView and Yahoo each delivered, and codes TradingView did not recognise.
4. **Notes.**
The CSV holds every symbol × timeframe (state, EMAs, last cross, bars since cross) for Excel.

---

## 6. Everyday use (all in `config.yaml` — click the pencil icon on GitHub, then Commit)

| I want to… | Change |
|---|---|
| Use Yahoo only (e.g. TradingView stops working) | `data_source: yahoo` — near-real-time, but MCX is replaced by global futures |
| Use TradingView only, no backup | `data_source: tradingview` |
| Switch a timeframe off everywhere | Delete it from `timeframes:` at the top |
| Fewer alerts for one group | Under that group add `timeframes: ["1h", "4h", "1d", "1w"]` |
| Only bullish (or only bearish) alerts | `directions: bullish` |
| Different EMA lengths | `ema:` → `short` and `long` (also change the Pine script inputs) |
| Add an index or MCX contract | Copy a line under `lists:` and change `symbol`, `name` and `tv:` — open the chart on tradingview.com and copy the `EXCHANGE:CODE` shown at the top left |
| Add a coin | Copy a crypto line and change `symbol` (e.g. `PEPE-USD`) and `name`. If the coin's ticker differs from the symbol, add `base: "TICKER"` |
| Stop alarms about a code you are unsure of | Add `optional: true` to that line |
| Turn a whole group off | `enabled: false` |
| Pause everything | Actions tab → workflow → **⋯ → Disable workflow** |
| Test without sending anything | Run `1 - EMA Scan` with **Dry run** ticked |

A symbol you add later may send one alert if it crossed within the last few candles.

---

## 7. Assumptions I made — please confirm or tell me to change

1. Alert on **both** bullish and bearish crossovers.
2. A **public** GitHub repository is acceptable (needed for free 15-minute runs).
3. 15-minute alerts on **all** ~1,100 symbols, as you specified. This will be chatty and is the heaviest load on TradingView. Easy to narrow later.
4. TradingView is used **without logging in** (free delayed feed), with Yahoo as automatic backup.
5. Commodities are **MCX front-month contracts**; NCDEX is not covered.
6. Times are shown in IST.

---

## 8. Limits and risks (read once)

| Risk | What it means | What to do |
|---|---|---|
| **TradingView's terms** | TradingView has no official data service for programs. The scanner uses the same web connection TradingView's own charts use, without logging in. TradingView's Terms of Use (sections 2, 3 and 10) say its market data is for personal, display-only use, restrict machine-driven processing that does not display the data, and give no promise that its interfaces stay the same. Reading data this way for your own alerts goes against the wording of those terms, so treat it as a grey area. In practice TradingView could block GitHub's servers or change the connection at any time, without notice. Your TradingView account is not involved because the scanner never logs in. | Your decision. It is personal use, paced gently and not redistributed. Kill-switch: `data_source: yahoo`. The official route for a short list is in Section 9. |
| **15-minute delay** | The free feed is about 15 minutes behind for NSE, US and MCX (crypto is live). Paying TradingView for real-time data would **not** help, because the scanner does not log in. | If minutes matter: broker data feed (Section 9). |
| **Blocking or throttling** | With 15m on every symbol the scanner makes roughly 35,000–45,000 chart requests a day. It is paced (25 symbols per connection, short pauses, one retry), stops early if TradingView goes silent, and hands over to Yahoo for everything except MCX. You get a ⚠ message in Telegram (at most once every 6 hours) if fewer than 80% of symbols return data. I cannot promise TradingView will never limit it. | Run Check Setup and the first Daily Report. If many symbols show "no data", send me the *Data coverage* table; fallback is narrowing 15m. |
| **Unofficial connection can change** | If TradingView changes its format, candles stop arriving until the scanner is updated. | Check Setup shows it in plain words; send me the log. Meanwhile set `data_source: yahoo`. |
| **GitHub timing** | Scheduled runs can start late (commonly 5–30 minutes at busy times) and occasionally get skipped. Together with the data delay, NSE / US / MCX alerts are not suited to scalping. | For on-time alerts: Section 9. |
| **60-day rule** | GitHub silently disables scheduled workflows in a public repo with no activity for 60 days. The daily report saves a file each day, which normally counts as activity. | If it ever stops: Actions tab → re-enable the workflow. |
| **Data differences** | Candles are the ones TradingView's charts show, so crosses should match your charts closely. Crypto depends on the exchange used (Binance first); your chart may use another, so edge cases can differ. | Treat alerts as a screen; confirm on the chart. |
| **Exchange holidays** | Handled: before scanning, the scanner checks that a benchmark index actually has the new candle. | None. |
| **Crossovers are mechanical signals** | EMA crossovers lag and whipsaw in sideways markets. | Your judgement and risk rules still apply. |
| **Not live-tested** | Everything except contact with TradingView, Yahoo, NSE, Wikipedia, Telegram and GitHub was tested (Section 11). Specifically unconfirmed: TradingView's exact connection details, that every symbol code exists, how daily candles are time-stamped, that GitHub's servers are allowed in, and the MCX 4h candle boundaries. | *Check Setup* verifies the first four and prints what it finds; the first Daily Report lists every unrecognised code. Send me the log if anything is off. |

---

## 9. Next steps — only if you want them

| Item | What | Needed from you |
|---|---|---|
| Real-time NSE / MCX candles (no 15-minute delay) | Pull from your broker's data API (Fyers is one option). Needs a daily login token; we would agree a safe way to refresh it. | Which broker; an API app. Nothing shared in chat — secrets go into GitHub Secrets. |
| TradingView's own alerts for a short list | Use the Pine alert conditions on ~20 key charts and send them to Telegram through a webhook. This is TradingView's official route and is real-time, but alert counts are limited and webhooks need a paid TradingView plan. | Your plan type |
| On-time scans | Run the same scanner every minute on a small cloud server or always-on PC | Server or PC |
| More indices / coins / NCDEX | Add codes | The list |

### Decisions that are yours (I assumed the first answer in each)
1. Both bullish **and** bearish alerts?
2. Public repository?
3. 15m on everything, or only indices, crypto, commodities and (say) Nifty 500 leaders?
4. Accept unofficial, 15-minute-delayed TradingView data for NSE / US / MCX — or start with `data_source: yahoo` (near-real-time, no MCX) until you have seen the first report?

---

## 10. Troubleshooting

| What you see | Likely cause | Fix |
|---|---|---|
| Check Setup: "Telegram secrets are missing" | Secret name typed differently | Re-add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` exactly as written |
| "chat not found" / "Unauthorized" | Bot not started, wrong chat ID, or wrong token | Press **Start** on the bot, redo Step A.4; for groups add the bot first |
| Nasdaq 100 list `FAIL` (older versions only) | Wikipedia changed its page | Update to version 1.1.1 or later (the log's first line shows the version) |
| Nifty 500 list `FAIL` | NSE blocked the download | Download the file from `https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv` in your browser, upload it to the repo as `universe_cache/nifty500.csv` (Add file → Upload files) |
| `[WARN] TradingView is not working from here - the scanner will use the Yahoo backup` | TradingView refuses GitHub's servers, or changed its connection | Alerts still run on Yahoo (no MCX). Send me the log. Set `data_source: yahoo` to stop trying TradingView |
| `[FAIL] Neither TradingView nor Yahoo returned usable data` | Both sources unreachable from GitHub | Re-run later; if it repeats, send me the log |
| "TradingView does not recognise: …" (Check Setup or report) | A symbol code is wrong or retired | Open the chart on tradingview.com, copy `EXCHANGE:CODE` into that line's `tv:`; add `optional: true` to silence it |
| `[WARN] … data mode '…' = N min delay, but config.yaml assumes M` | The delay differs from my assumption | Copy the line the message suggests into `feed_delay_minutes` |
| Many symbols "no data from the data feed" | Throttling or wrong codes | Wait and re-run; send me the *Data coverage* table |
| ⚠ Telegram message "TradingView feed not reachable … using the Yahoo backup" | TradingView was down or blocked during that run | Yahoo covers everything except MCX; if it persists for a day, tell me |
| No alerts for hours | Market closed, nothing crossed, or runs delayed | Check the Actions tab: recent `1 - EMA Scan` runs should be green. Weekends: only crypto moves |
| Too many alerts | 15m on ~1,100 symbols | Section 6 |
| Run shows a red ✗ | Telegram rejected a message, or setup problem | Open the run → read the last lines; send them to me |

---

## 11. Testing performed (in my workspace, synthetic data)

83 automated tests passed on pandas 3.0.5 (Python 3.13) **and** pandas 2.2.3: 16 on EMA/candle timing, 16 on end-to-end runs, 16 on lists/config/Telegram/report, 4 on the workflow files, and 31 on TradingView (connection, source switching and the setup check).

- EMA and cross maths match the Pine definitions, including equal-value edge cases.
- Candle building: NSE/US/crypto/MCX/futures sessions, 4h anchoring, short final candles, US daylight-saving change, incomplete blocks dropped, weekly Monday–Friday.
- Forming candle never used; exchange-closed periods and weekends handled; a candle still being built inside a 15-minute-delayed feed is never judged.
- **TradingView connection against a simulated TradingView:** message framing, heartbeat replies, candle parsing, every kind of failure reply, timeouts, one retry, symbols per connection and pauses, time budget, "stop early when TradingView goes silent", trying the next exchange for a symbol and remembering the winner, remembering unknown symbols for 2 days, never logging in, and TradingView candles giving the same signals as the equivalent candles for NSE / US / MCX / crypto.
- **Source switching:** `auto`, `tradingview` and `yahoo` modes; Yahoo backup used only for symbols it knows; the outage reported once; unknown or optional codes never counted against data coverage.
- Setup check: source-aware output, delay mismatch warnings, both sources down.
- End-to-end runs: silent first run → one alert → no duplicates; Telegram failure retried without losing the alert; low data coverage pauses and warns; missing secrets fail loudly; dry run sends nothing; report command writes files and sends summary + documents.
- Messages: HTML escaping, splitting under Telegram's length limit, bullish before bearish, rate-limit handling, token never printed.
- Yahoo parsing for both column layouts; throttle detection with back-off; list downloads (NSE, Wikipedia) and cache fallback; config mistakes explained in plain words; workflow files validated.

**Not testable here:** live TradingView, Yahoo, NSE, Wikipedia, Telegram and GitHub runs (my workspace blocks them). *Check Setup* verifies each.

---

## 12. Files

| File | Purpose |
|---|---|
| `EMA_Crossover_Scanner_Guide.md` | This guide |
| `ema_crossover.pine` | Updated Pine script (10 green / 20 red, richer alert text) |
| `config.yaml` | Your settings (data source, groups, lists) |
| `scanner/` | The scanner code: signals, candle timing, TradingView connection (`tv.py`), Yahoo backup and source switching (`data.py`), lists, Telegram, report |
| `.github/workflows/` | The three buttons: `check-setup`, `scan`, `daily-report` |
| `workflow_files_to_paste/` | Copies of the three workflow files (for the paste method in Step B.3) |
| `tests/` | Automated tests |
| `reports/` | Created on first run: `latest.md`, `latest.csv`, `archive/` |

### Pine script changes
Defaults 9/21 → **10/20**; short line colour blue → **green**; long line orange → **red**; alert texts now include exchange, symbol, timeframe and close. In TradingView: Pine Editor → paste → **Add to chart**; for a manual alert choose condition *EMA Cross → Bullish/Bearish EMA Cross*, trigger **Once Per Bar Close**.

---

## 13. Sources

- [TradingView Terms of Use and policies — display-only, personal-use and machine-driven wording (sections 2, 3, 10)](https://in.tradingview.com/policies)
- [python-tradingview-ta FAQ — notes on TradingView data and delays](https://python-tradingview-ta.readthedocs.io/en/stable/faq.html)
- [tvdatafeed-enhanced](https://pypi.org/project/tvdatafeed-enhanced/) and [tradingview-scraper](https://pypi.org/project/tradingview-scraper/) — open-source libraries whose chart-feed connection format the scanner follows (code was read, not copied)
- [GitHub Actions scheduled workflows — interval, delays, 60-day rule, default branch](https://cronuru.com/guides/github-actions-scheduled-workflows)
- [GitHub Docs — Actions billing and usage (public repositories are free)](https://docs.github.com/en/actions/reference/usage-limits-billing-and-administration)
- [yfinance community discussion on Yahoo rate limiting](https://github.com/ranaroussi/yfinance/discussions/2431)

## 14. Change log
- 1.1.1 · 01 Nov 2026 · Nasdaq 100 list: Wikipedia no longer carries the table, so the scanner now tries a second web page and, as a last resort, a copy built into the scanner (found by your first Check Setup run). Tests 83.
- 1.1.0 · 01 Nov 2026 · Prices now come from TradingView's chart feed (Yahoo is the automatic backup; `data_source` setting). Added MCX commodity contracts, 9 more NSE indices, a 15-minute data-delay allowance, symbol fall-backs and bad-code memory, and a TradingView-aware Check Setup. Tests 50 → 82.
- 1.0.0 · 01 Nov 2026 · First release (Yahoo Finance data).
