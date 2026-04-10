from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.symbols import unique_symbols


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
    persistence_db_path: str = Field("bot_state.db")

    default_symbols: list[str] = Field(default_factory=lambda: ["BTC/USD", "ETH/USD"])
    enable_dynamic_universe: bool = Field(False)
    universe_refresh_seconds: int = Field(3600)
    universe_quote_currency: str = Field("USD")
    universe_excluded_symbols: list[str] = Field(default_factory=list)
    universe_max_symbols: int = Field(0)
    universe_require_tradable: bool = Field(True)
    universe_persist_cache: bool = Field(True)
    default_timeframe: str = Field("1H")
    scan_interval_seconds: int = Field(60)
    max_symbols_per_scan: int = Field(0)
    top_candidates_per_scan: int = Field(10)
    bar_batch_size: int = Field(50)
    bar_batch_max_retries: int = Field(2)

    order_notional_usd: float = Field(100.0)
    position_sizing_mode: Literal["fixed_notional", "percent_equity", "atr"] = Field("fixed_notional")
    position_size_percent: float = Field(0.02)
    max_open_positions: int = Field(2)
    cooldown_seconds_per_symbol: int = Field(900)
    post_exit_cooldown_seconds: int = Field(900)
    max_trades_per_symbol_per_day: int = Field(2)
    bar_limit: int = Field(120)
    max_daily_orders: int = Field(10)
    max_daily_loss_usd: float = Field(150.0)
    max_position_notional_usd: float = Field(250.0)
    max_symbol_exposure_usd: float = Field(300.0)
    max_portfolio_exposure_usd: float = Field(500.0)
    require_healthy_account: bool = Field(True)
    paper_trading: bool = Field(True)
    trade_time_in_force: str = Field("gtc")

    stop_loss_pct: float = Field(0.03)
    take_profit_pct: float = Field(0.05)
    stop_loss_mode: Literal["fixed_pct", "atr"] = Field("fixed_pct")
    atr_length: int = Field(14)
    atr_stop_multiplier: float = Field(2.0)
    enable_trailing_stop: bool = Field(False)

    strategy_fast_sma: int = Field(20)
    strategy_slow_sma: int = Field(50)
    rsi_length: int = Field(14)
    rsi_oversold: float = Field(30.0)
    rsi_overbought: float = Field(70.0)
    min_volume: float = Field(0.0)
    min_average_volume: float = Field(0.0)
    min_volatility_pct: float = Field(0.0)
    min_price: float = Field(0.0)
    exclude_cooldown_symbols_from_prefilter: bool = Field(True)
    exclude_existing_positions_from_prefilter: bool = Field(True)
    exclude_open_order_symbols_from_prefilter: bool = Field(True)
    rank_by_trend_weight: float = Field(0.35)
    rank_by_volume_weight: float = Field(0.20)
    rank_by_volatility_weight: float = Field(0.20)
    rank_by_momentum_weight: float = Field(0.25)
    higher_timeframe_confirmation: bool = Field(False)
    higher_timeframe: str = Field("4H")

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

        self.universe_quote_currency = self.universe_quote_currency.upper()
        self.default_symbols = unique_symbols(
            self.default_symbols,
            quote_currency=self.universe_quote_currency,
        )
        self.universe_excluded_symbols = unique_symbols(
            self.universe_excluded_symbols,
            quote_currency=self.universe_quote_currency,
        )

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
    def paper_mode(self) -> bool:
        return self.broker_mode == "paper"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        populate_by_name=True,
        env_prefix="",
    )
