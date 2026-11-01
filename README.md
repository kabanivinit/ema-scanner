# EMA 10/20 Crossover Scanner

Watches Nifty 500, Nasdaq 100, S&P 500, major crypto, Indian indices and commodities for
**EMA 10 (green) / EMA 20 (red) crossovers** on 15m, 1h, 4h, 1D and 1W candles, sends grouped
alerts to Telegram, and builds a daily Markdown/CSV report. It mirrors `ema_crossover.pine`.

**Start here: [`EMA_Crossover_Scanner_Guide.md`](EMA_Crossover_Scanner_Guide.md)** - step-by-step setup for non-coders.

Quick start
1. Create a Telegram bot with @BotFather and note the token and your chat ID.
2. Put these files in a new **public** GitHub repository.
3. Add two secrets: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. Actions tab: run **0 - Check Setup**, then **2 - Daily Report**, then **1 - EMA Scan**.

Settings live in `config.yaml` (timeframes, EMA lengths, which groups to scan).
