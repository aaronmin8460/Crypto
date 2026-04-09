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

Update `.env` with your own Alpaca paper API credentials. `.env.example` contains placeholder values only and does not include working keys.

> If any Alpaca keys were ever committed publicly, rotate them immediately and replace them with new paper credentials.

## Run the API

```bash
uvicorn main:app --reload
```

If port `8000` is already in use, change the port with `--port 8001`. If your virtualenv is inside the project directory, reload can be noisy when the `.venv` folder changes. For cleaner reload behavior, keep the venv outside the project or use `uvicorn main:app --reload --reload-dir app --reload-dir main.py`.

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
