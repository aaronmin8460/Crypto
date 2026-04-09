from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
from app.services.bot import TradingBot
from app.utils.logging import configure_logging
from app.api.routes import router

logger = logging.getLogger(__name__)

settings = AppSettings()
configure_logging(settings)
if settings.is_live_mode:
    logger.warning("LIVE trading mode enabled. Live trading will execute real orders only if ALLOW_LIVE_TRADING=true.")
else:
    logger.info("Starting in paper trading mode.")
logger.info(
    "broker_mode=%s, trading_enabled=%s, allow_live_trading=%s",
    settings.broker_mode,
    settings.trading_enabled,
    settings.allow_live_trading,
)

crypto_data = AlpacaCryptoData(settings)
trading_service = AlpacaTrading(settings)
bot = TradingBot(settings, crypto_data, trading_service)

app = FastAPI(title="Alpaca Crypto Paper Trading Bot")
app.state.settings = settings
app.state.crypto_data = crypto_data
app.state.trading_service = trading_service
app.state.bot = bot
app.include_router(router)
