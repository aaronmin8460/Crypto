# Alpaca Crypto Trading Bot

A Python FastAPI application for Alpaca crypto trading. This repo now includes a more resilient paper trading service with persistence, richer signal reasoning, risk controls, metrics, and journaling.

## What it does

- Loads trading configuration from `.env`
- Defaults to **paper trading** mode
- Requires explicit opt-in for live trading
- Persists bot state in a local SQLite file
- Tracks last run time, cooldowns, entry prices, orders, positions, and drawdown
- Treats broker truth as the source of truth for positions, orders, cooldowns, and daily trade counts
- Reconciles broker state on startup to avoid duplicate orders after restart
- Uses SMA trend logic enhanced with volume, volatility, and RSI filters
- Supports configurable sizing via fixed notional, percent equity, or ATR-adjusted sizing
- Enforces per-symbol and portfolio exposure limits
- Exposes metrics, journal, and performance endpoints

## Key features

- Background bot start/stop with a clean lifecycle
- Last loop heartbeat and consecutive failure tracking
- Persistence via `bot_state.db`
- Broker reconciliation on startup
- Strategy decision payloads with filters and indicator metadata
- Trade journal persisted to SQLite
- Metrics and performance summary endpoints

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Update `.env` with your Alpaca credentials and any additional runtime settings.

> If any Alpaca keys were ever committed publicly, rotate them immediately.

## Run the API

```bash
uvicorn main:app --reload --reload-exclude .venv
```

If port `8000` is already in use, add `--port 8001`.

## Operation modes

- `POST /run-once` runs one trading cycle immediately
- `POST /bot/start` launches the continuous background loop
- `POST /bot/stop` stops the background loop cleanly
- `POST /bot/halt` pauses trading with an emergency reason
- `POST /bot/resume` clears a manual halt state
- `POST /bot/reset-risk` clears a daily loss stop and recomputes risk using current equity
- `POST /bot/reconcile-state` rebuilds internal bot state from broker truth

## Important endpoints

- `GET /health`
- `GET /config`
- `GET /account`
- `GET /positions`
- `GET /orders`
- `POST /run-once`
- `POST /bot/start`
- `POST /bot/stop`
- `POST /bot/halt`
- `POST /bot/resume`
- `POST /bot/reset-risk`
- `POST /bot/reconcile-state`
- `GET /bot/status`
- `GET /bot/log-summary`
- `GET /metrics`
- `GET /journal`
- `GET /performance`

## Example curl commands

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config
curl -X POST http://127.0.0.1:8000/run-once
curl -X POST http://127.0.0.1:8000/bot/start
curl http://127.0.0.1:8000/bot/status
curl -X POST http://127.0.0.1:8000/bot/halt
curl -X POST http://127.0.0.1:8000/bot/resume
curl -X POST http://127.0.0.1:8000/bot/reset-risk
curl -X POST http://127.0.0.1:8000/bot/reconcile-state
curl http://127.0.0.1:8000/metrics
curl http://127.0.0.1:8000/journal
curl http://127.0.0.1:8000/performance
```

## Persistence and storage

- Bot state is stored in `bot_state.db`
- The SQLite persistence layer saves:
  - bot state and run metadata
  - open orders and broker positions
  - recent orders
  - trade journal entries
- On restart, the bot reconciles Alpaca account, recent orders, and positions before using persisted trading state.
- Placeholder or stale local paper-testing state that is not confirmed by the broker is purged during reconciliation.

## Risk controls

- Daily loss stop is calculated from the highest equity observed during the current trading day
- Current drawdown is `max(0, day_peak_equity - current_equity)`
- `max_intraday_drawdown_usd` tracks the worst peak-to-trough drawdown seen today
- `MAX_DAILY_LOSS_USD` is latched for the remainder of the trading day once breached
- `POST /bot/resume` does not clear a true daily loss stop; use `POST /bot/reset-risk` to recover
- `POST /bot/reset-risk` resets drawdown and risk latch state, then refreshes broker-backed state so bogus order memory does not linger
- Per-symbol trade count limits
- Portfolio and symbol exposure limits
- Optional higher-timeframe confirmation
- RSI-based overbought filtering
- ATR-based sizing and stop functionality

## Broker truth and reconciliation

- **Signal generated**: Strategy indicates BUY or SELL
- **Submission attempted**: Order payload sent to Alpaca
- **Broker response validated**: Alpaca returns a realistic order record with `id`, `symbol`, `side`, `status`, and `submitted_at`
- **Broker confirmed**: A follow-up broker reconciliation sees the order in Alpaca `list_orders`
- **Filled**: Order reaches `filled` status (may happen later)
- **Position confirmed**: Broker positions reflect the trade
- Broker truth wins over internal memory. Reconciliation rebuilds:
  - confirmed open orders
  - confirmed positions
  - `last_order_by_symbol`
  - `daily_order_count`
  - `daily_symbol_trade_count`
  - cooldowns
  - entry prices
- Fake or stale local orders such as placeholder IDs like `12345` are discarded automatically.
- Broker state is reconciled at startup, before trading decisions, after validated submission attempts, on `POST /bot/reconcile-state`, and when `/bot/status` detects structurally suspicious local order state.
- Unconfirmed local submit responses are kept separate from broker-confirmed state under `local_order_attempts_by_symbol`.

## `reset-risk` vs `reconcile-state`

- `POST /bot/reset-risk` clears the daily loss latch and re-anchors risk tracking to current equity. It is for recovering from a risk halt.
- `POST /bot/reconcile-state` does not reset risk history. It pulls fresh broker truth and purges stale local order, cooldown, counter, and entry-price state.
- Use `POST /bot/reconcile-state` first when `/bot/status` disagrees with `/orders`, `/positions`, or `/account`.

## Strategy configuration

New configurable settings include:

- `POSITION_SIZING_MODE`
- `POSITION_SIZE_PERCENT`
- `MAX_SYMBOL_EXPOSURE_USD`
- `MAX_PORTFOLIO_EXPOSURE_USD`
- `MAX_TRADES_PER_SYMBOL_PER_DAY`
- `POST_EXIT_COOLDOWN_SECONDS`
- `STOP_LOSS_MODE`
- `ATR_LENGTH`
- `ATR_STOP_MULTIPLIER`
- `ENABLE_TRAILING_STOP`
- `STRATEGY_FAST_SMA`
- `STRATEGY_SLOW_SMA`
- `RSI_LENGTH`
- `RSI_OVERSOLD`
- `RSI_OVERBOUGHT`
- `MIN_VOLUME`
- `MIN_VOLATILITY_PCT`
- `HIGHER_TIMEFRAME_CONFIRMATION`
- `HIGHER_TIMEFRAME`

## Common failure modes and fixes

- If the bot reports `account not healthy`, confirm your Alpaca account status and `REQUIRE_HEALTHY_ACCOUNT` configuration.
- If trading is disabled, verify `TRADING_ENABLED=true` and that paper mode is configured as expected.
- If `/bot/status` shows stale order metadata but `/orders` and `/positions` are empty, call `POST /bot/reconcile-state` and check:
  - `broker_state_consistent`
  - `stale_state_detected`
  - `stale_state_cleared_count`
  - `confirmed_open_orders`
  - `confirmed_positions`
  - `untrusted_local_orders_discarded`
- If the bot does not place orders after restart, check the `open_orders` state and broker reconciliation logs.
- Debug mismatches by comparing `/bot/status`, `/orders`, `/positions`, and `/account`. After a successful reconciliation they should be logically consistent.
- For strategy tuning, inspect the `filters` and `indicators` fields in `POST /run-once` responses.

## Changelog

- Added SQLite persistence for bot state, orders, positions, and journal entries
- Added startup broker reconciliation and restore behavior
- Added continuous `/bot/start` and `/bot/stop` support with clean lifecycle
- Added heartbeat fields and failure tracking to `/bot/status`
- Added `/metrics`, `/journal`, and `/performance` endpoints
- Added richer strategy reasoning with filters, indicators, and blocked reasons
- Added configurable sizing modes and exposure limits
- Added journal persistence and performance metrics
- Added tests for persistence, reconciliation, duplicate order prevention, and new API endpoints
