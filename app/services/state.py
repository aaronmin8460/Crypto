from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class BotState(BaseModel):
    running: bool = False
    mode: str = "paper"
    trading_enabled: bool = False
    last_run_time: datetime | None = None
    last_results: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    cooldowns: dict[str, datetime] = Field(default_factory=dict)
    last_signal_by_symbol: dict[str, str] = Field(default_factory=dict)
    last_order_by_symbol: dict[str, dict[str, Any]] = Field(default_factory=dict)
    daily_order_count: int = 0
    daily_realized_pnl: float = 0.0
    daily_order_date: date | None = None
    last_cash: float | None = None
    halted_reason: str | None = None
    risk_profile: dict[str, float] = Field(default_factory=lambda: {"stop_loss_pct": 0.03, "take_profit_pct": 0.05})

    def can_trade(self, symbol: str) -> bool:
        if self.halted_reason:
            return False
        available_at = self.cooldowns.get(symbol)
        return available_at is None or datetime.now(timezone.utc) >= available_at

    def record_trade(self, symbol: str, cooldown_seconds: int) -> None:
        self.cooldowns[symbol] = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)

    def record_order(self, symbol: str, order: dict[str, Any]) -> None:
        self.last_order_by_symbol[symbol] = order
        self.last_signal_by_symbol[symbol] = order.get("side", "order")
        self.daily_order_count += 1

    def record_signal(self, symbol: str, signal: str) -> None:
        self.last_signal_by_symbol[symbol] = signal

    def record_cash_change(self, cash: Decimal) -> None:
        cash_value = float(cash)
        if self.last_cash is not None:
            self.daily_realized_pnl += cash_value - self.last_cash
        self.last_cash = cash_value

    def reset_daily(self) -> None:
        today = date.today()
        if self.daily_order_date != today:
            self.daily_order_date = today
            self.daily_order_count = 0
            self.daily_realized_pnl = 0.0

    def set_error(self, message: str | None) -> None:
        self.last_error = message

    def halt(self, reason: str) -> None:
        self.halted_reason = reason

    def resume(self) -> None:
        self.halted_reason = None
