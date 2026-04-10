from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class UsageResponse(BaseModel):
    app_name: str = "Alpaca Crypto Trading Bot"
    mode: str
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
        "POST /bot/reset-risk": "Reset daily risk latch and sync broker state",
        "POST /bot/reconcile-state": "Rebuild bot state from broker truth",
        "GET /bot/status": "Bot status",
        "GET /bot/log-summary": "Summary of latest run",
        "GET /metrics": "Trading metrics and performance summary",
        "GET /journal": "Trade journal entries",
        "GET /performance": "Trading performance summary",
        "GET /docs": "Interactive API documentation (Swagger)",
    }
    notices: list[str] = [
        "⚠️  /run-once requires HTTP POST (not GET)",
        "📝 Browser address bar sends GET and will return 405 Method Not Allowed",
        "🔧 Use `curl -X POST http://127.0.0.1:8000/run-once` to test",
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
    position_sizing_mode: str
    position_size_percent: float
    max_open_positions: int
    cooldown_seconds_per_symbol: int
    post_exit_cooldown_seconds: int
    max_trades_per_symbol_per_day: int
    bar_limit: int
    max_daily_orders: int
    max_daily_loss_usd: float
    max_position_notional_usd: float
    max_symbol_exposure_usd: float
    max_portfolio_exposure_usd: float
    require_healthy_account: bool
    paper_trading: bool
    trade_time_in_force: str
    stop_loss_pct: float
    take_profit_pct: float
    stop_loss_mode: str
    atr_length: int
    atr_stop_multiplier: float
    enable_trailing_stop: bool
    strategy_fast_sma: int
    strategy_slow_sma: int
    rsi_length: int
    rsi_oversold: float
    rsi_overbought: float
    min_volume: float
    min_volatility_pct: float
    higher_timeframe_confirmation: bool
    higher_timeframe: str


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
    filters: dict[str, bool] = Field(default_factory=dict)
    indicators: dict[str, float] = Field(default_factory=dict)
    blocked_by: list[str] = Field(default_factory=list)
    submission_attempted: bool = False
    broker_order_accepted: bool = False
    broker_order_id: str | None = None
    broker_order_status: str | None = None
    cooldown_applied: bool = False


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
    last_loop_time: datetime | None
    last_error: str | None
    consecutive_failures: int
    last_results: dict[str, Any]
    cooldowns: dict[str, datetime]
    open_orders: dict[str, dict[str, Any]]
    risk_profile: dict[str, float]
    daily_order_count: int
    daily_equity_drawdown_usd: float
    day_peak_equity: float | None
    current_equity_drawdown_usd: float
    max_intraday_drawdown_usd: float
    risk_stop_latched: bool
    total_portfolio_exposure_usd: float
    daily_symbol_trade_count: dict[str, int]
    last_signal_by_symbol: dict[str, str]
    last_order_by_symbol: dict[str, dict[str, Any]]
    local_order_attempts_by_symbol: dict[str, dict[str, Any]]
    state_last_reconciled_at: datetime | None
    broker_state_consistent: bool
    stale_state_detected: bool
    stale_state_cleared_count: int
    confirmed_open_orders: int
    confirmed_positions: int
    untrusted_local_orders_discarded: int


class BotLogSummaryResponse(BaseModel):
    running: bool
    mode: str
    halted_reason: str | None
    daily_order_count: int
    daily_equity_drawdown_usd: float
    last_run_time: datetime | None
    last_results: dict[str, Any]


class MetricsResponse(BaseModel):
    total_trades: int
    win_rate: float
    average_gain_loss: float
    cumulative_realized_pnl: float


class JournalEntry(BaseModel):
    id: int
    timestamp: datetime
    symbol: str
    action: str
    reason: str
    entry_price: float | None = None
    exit_price: float | None = None
    quantity: float | None = None
    notional: float | None = None
    realized_pnl: float | None = None
    drawdown: float | None = None
    raw: dict[str, Any]


class JournalResponse(BaseModel):
    entries: list[JournalEntry]
