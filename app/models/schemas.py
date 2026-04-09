from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"


class UsageResponse(BaseModel):
    app_name: str = "Alpaca Crypto Trading Bot"
    mode: str  # paper or live
    trading_enabled: bool
    endpoints: dict[str, str] = {
        "GET /": "This help message",
        "GET /health": "Health check",
        "GET /config": "Configuration details",
        "GET /account": "Account info from Alpaca",
        "GET /positions": "Current positions",
        "GET /orders": "Recent orders",
        "POST /run-once": "Run one scan and execute",
        "POST /bot/start": "Start background loop",
        "POST /bot/stop": "Stop background loop",
        "POST /bot/halt": "Emergency halt trading",
        "POST /bot/resume": "Resume after halt",
        "GET /bot/status": "Bot status",
        "GET /bot/log-summary": "Summary of latest run",
        "GET /docs": "Interactive API documentation (Swagger)",
    }
    notices: list[str] = [
        "⚠️  /run-once requires HTTP POST (not GET)",
        "📝 Browser address bar sends GET and will return 405 Method Not Allowed",
        "🔧 Use `curl -X POST http://localhost:8000/run-once` to test",
        "📖 Visit GET /docs for interactive API testing",
        "🛡️  Paper trading is the default; live trading must be explicitly enabled",
        "🚨 See GET /config for all safety limits and settings",
    ]


class ConfigResponse(BaseModel):
    app_env: str
    broker_mode: str
    trading_enabled: bool
    allow_live_trading: bool
    default_symbols: list[str]
    default_timeframe: str
    scan_interval_seconds: int
    order_notional_usd: float
    max_open_positions: int
    max_daily_orders: int
    max_daily_loss_usd: float
    max_position_notional_usd: float
    cooldown_seconds_per_symbol: int
    bar_limit: int
    require_healthy_account: bool
    paper_trading: bool
    trade_time_in_force: str
    stop_loss_pct: float
    take_profit_pct: float


class AccountResponse(BaseModel):
    raw: dict[str, Any]


class PositionResponse(BaseModel):
    raw: list[dict[str, Any]]


class OrderResponse(BaseModel):
    raw: list[dict[str, Any]]


class SymbolResult(BaseModel):
    symbol: str
    signal: str
    reason: str
    order: dict[str, Any] | None = None


class RunOnceResponse(BaseModel):
    run_time: datetime
    results: list[SymbolResult]
    account: dict[str, Any]
    positions: list[dict[str, Any]]


class BotStatusResponse(BaseModel):
    running: bool
    mode: str
    trading_enabled: bool
    halted_reason: str | None
    last_run_time: datetime | None
    last_error: str | None
    last_results: dict[str, Any]
    cooldowns: dict[str, datetime]
    risk_profile: dict[str, float]
    daily_order_count: int
    daily_equity_drawdown_usd: float
    last_signal_by_symbol: dict[str, str]
    last_order_by_symbol: dict[str, dict[str, Any]]


class BotLogSummaryResponse(BaseModel):
    running: bool
    mode: str
    halted_reason: str | None
    daily_order_count: int
    daily_equity_drawdown_usd: float
    last_run_time: datetime | None
    last_results: dict[str, Any]
