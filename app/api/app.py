from __future__ import annotations

from fastapi import FastAPI

from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
from app.services.bot import TradingBot
from app.utils.logging import configure_logging
from app.api.routes import router

settings = AppSettings()
configure_logging(settings)
crypto_data = AlpacaCryptoData(settings)
trading_service = AlpacaTrading(settings)
bot = TradingBot(settings, crypto_data, trading_service)

app = FastAPI(title="Alpaca Crypto Paper Trading Bot")
app.state.settings = settings
app.state.crypto_data = crypto_data
app.state.trading_service = trading_service
app.state.bot = bot
app.include_router(router)
