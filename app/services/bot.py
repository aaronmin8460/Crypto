from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
from app.services.state import BotState
from app.services.strategy import evaluate_signal

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
        self.state = BotState(mode=settings.broker_mode, trading_enabled=settings.trading_enabled)
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def _fetch_bars_with_retry(self, symbol: str, timeframe: str, limit: int) -> tuple[list, str]:
        """Fetch bars with automatic retry if insufficient bars returned.
        
        Returns:
            tuple: (bars_dataframe, error_reason_or_empty_string)
        """
        max_retries = 2
        retry_limits = [limit, limit * 2, limit * 3]  # Increase limit on each retry
        
        for attempt in range(max_retries + 1):
            try:
                current_limit = retry_limits[attempt] if attempt < len(retry_limits) else limit * 4
                logger.info(
                    f"Fetching {symbol} bars: attempt={attempt+1}, timeframe={timeframe}, limit={current_limit}"
                )
                
                bars = await self.data_service.fetch_bars(symbol, timeframe, current_limit)
                
                bar_count = len(bars)
                if bar_count < 51:
                    if attempt < max_retries:
                        logger.warning(
                            f"Insufficient bars for {symbol}: got {bar_count}, need >=51. Retrying with larger limit..."
                        )
                        continue
                    else:
                        error_msg = f"insufficient bars after retries: received {bar_count} (need >=51 for SMA50)"
                        logger.error(error_msg)
                        return None, error_msg
                
                logger.info(f"Successfully fetched {bar_count} bars for {symbol}")
                return bars, ""
                
            except Exception as exc:
                if attempt < max_retries:
                    logger.warning(f"Bar fetch failed for {symbol}: {exc}. Retrying...")
                    await asyncio.sleep(0.5)  # Brief delay before retry
                    continue
                else:
                    error_msg = f"bar fetch failed: {exc}"
                    logger.error(error_msg)
                    return None, error_msg
        
        return None, "unknown error in bar fetch"

    async def run_once(self) -> dict:
        async with self._lock:
            run_time = datetime.now(timezone.utc)
            self.state.set_error(None)
            self.state.mode = self.settings.broker_mode
            self.state.trading_enabled = self.settings.trading_enabled
            self.state.reset_daily()

            account = await self.trading_service.get_account()
            positions = await self.trading_service.list_positions()
            position_map = {pos["symbol"]: pos for pos in positions}
            
            # Track equity for daily drawdown logic (not cash, which changes with buy orders)
            equity = Decimal(str(account.get("equity", account.get("cash", "0"))))
            self.state.record_equity_change(equity)
            
            open_positions = len(positions)
            results: list[dict] = []

            if self.state.halted_reason:
                reason = f"trading halted: {self.state.halted_reason}"
                logger.warning(reason)
                for symbol in self.settings.default_symbols:
                    results.append(
                        {
                            "symbol": symbol,
                            "signal": "HOLD",
                            "reason": reason,
                            "order": None,
                        }
                    )
                return self._build_response(run_time, results, account, positions)

            if self.settings.require_healthy_account and account.get("status") != "ACTIVE":
                self.state.halted_reason = "account not healthy"
                logger.warning("halting bot because account status is not ACTIVE: %s", account.get("status"))
                return self._build_response(run_time, results, account, positions)

            if self.settings.is_live_mode and self.settings.trading_allowed and (
                not self.settings.alpaca_api_key or not self.settings.alpaca_secret_key
            ):
                self.state.halted_reason = "live trading credentials missing"
                logger.warning("halting live trading because API credentials are missing")
                return self._build_response(run_time, results, account, positions)

            for symbol in self.settings.default_symbols:
                symbol_result: dict = {
                    "symbol": symbol,
                    "signal": "HOLD",
                    "reason": "pending",
                    "order": None,
                }

                if not self.state.can_trade(symbol):
                    symbol_result["reason"] = "cooldown active"
                    logger.info("skip %s because cooldown is active", symbol)
                    results.append(symbol_result)
                    continue

                held_position = position_map.get(symbol)
                
                # First, check for risk-based exits on existing positions
                if held_position is not None:
                    current_price = float(held_position.get("current_price", 0))
                    risk_exit_reason = self.state.can_exit_by_risk(
                        symbol,
                        current_price,
                        self.settings.stop_loss_pct,
                        self.settings.take_profit_pct
                    )
                    
                    if risk_exit_reason and self.settings.trading_allowed:
                        # Exit due to stop-loss or take-profit
                        try:
                            qty = float(held_position.get("qty", 0))
                            if qty > 0:
                                order = await self.trading_service.submit_market_sell_qty(symbol, qty)
                                symbol_result["order"] = order
                                symbol_result["signal"] = "SELL"
                                symbol_result["reason"] = f"{risk_exit_reason} exit at {current_price}"
                                self.state.record_trade(symbol, self.settings.cooldown_seconds_per_symbol)
                                self.state.record_order(symbol, order)
                                self.state.clear_entry_price(symbol)
                                logger.info(f"submitted {risk_exit_reason} exit for {symbol} at {current_price}: {order}")
                                results.append(symbol_result)
                                continue
                        except Exception as exc:
                            symbol_result["reason"] = f"exit error: {exc}"
                            logger.warning(f"exit error for {symbol}: {exc}")
                            results.append(symbol_result)
                            continue

                # Fetch bars with retry logic
                bars, fetch_error = await self._fetch_bars_with_retry(
                    symbol, self.settings.default_timeframe, self.settings.bar_limit
                )
                
                if bars is None:
                    symbol_result["reason"] = fetch_error
                    logger.warning(f"bar fetch failed for {symbol}: {fetch_error}")
                    results.append(symbol_result)
                    continue
                
                try:
                    signal = evaluate_signal(bars)
                except Exception as exc:
                    symbol_result["reason"] = f"signal error: {exc}"
                    logger.warning("signal error for %s: %s", symbol, exc)
                    results.append(symbol_result)
                    continue

                symbol_result["signal"] = signal.signal
                symbol_result["reason"] = signal.reason
                self.state.record_signal(symbol, signal.signal)

                if signal.signal == "BUY":
                    if not self.settings.trading_allowed:
                        symbol_result["reason"] = "trading disabled"
                    elif held_position is not None:
                        symbol_result["reason"] = "already holding symbol"
                    elif self.state.daily_order_count >= self.settings.max_daily_orders:
                        symbol_result["reason"] = "daily order limit reached"
                    elif self.state.daily_equity_drawdown_usd >= self.settings.max_daily_loss_usd:
                        self.state.halted_reason = "max daily loss exceeded"
                        symbol_result["reason"] = "halted by max daily loss"
                        logger.warning(
                            "max daily loss exceeded: %.2f USD (limit: %.2f)",
                            self.state.daily_equity_drawdown_usd,
                            self.settings.max_daily_loss_usd
                        )
                    elif open_positions >= self.settings.max_open_positions:
                        symbol_result["reason"] = "max open positions reached"
                    elif self.settings.order_notional_usd > self.settings.max_position_notional_usd:
                        symbol_result["reason"] = "order amount exceeds max position notional"
                    elif equity < Decimal(str(self.settings.order_notional_usd)):
                        symbol_result["reason"] = "not enough cash"
                    else:
                        order = await self.trading_service.submit_market_buy_notional(
                            symbol, self.settings.order_notional_usd
                        )
                        symbol_result["order"] = order
                        symbol_result["reason"] = "buy order submitted"
                        # Record entry price for future stop-loss/take-profit checks
                        entry_price = float(order.get("filled_avg_price", 0)) or float(bars.iloc[-1]["Close"])
                        self.state.record_entry_price(symbol, entry_price)
                        self.state.record_trade(symbol, self.settings.cooldown_seconds_per_symbol)
                        self.state.record_order(symbol, order)
                        logger.info("submitted buy order for %s at %.2f: %s", symbol, entry_price, order)

                elif signal.signal == "SELL":
                    if not self.settings.trading_allowed:
                        symbol_result["reason"] = "trading disabled"
                    elif held_position is None:
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
                            self.state.record_order(symbol, order)
                            self.state.clear_entry_price(symbol)
                            logger.info("submitted sell order for %s: %s", symbol, order)

                else:
                    logger.info("no trade signal for %s: %s", symbol, signal.reason)

                results.append(symbol_result)

            return self._build_response(run_time, results, account, positions)

    def _build_response(self, run_time: datetime, results: list[dict], account: dict, positions: list[dict]) -> dict:
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

    async def halt(self, reason: str = "manual emergency stop") -> None:
        self.state.halt(reason)
        await self.stop()
        logger.warning("bot halted: %s", reason)

    async def resume(self) -> None:
        self.state.resume()
        logger.info("bot resume requested")

    def status(self) -> dict:
        return {
            "running": self.state.running,
            "mode": self.state.mode,
            "trading_enabled": self.state.trading_enabled,
            "halted_reason": self.state.halted_reason,
            "last_run_time": self.state.last_run_time,
            "last_error": self.state.last_error,
            "last_results": self.state.last_results,
            "cooldowns": self.state.cooldowns,
            "risk_profile": self.state.risk_profile,
            "daily_order_count": self.state.daily_order_count,
            "daily_equity_drawdown_usd": self.state.daily_equity_drawdown_usd,
            "last_signal_by_symbol": self.state.last_signal_by_symbol,
            "last_order_by_symbol": self.state.last_order_by_symbol,
        }
