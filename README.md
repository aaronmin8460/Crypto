# Alpaca Crypto Trading Bot

A Python FastAPI application for Alpaca crypto trading. This repo now includes a more resilient paper trading service with persistence, richer signal reasoning, risk controls, metrics, and journaling.

## What it does

- Loads trading configuration from `.env`
- Defaults to **paper trading** mode
- Requires explicit opt-in for live trading
- Persists bot state in a local SQLite file
- Tracks last run time, cooldowns, entry prices, orders, positions, and drawdown
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
- `POST /bot/resume` clears the halt state

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
curl -X POST http://127.0.0.1:8000/bot/status
curl -X POST http://127.0.0.1:8000/bot/halt
curl -X POST http://127.0.0.1:8000/bot/resume
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
- On restart, the bot reconciles open Alpaca positions and orders to avoid duplicate trades.

## Risk controls

- Daily loss limit and daily order limit enforcement
- Per-symbol trade count limits
- Portfolio and symbol exposure limits
- Optional higher-timeframe confirmation
- RSI-based overbought filtering
- ATR-based sizing and stop functionality

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
- If the bot does not place orders after restart, check the `open_orders` state and broker reconciliation logs.
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
