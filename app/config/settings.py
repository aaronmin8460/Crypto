from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    app_env: str = Field("development")
    log_level: str = Field("INFO")
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
    paper_trading: bool = Field(True)
    trade_time_in_force: str = "gtc"
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.05

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)
