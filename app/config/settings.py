from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    app_env: str = Field("development", alias="APP_ENV")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    alpaca_api_key: str = Field("", alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field("", alias="ALPACA_SECRET_KEY")
    alpaca_base_url: str = Field("https://paper-api.alpaca.markets", alias="ALPACA_BASE_URL")
    alpaca_data_base_url: str = Field("https://data.alpaca.markets", alias="ALPACA_DATA_BASE_URL")
    default_symbols: list[str] = Field(default_factory=lambda: ["BTC/USD", "ETH/USD"], alias="DEFAULT_SYMBOLS")
    default_timeframe: str = Field("1H", alias="DEFAULT_TIMEFRAME")
    scan_interval_seconds: int = Field(60, alias="SCAN_INTERVAL_SECONDS")
    order_notional_usd: float = Field(100.0, alias="ORDER_NOTIONAL_USD")
    max_open_positions: int = Field(2, alias="MAX_OPEN_POSITIONS")
    cooldown_seconds_per_symbol: int = Field(900, alias="COOLDOWN_SECONDS_PER_SYMBOL")
    bar_limit: int = Field(120, alias="BAR_LIMIT")
    paper_trading: bool = Field(True, alias="PAPER_TRADING")
    trade_time_in_force: str = Field("gtc", alias="TRADE_TIME_IN_FORCE")
    stop_loss_pct: float = Field(0.03, alias="STOP_LOSS_PCT")
    take_profit_pct: float = Field(0.05, alias="TAKE_PROFIT_PCT")

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        populate_by_name=True,
        env_prefix="",
    )
