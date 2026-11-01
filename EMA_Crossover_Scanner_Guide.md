# EMA 10/20 Crossover Scanner — Guide & Work Log

Prepared 01 Nov 2026 · version 1.0.0 · for a non-coder (no programming needed after the one-time setup)

---

## 0. Status at a glance

| Item | Status |
|---|---|
| Pine script updated to EMA **10 (green)** / **20 (red)** | Done — `ema_crossover.pine` |
| Scanner that applies the same logic to ~1,100 symbols on 15m / 1h / 4h / 1D / 1W | Built and tested offline (50 automated tests passing) |
| Telegram alerts (grouped, de-duplicated, clickable TradingView links) | Built and tested with a simulated Telegram |
| Daily report (Markdown + CSV, also sent to Telegram) | Built and tested |
| **Live report with real prices** | **Not produced yet.** My workspace is blocked from Yahoo Finance and Telegram, so I could not pull live data. Your first GitHub run (Section 4) produces it in about 10 minutes. |
| MCX commodities (real MCX prices) and *all* NSE/BSE indices | **Phase 2** — needs a broker data feed (Section 9). Phase 1 uses clearly labelled stand-ins. |

---

## 1. What you asked for → what was built

| You asked | Delivered |
|---|---|
| Run the Pine script logic on 6 groups of securities and report | Python scanner replicating the script exactly (Section 5), plus a daily report |
| Alert on Telegram when Short (10, green) crosses Long (20, red) on 15m, 1h, 4h, 1D, 1W | `1 - EMA Scan` runs every 15 minutes, sends one grouped message per timeframe/direction |
| Markdown file of all work | This file |
| Simple for a non-coder | All settings in one file (`config.yaml`); setup is clicks only; a "Check Setup" button tells you what is wrong in plain words |

---

## 2. How it works (one minute)

```
GitHub (free, runs by itself every 15 min)
   │  downloads candles from Yahoo Finance
   │  rebuilds 15m / 1h / 4h / 1D / 1W candles exactly like TradingView
   │  computes EMA10 & EMA20, finds crossovers on CLOSED candles only
   │  skips anything already alerted (remembers in a small saved file)
   ▼
Telegram message ──► you
Daily 17:10 IST ──► report (.md + .csv) in Telegram and in your GitHub repo
```

TradingView alone cannot do this: one alert covers one symbol on one timeframe, so 1,100 symbols × 5 timeframes is far beyond what alerts allow. That is why the scanner exists. You can still use the Pine script on charts for visual checking.

---

## 3. What is scanned

| # | Your group | What the scanner uses | Count | Source of list | Data |
|---|---|---|---|---|---|
| 1 | Nifty 500 | All constituents | ~500 | Downloaded from NSE, refreshed weekly | Yahoo (`.NS`) |
| 2 | Nasdaq 100 | All constituents | ~100 | Downloaded from Wikipedia, weekly | Yahoo |
| 3 | Crypto | Curated list of major coins vs USD | 48 | `config.yaml` (editable) | Yahoo |
| 4 | Commodities (Indian exchanges) | **Global futures stand-ins**: Gold, Silver, Copper, Crude WTI, Brent, Natural Gas, Aluminium, Platinum, Cotton, Soybeans. Links open the MCX chart. | 10 | `config.yaml` | Yahoo |
| 5 | S&P 500 | All constituents | ~500 | Downloaded from Wikipedia, weekly | Yahoo |
| 6 | Indian indices | Nifty 50, Bank, Next 50, Midcap 50, Nifty 500, Sensex and 15 sector indices | 21 | `config.yaml` | Yahoo |

Overlaps (e.g. Apple is in S&P 500 and Nasdaq 100) are fetched once and alerted once, labelled with both groups.

**Honest gaps (Phase 1):**
- **Commodities are not MCX prices.** Yahoo has no MCX data. COMEX/NYMEX/ICE futures cross at nearly the same times as MCX contracts, but not identically (USD vs INR, different contract months). Alerts say "Commodities (global proxy for MCX)".
- **Indian indices: 21, not all ~130.** Yahoo only carries a subset. A few ticker codes may return no data on the first run; the report's *Data coverage* table names them.
- **Crypto: 48 major coins, not "all".** Add more by copying a line in `config.yaml`. A few coin codes on Yahoo have odd suffixes and may need correcting after the first run.

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

### Step D — Switch it on (run in this order)
Open the **Actions** tab (click the green button if GitHub asks to enable workflows), choose a workflow on the left, click **Run workflow**.

1. **0 - Check Setup (run this first)** — about 3 minutes. The log shows `[PASS]`, `[WARN]` or `[FAIL]` lines in plain words, and Telegram shows a "Setup check" message. Fix any `[FAIL]` using Section 10, or send me the log text.
2. **2 - Daily Report** — about 10–15 minutes. You receive a summary plus the full report (`.md`) and data (`.csv`) in Telegram. **This is the report you asked for.**
3. **1 - EMA Scan** — a minute or two. Telegram shows "scanner is live". From now on it runs by itself every 15 minutes.

The very first scan records crossovers already in progress *silently*, so you are not flooded; alerts begin from the next closed candle.

---

## 5. Exact rules used (same as your Pine script)

- **EMA:** `ta.ema(close, n)` — smoothing 2/(n+1), starting from the first candle. Short = 10 (green), Long = 20 (red).
- **Bullish cross (green above red):** EMA10 is above EMA20 now and was at or below it on the previous candle (`ta.crossover`).
- **Bearish cross:** EMA10 below EMA20 now and at or above it previously (`ta.crossunder`).
- **Closed candles only** (like TradingView "Once Per Bar Close"). A candle still forming is never used, so alerts do not appear and then vanish.
- **Candles are rebuilt like TradingView:**
  - 4h anchors on the session open — NSE: 09:15–13:15 and 13:15–15:30; US: 09:30–13:30 and 13:30–16:00; crypto: 00:00, 04:00, … UTC; commodity stand-ins: 18:00 New York.
  - 1D = one exchange session; 1W = Monday–Friday for stocks, Monday–Sunday for crypto.
  - Short final candles (e.g. NSE 15:15–15:30) end at the close.
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
3. **Data coverage** — symbols Yahoo could not provide.
4. **Notes.**
The CSV holds every symbol × timeframe (state, EMAs, last cross, bars since cross) for Excel.

---

## 6. Everyday use (all in `config.yaml` — click the pencil icon on GitHub, then Commit)

| I want to… | Change |
|---|---|
| Switch a timeframe off everywhere | Delete it from `timeframes:` at the top |
| Fewer alerts for one group | Under that group add `timeframes: ["1h", "4h", "1d", "1w"]` |
| Only bullish (or only bearish) alerts | `directions: bullish` |
| Different EMA lengths | `ema:` → `short` and `long` (also change the Pine script inputs) |
| Add a coin / index | Copy a line under `lists:` and change `symbol` and `name` (Yahoo code) |
| Turn a whole group off | `enabled: false` |
| Pause everything | Actions tab → workflow → **⋯ → Disable workflow** |
| Test without sending anything | Run `1 - EMA Scan` with **Dry run** ticked |

---

## 7. Assumptions I made — please confirm

1. Alert on **both** bullish and bearish crossovers.
2. A **public** GitHub repository is acceptable (needed for free 15-minute runs).
3. 15-minute alerts on **all** ~1,100 symbols, as you specified. This will be chatty (expect many messages on busy days) and puts the most load on Yahoo. Easy to narrow later.
4. Commodities use global stand-ins until Phase 2.
5. Times are shown in IST.

---

## 8. Limits and risks (read once)

| Risk | What it means | What to do |
|---|---|---|
| **Yahoo may throttle** | Yahoo is an unofficial, free source; users report temporary "Too Many Requests" blocks, especially from cloud servers. My estimate for this setup is ~40,000 requests per day. The scanner paces itself, backs off, retries, and warns you on Telegram when coverage drops, but I cannot promise it will never be throttled. | Run Check Setup and the first Daily Report; if many symbols show "no data", tell me — fallback is narrowing 15m or moving to a broker feed (Phase 2). |
| **GitHub timing** | Scheduled runs can start late (commonly 5–30 minutes at busy times) and occasionally get skipped. A "15m" alert typically arrives 3–15 minutes after the candle closes. Not suited to scalping. | For on-time alerts: Phase 2 cloud server or always-on PC. |
| **60-day rule** | GitHub silently disables scheduled workflows in a public repo with no activity for 60 days. The daily report saves a file each day, which normally counts as activity. | If it ever stops: Actions tab → re-enable the workflow. |
| **Data differences** | Yahoo's prices can differ slightly from TradingView's feed; a cross sitting exactly on the edge can differ. History is limited (15m ≈ 30 days) but plenty for a 20-period EMA. | Treat alerts as a screen; confirm on the chart. |
| **Exchange holidays** | Handled: before scanning, the scanner checks that a benchmark index actually has the new candle. | None. |
| **Crossovers are mechanical signals** | EMA crossovers lag and whipsaw in sideways markets. | Your judgement and risk rules still apply. |
| **Not live-tested** | Everything except contact with Yahoo, NSE, Wikipedia, Telegram and GitHub was tested (Section 11). Those connections are verified by *Check Setup*. | Run Check Setup first. |

---

## 9. Phase 2 — proposed next steps (needs your decision)

| Item | What | Needed from you |
|---|---|---|
| MCX real prices + all NSE/BSE indices | Pull from your broker's data API (Fyers is the natural choice, since you trade there). Needs a daily login token; we would agree a safe way to refresh it. | Confirm Fyers; create an API app; share nothing in chat — secrets go into GitHub Secrets. |
| Crypto exchange-grade candles | Delta Exchange public candle data, wider coin list | Nothing |
| On-time 15m alerts | Run the same scanner every minute on a small cloud server or always-on PC | Choose server vs PC |
| TradingView alerts for a few key charts | Use the Pine alert conditions (webhook needs a paid TradingView plan) | Your plan type |

### Questions for you (one line each is enough)
1. Both bullish **and** bearish alerts OK?
2. Public repository OK?
3. Keep 15m on everything, or only indices, crypto, commodities and (say) Nifty 500 leaders?
4. Go ahead with Phase 2 via Fyers for MCX and full index coverage?

---

## 10. Troubleshooting

| What you see | Likely cause | Fix |
|---|---|---|
| Check Setup: "Telegram secrets are missing" | Secret name typed differently | Re-add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` exactly as written |
| "chat not found" / "Unauthorized" | Bot not started, wrong chat ID, or wrong token | Press **Start** on the bot, redo Step A.4; for groups add the bot first |
| Nifty 500 list `FAIL` | NSE blocked the download | Download the file from `https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv` in your browser, upload it to the repo as `universe_cache/nifty500.csv` (Add file → Upload files) |
| Many symbols "no data from Yahoo" | Yahoo throttling or a wrong ticker | Wait and re-run; a few wrong coin/index codes are normal — send me the *Data coverage* table |
| No alerts for hours | Market closed, nothing crossed, or runs delayed | Check the Actions tab: recent `1 - EMA Scan` runs should be green. Weekends: only crypto and commodities move |
| Too many alerts | 15m on ~1,100 symbols | Section 6 |
| Run shows a red ✗ | Telegram rejected a message, or setup problem | Open the run → read the last lines; send them to me |

---

## 11. Testing performed (in my workspace, synthetic data)

50 automated tests passed on pandas 3.0.5 (Python 3.13) **and** pandas 2.2.3.

- EMA and cross maths match the Pine definitions, including equal-value edge cases.
- Candle building: NSE/US/crypto/commodity sessions, 4h anchoring, short final candles, US daylight-saving change, incomplete blocks dropped, weekly Monday–Friday.
- Forming candle never used; exchange-closed periods and weekends handled.
- End-to-end runs: silent first run → one alert → no duplicates; Telegram failure retried without losing the alert; low Yahoo coverage pauses and warns; missing secrets fail loudly; dry run sends nothing; report command writes files and sends summary + documents.
- Messages: HTML escaping, splitting under Telegram's length limit, bullish before bearish, rate-limit handling, token never printed.
- Yahoo parsing for both column layouts; throttle detection with back-off; list downloads (NSE, Wikipedia) and cache fallback; config mistakes explained in plain words; workflow files validated.

**Not testable here:** live Yahoo, NSE, Wikipedia, Telegram and GitHub runs (my workspace blocks them). *Check Setup* verifies each.

---

## 12. Files

| File | Purpose |
|---|---|
| `EMA_Crossover_Scanner_Guide.md` | This guide |
| `ema_crossover.pine` | Updated Pine script (10 green / 20 red, richer alert text) |
| `config.yaml` | Your settings |
| `scanner/` | The scanner code (signals, candle timing, Yahoo data, lists, Telegram, report) |
| `.github/workflows/` | The three buttons: `check-setup`, `scan`, `daily-report` |
| `workflow_files_to_paste/` | Copies of the three workflow files (for the paste method in Step B.3) |
| `tests/` | Automated tests |
| `reports/` | Created on first run: `latest.md`, `latest.csv`, `archive/` |

### Pine script changes
Defaults 9/21 → **10/20**; short line colour blue → **green**; long line orange → **red**; alert texts now include exchange, symbol, timeframe and close. In TradingView: Pine Editor → paste → **Add to chart**; for a manual alert choose condition *EMA Cross → Bullish/Bearish EMA Cross*, trigger **Once Per Bar Close**.

---

## 13. Sources

- [GitHub Actions scheduled workflows — interval, delays, 60-day rule, default branch](https://cronuru.com/guides/github-actions-scheduled-workflows)
- [GitHub Docs — Actions billing and usage (public repositories are free)](https://docs.github.com/en/actions/reference/usage-limits-billing-and-administration)
- [yfinance community discussion on Yahoo rate limiting](https://github.com/ranaroussi/yfinance/discussions/2431)

## 14. Change log
- 1.0.0 · 01 Nov 2026 · First release.
