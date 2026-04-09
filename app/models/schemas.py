from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"


class ConfigResponse(BaseModel):
    app_env: str
    log_level: str
    default_symbols: list[str]
    default_timeframe: str
    scan_interval_seconds: int
    order_notional_usd: float
    max_open_positions: int
    cooldown_seconds_per_symbol: int
    bar_limit: int
    paper_trading: bool


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
    last_run_time: datetime | None
    last_error: str | None
    last_results: dict[str, Any]
    cooldowns: dict[str, datetime]
    risk_profile: dict[str, float]
