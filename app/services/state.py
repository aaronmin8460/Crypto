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
    last_loop_time: datetime | None = None
    consecutive_failures: int = 0
    last_results: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    cooldowns: dict[str, datetime] = Field(default_factory=dict)
    open_orders: dict[str, dict[str, Any]] = Field(default_factory=dict)
    last_signal_by_symbol: dict[str, str] = Field(default_factory=dict)
    last_order_by_symbol: dict[str, dict[str, Any]] = Field(default_factory=dict)
    recent_orders: list[dict[str, Any]] = Field(default_factory=list)
    daily_order_count: int = 0
    daily_symbol_trade_count: dict[str, int] = Field(default_factory=dict)
    daily_equity_drawdown_usd: float = 0.0
    total_portfolio_exposure_usd: float = 0.0
    daily_order_date: date | None = None
    last_equity: float | None = None
    halted_reason: str | None = None
    risk_profile: dict[str, float] = Field(default_factory=lambda: {"stop_loss_pct": 0.03, "take_profit_pct": 0.05})
    position_entry_price: dict[str, float] = Field(default_factory=dict)

    def can_trade(self, symbol: str) -> bool:
        if self.halted_reason:
            return False
        available_at = self.cooldowns.get(symbol)
        return available_at is None or datetime.now(timezone.utc) >= available_at

    def can_exit_by_risk(
        self,
        symbol: str,
        current_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        stop_loss_mode: str = "fixed_pct",
        atr_value: float | None = None,
        atr_multiplier: float = 2.0,
    ) -> str | None:
        entry_price = self.position_entry_price.get(symbol)
        if entry_price is None or entry_price <= 0:
            return None
        if current_price <= 0:
            return None

        unrealized_pct = (current_price - entry_price) / entry_price
        if stop_loss_mode == "atr" and atr_value is not None and atr_value > 0:
            if current_price <= entry_price - atr_value * atr_multiplier:
                return "stop_loss"
        elif unrealized_pct <= -stop_loss_pct:
            return "stop_loss"

        if unrealized_pct >= take_profit_pct:
            return "take_profit"

        return None

    def record_trade(self, symbol: str, cooldown_seconds: int) -> None:
        self.cooldowns[symbol] = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
        count = self.daily_symbol_trade_count.get(symbol, 0) + 1
        self.daily_symbol_trade_count[symbol] = count

    def record_order(self, symbol: str, order: dict[str, Any]) -> None:
        self.last_order_by_symbol[symbol] = order
        self.last_signal_by_symbol[symbol] = order.get("side", "order")
        self.daily_order_count += 1
        self.recent_orders.append(order)
        if len(self.recent_orders) > 50:
            self.recent_orders.pop(0)

    def record_entry_price(self, symbol: str, price: float) -> None:
        self.position_entry_price[symbol] = price

    def clear_entry_price(self, symbol: str) -> None:
        self.position_entry_price.pop(symbol, None)

    def record_signal(self, symbol: str, signal: str) -> None:
        self.last_signal_by_symbol[symbol] = signal

    def record_equity_change(self, equity: Decimal) -> None:
        equity_value = float(equity)
        if self.last_equity is not None:
            drawdown = self.last_equity - equity_value
            if drawdown > 0:
                self.daily_equity_drawdown_usd = max(self.daily_equity_drawdown_usd, drawdown)
        else:
            self.last_equity = equity_value
            self.daily_equity_drawdown_usd = 0.0

    def reset_daily(self) -> None:
        today = date.today()
        if self.daily_order_date != today:
            self.daily_order_date = today
            self.daily_order_count = 0
            self.daily_symbol_trade_count.clear()
            self.daily_equity_drawdown_usd = 0.0
            self.last_equity = None
            self.position_entry_price.clear()

    def set_error(self, message: str | None) -> None:
        self.last_error = message

    def halt(self, reason: str) -> None:
        self.halted_reason = reason

    def resume(self) -> None:
        self.halted_reason = None
