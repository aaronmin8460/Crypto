# Alpaca Crypto Paper Trading Bot

A small Python FastAPI application that runs a long-only momentum strategy for Alpaca crypto paper trading using `BTC/USD` and `ETH/USD`.

## What it does

- Connects to Alpaca paper trading using API key and secret from `.env`
- Fetches historical crypto bars from Alpaca crypto market data
- Computes a simple 1H momentum strategy using SMA20 / SMA50
- Places paper market orders by notional amount
- Exposes an API for manual scans and bot control
- Supports a background scan loop

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Update `.env` with your Alpaca paper API credentials.

## Run the API

```bash
uvicorn main:app --reload
```

## API endpoints

- `GET /health`
- `GET /config`
- `GET /account`
- `GET /positions`
- `GET /orders`
- `POST /run-once`
- `POST /bot/start`
- `POST /bot/stop`
- `GET /bot/status`

## Example curl commands

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config
curl -X POST http://127.0.0.1:8000/run-once
curl -X POST http://127.0.0.1:8000/bot/start
curl http://127.0.0.1:8000/bot/status
curl -X POST http://127.0.0.1:8000/bot/stop
```

## Notes

- This project is configured for **paper trading only** by default.
- Crypto is treated as 24/7 tradable.
- Uses Alpaca crypto historical bars endpoint and market orders.
