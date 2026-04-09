from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    app_env: str = Field("development")
    log_level: str = Field("INFO")
    broker_mode: Literal["paper", "live"] = Field("paper")
    trading_enabled: bool = Field(False)
    allow_live_trading: bool = Field(False)
    alpaca_api_key: str = Field("")
    alpaca_secret_key: str = Field("")
    alpaca_base_url: str = Field("https://paper-api.alpaca.markets")
    alpaca_data_base_url: str = Field("https://data.alpaca.markets")
    default_symbols: list[str] = Field(default_factory=lambda: ["BTC/USD", "ETH/USD"])
    default_timeframe: str = Field("1H")
    scan_interval_seconds: int = Field(60)
    order_notional_usd: float = Field(100.0)
    max_open_positions: int = Field(2)
    cooldown_seconds_per_symbol: int = Field(900)
    bar_limit: int = Field(120)
    max_daily_orders: int = Field(10)
    max_daily_loss_usd: float = Field(150.0)
    max_position_notional_usd: float = Field(250.0)
    require_healthy_account: bool = Field(True)
    paper_trading: bool = Field(True)
    trade_time_in_force: str = Field("gtc")
    stop_loss_pct: float = Field(0.03)
    take_profit_pct: float = Field(0.05)

    @model_validator(mode="after")
    def validate_broker_mode(self):
        self.broker_mode = self.broker_mode.lower()
        if self.broker_mode not in {"paper", "live"}:
            raise ValueError("BROKER_MODE must be paper or live")

        if self.broker_mode == "live":
            if self.alpaca_base_url == "https://paper-api.alpaca.markets":
                self.alpaca_base_url = "https://api.alpaca.markets"
            self.paper_trading = False
        else:
            if self.alpaca_base_url == "https://api.alpaca.markets":
                self.alpaca_base_url = "https://paper-api.alpaca.markets"
            # Don't override paper_trading if broker_mode is paper; let it come from env/kwarg

        return self

    @property
    def is_live_mode(self) -> bool:
        return self.broker_mode == "live"

    @property
    def trading_allowed(self) -> bool:
        if self.is_live_mode:
            return self.trading_enabled and self.allow_live_trading
        return self.trading_enabled

    @property
    def live_mode_warning(self) -> bool:
        return self.is_live_mode and self.allow_live_trading

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        populate_by_name=True,
        env_prefix="",
    )
