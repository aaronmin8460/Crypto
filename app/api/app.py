from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
from app.services.bot import TradingBot
from app.services.persistence import Persistence
from app.utils.logging import configure_logging
from app.api.routes import router

logger = logging.getLogger(__name__)

settings = AppSettings()
configure_logging(settings)
if settings.is_live_mode:
    logger.warning(
        "LIVE trading mode enabled. Live trading will execute real orders only if ALLOW_LIVE_TRADING=true."
    )
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
persistence = Persistence(settings)
bot = TradingBot(settings, crypto_data, trading_service, persistence)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.initialize()
    yield
    await bot.shutdown()


app = FastAPI(title="Alpaca Crypto Paper Trading Bot", lifespan=lifespan)
app.state.settings = settings
app.state.crypto_data = crypto_data
app.state.trading_service = trading_service
app.state.bot = bot
app.state.persistence = persistence
app.include_router(router)
