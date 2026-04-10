# Alpaca Crypto Trading Bot

FastAPI-based Alpaca crypto bot with paper-trading safety, broker-backed reconciliation, SQLite persistence, and a market-wide crypto scanner that can rank the full Alpaca-supported USD crypto universe before trading.

## What it does

- Defaults to **paper trading**
- Requires explicit opt-in for live trading
- Preserves broker truth as the source of truth for orders, positions, cooldowns, and trade counts
- Supports both:
  - `static` mode with `DEFAULT_SYMBOLS`
  - `dynamic` mode with a cached Alpaca crypto universe scanner
- Filters and ranks symbols before full strategy evaluation
- Evaluates only a manageable shortlist each cycle
- Keeps cooldowns, daily limits, max open positions, symbol exposure, and portfolio exposure enforced
- Persists bot state, journal entries, positions, orders, and the cached universe in SQLite

## Scanner modes

### Static mode

- Set `ENABLE_DYNAMIC_UNIVERSE=false`
- The bot uses `DEFAULT_SYMBOLS`
- This keeps the old fixed-symbol behavior for manual/debug workflows

### Dynamic universe mode

- Set `ENABLE_DYNAMIC_UNIVERSE=true`
- The bot discovers active Alpaca crypto assets, normalizes them to `BASE/USD`, caches the universe, and refreshes it periodically
- `DEFAULT_SYMBOLS` are only used as a fallback if universe discovery returns nothing

## How the dynamic universe is built

The universe service:

- pulls Alpaca crypto assets from `/v2/assets`
- keeps only active symbols
- requires tradable assets by default
- restricts to the configured quote currency, `USD` by default
- normalizes symbols to a canonical format such as `BTC/USD`
- removes malformed and duplicate symbols
- applies `UNIVERSE_EXCLUDED_SYMBOLS`
- caches the result in memory and persists the latest snapshot in SQLite

Key settings:

- `ENABLE_DYNAMIC_UNIVERSE`
- `UNIVERSE_REFRESH_SECONDS`
- `UNIVERSE_QUOTE_CURRENCY`
- `UNIVERSE_EXCLUDED_SYMBOLS`
- `UNIVERSE_MAX_SYMBOLS`
- `UNIVERSE_REQUIRE_TRADABLE`
- `UNIVERSE_PERSIST_CACHE`

## Scanner pipeline

Each scan runs in stages:

### Stage A: universe load

- Load all eligible symbols from the cached Alpaca universe

### Stage B: prefilter

Symbols can be rejected before strategy evaluation for:

- insufficient history
- minimum average volume
- minimum volatility
- minimum price
- exclusion list
- cooldown exclusion
- existing-position exclusion
- open-order exclusion

### Stage C: ranking

Remaining symbols are ranked with weighted factors:

- trend strength
- volume
- volatility
- momentum

The momentum/trend score also incorporates RSI quality and distance from moving averages so the shortlist favors cleaner setups instead of just raw volatility.

### Stage D: final candidates

- Only the top `TOP_CANDIDATES_PER_SCAN` symbols go into full strategy evaluation for new entries
- Existing positions and open-order symbols are still carried into final evaluation so exits and broker consistency are not skipped

## Market data behavior

- Uses Alpaca multi-symbol crypto bar requests when possible
- Chunks large universes with `BAR_BATCH_SIZE`
- Retries failed batches with `BAR_BATCH_MAX_RETRIES`
- Handles partial batch responses safely
- Logs batch size, requested symbols, returned symbols, retries, and failures

## Strategy and trade execution

The existing strategy remains in place:

- SMA crossover signal logic
- volume and volatility filters
- RSI filter
- optional higher timeframe confirmation
- fixed notional, percent equity, or ATR-based sizing

The scanner only changes **which symbols reach the strategy**, not the broker reconciliation model.

Per evaluated symbol, the bot now exposes:

- `symbol`
- `rank_score`
- `ranking_reasons`
- `prefilter`
- `filters`
- `indicators`
- `signal`
- `blocked_by`
- `submission_attempted`
- `broker_order_accepted`
- `broker_order_id`
- `broker_order_status`

## Risk and reconciliation

Risk controls remain enforced across the wider universe:

- `MAX_OPEN_POSITIONS`
- `MAX_PORTFOLIO_EXPOSURE_USD`
- `MAX_SYMBOL_EXPOSURE_USD`
- `MAX_TRADES_PER_SYMBOL_PER_DAY`
- per-symbol cooldowns
- daily drawdown stop

When several symbols qualify, the bot processes the best-ranked entry candidates first. Existing positions are evaluated ahead of new entries so sells and risk exits are not starved by a large universe.

Broker truth still wins:

- startup reconciliation rebuilds state from Alpaca
- post-submit reconciliation confirms broker-accepted orders
- stale or fake local orders are discarded
- `/bot/reconcile-state` rebuilds internal state from broker truth on demand

## Status visibility

`GET /bot/status` now includes scanner metadata such as:

- `dynamic_universe_enabled`
- `universe_symbol_count`
- `eligible_symbol_count`
- `filtered_symbol_count`
- `top_candidates`
- `scan_duration_ms`
- `symbols_evaluated_this_run`
- `symbols_skipped_by_prefilter`
- `last_scan_summary`

Use `last_scan_summary.prefilter_results` and `top_candidates` to understand why symbols were shortlisted or skipped.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then update `.env` with your Alpaca credentials and runtime settings.

> If any Alpaca keys were ever committed publicly, rotate them immediately.

## Run the API

```bash
uvicorn main:app --reload --reload-exclude .venv
```

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

## Example `.env` for dynamic market scanning

```env
APP_ENV=development
BROKER_MODE=paper
TRADING_ENABLED=true
ALLOW_LIVE_TRADING=false

ALPACA_API_KEY=your-key
ALPACA_SECRET_KEY=your-secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_BASE_URL=https://data.alpaca.markets

ENABLE_DYNAMIC_UNIVERSE=true
UNIVERSE_REFRESH_SECONDS=3600
UNIVERSE_QUOTE_CURRENCY=USD
UNIVERSE_EXCLUDED_SYMBOLS=["UST/USD"]
UNIVERSE_MAX_SYMBOLS=0
UNIVERSE_REQUIRE_TRADABLE=true
UNIVERSE_PERSIST_CACHE=true

DEFAULT_SYMBOLS=["BTC/USD","ETH/USD"]
SCAN_INTERVAL_SECONDS=60
DEFAULT_TIMEFRAME=1H
BAR_LIMIT=120
BAR_BATCH_SIZE=50
BAR_BATCH_MAX_RETRIES=2

MAX_SYMBOLS_PER_SCAN=0
TOP_CANDIDATES_PER_SCAN=8
MIN_AVERAGE_VOLUME=1000
MIN_VOLATILITY_PCT=0.01
MIN_PRICE=1
EXCLUDE_COOLDOWN_SYMBOLS_FROM_PREFILTER=true
EXCLUDE_EXISTING_POSITIONS_FROM_PREFILTER=true
EXCLUDE_OPEN_ORDER_SYMBOLS_FROM_PREFILTER=true

RANK_BY_TREND_WEIGHT=0.35
RANK_BY_VOLUME_WEIGHT=0.20
RANK_BY_VOLATILITY_WEIGHT=0.20
RANK_BY_MOMENTUM_WEIGHT=0.25

POSITION_SIZING_MODE=fixed_notional
ORDER_NOTIONAL_USD=100
MAX_OPEN_POSITIONS=2
MAX_POSITION_NOTIONAL_USD=250
MAX_SYMBOL_EXPOSURE_USD=300
MAX_PORTFOLIO_EXPOSURE_USD=500
MAX_TRADES_PER_SYMBOL_PER_DAY=2
COOLDOWN_SECONDS_PER_SYMBOL=900
POST_EXIT_COOLDOWN_SECONDS=900
MAX_DAILY_ORDERS=10
MAX_DAILY_LOSS_USD=150

STRATEGY_FAST_SMA=20
STRATEGY_SLOW_SMA=50
RSI_LENGTH=14
RSI_OVERBOUGHT=70
MIN_VOLUME=0
HIGHER_TIMEFRAME_CONFIRMATION=false
HIGHER_TIMEFRAME=4H
```

## Tuning guide

### Tune the universe size

- Lower `UNIVERSE_MAX_SYMBOLS` or `MAX_SYMBOLS_PER_SCAN` for smaller, faster scans
- Raise `TOP_CANDIDATES_PER_SCAN` if the shortlist is too narrow

### Tune prefilters

- Raise `MIN_AVERAGE_VOLUME` to avoid thin symbols
- Raise `MIN_VOLATILITY_PCT` to avoid dead markets
- Raise `MIN_PRICE` if you want to skip very low-priced assets
- Use `UNIVERSE_EXCLUDED_SYMBOLS` for permanent removals

### Tune ranking

- Increase `RANK_BY_TREND_WEIGHT` for stronger trend bias
- Increase `RANK_BY_VOLUME_WEIGHT` for liquidity bias
- Increase `RANK_BY_VOLATILITY_WEIGHT` to favor faster movers
- Increase `RANK_BY_MOMENTUM_WEIGHT` to favor stronger recent acceleration

## Debugging skipped symbols

If a symbol is not traded:

- check `/bot/status`
- inspect `last_scan_summary.prefilter_results`
- inspect `top_candidates`
- inspect `POST /run-once` results for `blocked_by`, `filters`, and `indicators`

Common causes:

- filtered out during prefilter
- not ranked into the top N
- cooldown active
- open order pending
- already holding the symbol
- daily trade limit reached
- exposure or open-position limits reached
- trading disabled or account halted

## Persistence

SQLite state in `bot_state.db` stores:

- bot state
- positions
- orders
- journal entries
- cached universe snapshot

## Tests

Run the full suite with:

```bash
./.venv/bin/python -m pytest -q
```

Current coverage includes:

- universe discovery and normalization
- invalid/non-tradable exclusion
- batched bar fetching and partial responses
- prefilter and ranking behavior
- top-N candidate selection
- static vs dynamic mode switching
- global risk enforcement under many-symbol scans
- duplicate symbol avoidance
- scanner status payloads
- broker reconciliation after dynamic-mode trading
