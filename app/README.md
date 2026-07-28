# TradingAgents Assistant

A personal finance assistant layered on the TradingAgents multi-agent engine.
It runs your watchlist automatically before each market opens, rotates tickers
to control LLM spend, and alerts you on Telegram/email when a rating changes.

> Research output — **not financial advice**. Verify before acting on any signal.

## The dashboard

`http://127.0.0.1:8000` after starting the server:

- **Action board** — every watchlist ticker grouped into Buy side / Hold /
  Sell side by its latest rating; click a card for the price chart (1M–1Y,
  hover for values) and the full multi-agent analysis report
- **Analysis schedule** — up to 4 daily "slots", each with its own time,
  timezone, market filter, and tickers-per-run; edit and Run-now from the UI
- **Watchlist** — add/pause/remove tickers
- **Run history** — every run with rating changes highlighted

## What it does on schedule

Default slots (all editable in the UI; two enabled out of the box to respect
the Ollama-cloud free-tier quota of roughly 10–12 full runs/week):

| Slot | When (default) | Market | Tickers/run |
|---|---|---|---|
| US pre-market | 07:30 America/Chicago, Mon–Fri | US | 1 |
| India pre-market | 21:30 America/Chicago (≈08:00 IST), Mon–Fri | India | 1 |
| US midday *(disabled)* | 12:00 America/Chicago | US | 1 |
| Crypto evening *(disabled)* | 18:00 America/Chicago, daily | Crypto | 1 |

Each slot analyzes the *stalest* due tickers first, so a budgeted schedule
still rotates through the whole watchlist over the week. A global
`ASSISTANT_DAILY_RUN_BUDGET` (default 4) caps total ticker-runs per day no
matter how slots are configured.

For each ticker it runs the full multi-agent pipeline (analysts → bull/bear
debate → trader → risk team → portfolio manager), then:

- **Telegram alert** if the rating changed (e.g. Hold → Buy), with the decision summary
- **Email digest** per market run with every ticker's rating and report path
- Full markdown reports under `~/.tradingagents/logs/reports/`
- Signal history in SQLite (`~/.tradingagents/assistant.db`)

**Watchlist rotation:** 5 consecutive `Hold` ratings demote a ticker to weekly
check-ins (Mondays); any actionable rating (Buy/Overweight/Underweight/Sell)
promotes it straight back to daily. Nothing is auto-deleted — history and the
engine's self-reflection loop stay intact.

## Setup

```bash
pip install ".[assistant]"
cp .env.example .env     # if you haven't already
```

In `.env`, set at minimum:

1. **LLM** — either `ANTHROPIC_API_KEY=sk-ant-...` (default provider), or for
   free local models:
   ```
   ASSISTANT_LLM_PROVIDER=ollama
   ASSISTANT_DEEP_MODEL=qwen3:32b
   ASSISTANT_QUICK_MODEL=llama3.2
   ```
   (pull the models first: `ollama pull qwen3:32b`; remote server via
   `ASSISTANT_LLM_BACKEND_URL=http://host:11434/v1`)
2. **Telegram** — talk to [@BotFather](https://t.me/BotFather) → `/newbot` →
   copy the token into `TELEGRAM_BOT_TOKEN`. Send your new bot any message,
   then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and
   copy `"chat":{"id":…}` into `TELEGRAM_CHAT_ID`.
3. **Email** — for Gmail: Google Account → Security → 2-Step Verification →
   App passwords; put it in `SMTP_PASSWORD`, your address in `SMTP_USERNAME`,
   `EMAIL_FROM`, and `EMAIL_TO`.

## Run

```bash
uvicorn app.main:app
```

First start seeds an 11-ticker starter watchlist (NVDA, MSFT, AMZN, LLY, JPM,
RELIANCE.NS, HDFCBANK.NS, INFY.NS, BHARTIARTL.NS, BTC-USD, ETH-USD) and
schedules the pre-market jobs. The process must be running for scheduled runs
to fire; a run missed while your machine slept still fires within an hour of
wake-up.

## Manage

Interactive docs at <http://127.0.0.1:8000/docs>, or:

```bash
curl http://127.0.0.1:8000/health                          # scheduler + next run times
curl http://127.0.0.1:8000/watchlist                       # current watchlist
curl -X POST http://127.0.0.1:8000/watchlist -H "Content-Type: application/json" -d '{"symbol": "TSLA"}'
curl -X DELETE http://127.0.0.1:8000/watchlist/TSLA
curl -X POST http://127.0.0.1:8000/watchlist/TSLA/pause    # keep, but stop analyzing
curl -X POST http://127.0.0.1:8000/runs/us                 # trigger a US run right now
curl "http://127.0.0.1:8000/signals?symbol=NVDA&limit=10"  # signal history
```

## Cost control

- Deep/quick models default to Sonnet 4.6 / Haiku 4.5 — roughly $0.30–0.80 per
  ticker-run; the 11-ticker seed costs ~$4–9 per full day across all three
  markets. Ollama drops this to $0.
- Rotation demotes signal-less tickers to 1 run/week automatically.
- Shrink the watchlist or use `pause` for anything you only occasionally care about.

## Roadmap

- **Phase 2** — paper-trading portfolio: signals executed virtually, P&L vs
  SPY/Nifty/BTC, weekly performance report (switch DB to Alembic migrations first)
- **Phase 3** — anomaly screener: strong fundamentals + low social attention →
  auto-added candidates
- **Phase 4** — web dashboard
