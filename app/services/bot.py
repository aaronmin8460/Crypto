from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
from app.services.state import BotState
from app.services.strategy import SignalResult, evaluate_signal

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(
        self,
        settings: AppSettings,
        data_service: AlpacaCryptoData,
        trading_service: AlpacaTrading,
    ) -> None:
        self.settings = settings
        self.data_service = data_service
        self.trading_service = trading_service
        self.state = BotState()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def run_once(self) -> dict:
        async with self._lock:
            run_time = datetime.now(timezone.utc)
            self.state.set_error(None)
            results: list[dict] = []

            account = await self.trading_service.get_account()
            positions = await self.trading_service.list_positions()
            position_map = {pos["symbol"]: pos for pos in positions}
            cash = Decimal(str(account.get("cash", "0")))
            open_positions = len(positions)

            for symbol in self.settings.default_symbols:
                symbol_result: dict = {
                    "symbol": symbol,
                    "signal": "HOLD",
                    "reason": "pending",
                    "order": None,
                }

                if not self.state.can_trade(symbol):
                    symbol_result["reason"] = "cooldown active"
                    results.append(symbol_result)
                    continue

                try:
                    bars = await self.data_service.fetch_bars(
                        symbol, self.settings.default_timeframe, self.settings.bar_limit
                    )
                    signal = evaluate_signal(bars)
                except Exception as exc:
                    symbol_result["reason"] = f"signal error: {exc}"
                    results.append(symbol_result)
                    continue

                symbol_result["signal"] = signal.signal
                symbol_result["reason"] = signal.reason

                held_position = position_map.get(symbol)
                if signal.signal == "BUY":
                    if held_position is not None:
                        symbol_result["reason"] = "already holding symbol"
                    elif open_positions >= self.settings.max_open_positions:
                        symbol_result["reason"] = "max open positions reached"
                    elif cash < Decimal(str(self.settings.order_notional_usd)):
                        symbol_result["reason"] = "not enough cash"
                    else:
                        order = await self.trading_service.submit_market_buy_notional(
                            symbol, self.settings.order_notional_usd
                        )
                        symbol_result["order"] = order
                        symbol_result["reason"] = "buy order submitted"
                        self.state.record_trade(symbol, self.settings.cooldown_seconds_per_symbol)

                elif signal.signal == "SELL":
                    if held_position is None:
                        symbol_result["reason"] = "no position to exit"
                    else:
                        qty = float(held_position.get("qty", 0))
                        if qty <= 0:
                            symbol_result["reason"] = "position quantity invalid"
                        else:
                            order = await self.trading_service.submit_market_sell_qty(symbol, qty)
                            symbol_result["order"] = order
                            symbol_result["reason"] = "sell order submitted"
                            self.state.record_trade(symbol, self.settings.cooldown_seconds_per_symbol)

                results.append(symbol_result)

            self.state.last_run_time = run_time
            self.state.last_results = {"symbols": results}
            return {
                "run_time": run_time,
                "results": results,
                "account": account,
                "positions": positions,
            }

    async def _run_loop(self) -> None:
        try:
            while self.state.running and not self._stop_event.is_set():
                try:
                    await self.run_once()
                except Exception as exc:
                    logger.exception("background scan failed")
                    self.state.set_error(str(exc))
                await asyncio.sleep(self.settings.scan_interval_seconds)
        except asyncio.CancelledError:
            logger.info("bot background loop canceled")
        finally:
            self.state.running = False

    async def start(self) -> None:
        if self.state.running:
            return
        self.state.running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("bot started")

    async def stop(self) -> None:
        if not self.state.running:
            return
        self.state.running = False
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("bot stopped")

    def status(self) -> dict:
        return {
            "running": self.state.running,
            "last_run_time": self.state.last_run_time,
            "last_error": self.state.last_error,
            "last_results": self.state.last_results,
            "cooldowns": self.state.cooldowns,
            "risk_profile": self.state.risk_profile,
        }
