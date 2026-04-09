from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field


class BotState(BaseModel):
    running: bool = False
    last_run_time: datetime | None = None
    last_results: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    cooldowns: dict[str, datetime] = Field(default_factory=dict)
    risk_profile: dict[str, float] = Field(default_factory=lambda: {"stop_loss_pct": 0.03, "take_profit_pct": 0.05})

    def can_trade(self, symbol: str) -> bool:
        available_at = self.cooldowns.get(symbol)
        return available_at is None or datetime.now(timezone.utc) >= available_at

    def record_trade(self, symbol: str, cooldown_seconds: int) -> None:
        self.cooldowns[symbol] = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)

    def set_error(self, message: str | None) -> None:
        self.last_error = message
