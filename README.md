# Alpaca Crypto Trading Bot

A Python FastAPI application for Alpaca crypto trading that runs a long-only momentum strategy on `BTC/USD` and `ETH/USD`.

## What it does

- Loads trading configuration from `.env`
- Supports safe default **paper trading** mode
- Adds explicit live trading as an opt-in mode
- Fetches historical crypto bars from Alpaca market data
- Runs SMA20/SMA50 momentum scans on 1H bars
- Places notional market orders when safety checks pass
- Exposes API endpoints for scans, bot control, and status
- Supports a background scan loop with risk controls

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Update `.env` with your own Alpaca credentials. `.env.example` contains placeholder values only and does not include valid keys.

> If any Alpaca keys were ever committed publicly, rotate them immediately.

## Run the API

```bash
uvicorn main:app --reload --reload-exclude .venv
```

If port `8000` is already in use, add `--port 8001`.

## API endpoints

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

## Example curl commands

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config
curl -X POST http://127.0.0.1:8000/run-once
curl -X POST http://127.0.0.1:8000/bot/start
curl http://127.0.0.1:8000/bot/status
curl -X POST http://127.0.0.1:8000/bot/halt
curl -X POST http://127.0.0.1:8000/bot/resume
curl http://127.0.0.1:8000/bot/log-summary
```

## Notes

- Paper trading is the default and live mode is opt-in only.
- Live mode executes real orders and requires both `TRADING_ENABLED=true` and `ALLOW_LIVE_TRADING=true`.
- Paper and live trading use separate Alpaca domains.
- The bot includes order limits, cooldowns, and daily loss protections.
