from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
from app.services.persistence import Persistence
from app.services.state import BotState
from app.services.strategy import StrategyResult, evaluate_signal

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(
        self,
        settings: AppSettings,
        data_service: AlpacaCryptoData,
        trading_service: AlpacaTrading,
        persistence: Persistence | None = None,
    ) -> None:
        self.settings = settings
        self.data_service = data_service
        self.trading_service = trading_service
        self.persistence = persistence or Persistence(settings)
        self.state = BotState(mode=settings.broker_mode, trading_enabled=settings.trading_enabled)
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def initialize(self) -> None:
        persisted = self.persistence.load_state()
        if persisted:
            try:
                self.state = BotState(**persisted)
            except Exception as exc:
                logger.warning("Failed to restore persisted state: %s", exc)
        self.state.running = False
        self.state.mode = self.settings.broker_mode
        self.state.trading_enabled = self.settings.trading_enabled
        await self.reconcile_broker_state()
        self.persistence.save_state(self.state)
        logger.info("bot initialized with persisted state")

    async def shutdown(self) -> None:
        await self.stop()
        self.persistence.save_state(self.state)
        self.persistence.close()
        logger.info("bot persistence closed")

    async def reconcile_broker_state(self) -> None:
        try:
            positions = await self.trading_service.list_positions()
            open_orders = await self.trading_service.list_orders(status="open")

            self.state.open_orders = {
                order.get("symbol", ""): order for order in open_orders if order.get("symbol")
            }
            self.state.total_portfolio_exposure_usd = sum(
                abs(float(pos.get("market_value", 0))) for pos in positions
            )

            for position in positions:
                symbol = position.get("symbol")
                if not symbol:
                    continue
                entry_price = position.get("avg_entry_price") or position.get("current_price")
                try:
                    if entry_price is not None:
                        self.state.position_entry_price[symbol] = float(entry_price)
                except (TypeError, ValueError):
                    continue

            self.persistence.save_positions(positions)
            for order in open_orders:
                symbol = order.get("symbol", "")
                self.persistence.save_order(symbol, order, order.get("side", "order"), "reconciled open order")

            logger.info(
                "reconciled broker state: %d positions, %d open orders",
                len(positions),
                len(open_orders),
            )
        except Exception as exc:
            logger.warning("Broker reconciliation failed: %s", exc)

    async def _fetch_bars_with_retry(self, symbol: str, timeframe: str, limit: int) -> tuple[Any, str]:
        max_retries = 2
        retry_limits = [limit, limit * 2, limit * 3]

        for attempt in range(max_retries + 1):
            try:
                current_limit = retry_limits[attempt] if attempt < len(retry_limits) else limit * 4
                logger.info(
                    "Fetching %s bars: attempt=%d timeframe=%s limit=%d",
                    symbol,
                    attempt + 1,
                    timeframe,
                    current_limit,
                )
                bars = await self.data_service.fetch_bars(symbol, timeframe, current_limit)
                bar_count = len(bars)
                if bar_count < 51:
                    if attempt < max_retries:
                        logger.warning(
                            "Insufficient bars for %s: got %d, need >=51. Retrying...",
                            symbol,
                            bar_count,
                        )
                        continue
                    error_msg = (
                        f"insufficient bars after retries: received {bar_count} "
                        f"(need >=51 for SMA50)"
                    )
                    return None, error_msg
                return bars, ""
            except Exception as exc:
                logger.warning("Bar fetch failed for %s on attempt %d: %s", symbol, attempt + 1, exc)
                if attempt == max_retries:
                    return None, f"bar fetch failed: {exc}"
                await asyncio.sleep(0.5)
        return None, "unknown error in bar fetch"

    def _calculate_order_notional(self, equity: Decimal, bars: Any) -> float:
        if self.settings.position_sizing_mode == "percent_equity":
            return float(equity * Decimal(str(self.settings.position_size_percent)))

        if self.settings.position_sizing_mode == "atr":
            try:
                high = bars["High"].astype(float)
                low = bars["Low"].astype(float)
                close = bars["Close"].astype(float)
                prev_close = close.shift(1)
                tr = pd.concat(
                    [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                    axis=1,
                ).max(axis=1)
                atr = float(tr.rolling(self.settings.atr_length).mean().iloc[-1])
            except Exception:
                atr = None

            close_last = float(bars["Close"].iloc[-1]) if len(bars) else 0.0
            if atr and close_last > 0:
                volatility_factor = max(0.2, min(2.0, close_last / atr))
                notional = float(equity * Decimal(str(self.settings.position_size_percent)) * Decimal(str(volatility_factor)))
                return min(notional, self.settings.max_position_notional_usd)

        return self.settings.order_notional_usd

    async def run_once(self) -> dict[str, Any]:
        async with self._lock:
            run_time = datetime.now(timezone.utc)
            self.state.last_loop_time = run_time
            self.state.set_error(None)
            self.state.mode = self.settings.broker_mode
            self.state.trading_enabled = self.settings.trading_enabled
            self.state.reset_daily()

            account = await self.trading_service.get_account()
            positions = await self.trading_service.list_positions()
            try:
                open_orders = await self.trading_service.list_orders(status="open")
            except Exception as exc:
                logger.warning("Unable to fetch open orders: %s", exc)
                open_orders = []

            open_orders_by_symbol = {
                order.get("symbol", ""): order for order in open_orders if order.get("symbol")
            }
            self.state.open_orders = open_orders_by_symbol
            position_map = {pos["symbol"]: pos for pos in positions}
            self.state.total_portfolio_exposure_usd = sum(
                abs(float(pos.get("market_value", 0))) for pos in positions
            )

            equity = Decimal(str(account.get("equity", account.get("cash", "0"))))
            self.state.record_equity_change(equity)
            logger.info(
                "scan start account equity=%s total_exposure=%.2f open_orders=%d",
                equity,
                self.state.total_portfolio_exposure_usd,
                len(open_orders_by_symbol),
            )

            results: list[dict[str, Any]] = []

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
                response = self._build_response(run_time, results, account, positions)
                self.persistence.save_state(self.state)
                return response

            if self.settings.require_healthy_account and account.get("status") != "ACTIVE":
                self.state.halted_reason = "account not healthy"
                logger.warning("halting bot because account status is not ACTIVE: %s", account.get("status"))
                response = self._build_response(run_time, results, account, positions)
                self.persistence.save_state(self.state)
                return response

            if self.settings.is_live_mode and self.settings.trading_allowed and (
                not self.settings.alpaca_api_key or not self.settings.alpaca_secret_key
            ):
                self.state.halted_reason = "live trading credentials missing"
                logger.warning("halting live trading because API credentials are missing")
                response = self._build_response(run_time, results, account, positions)
                self.persistence.save_state(self.state)
                return response

            for symbol in self.settings.default_symbols:
                symbol_result: dict[str, Any] = {
                    "symbol": symbol,
                    "signal": "HOLD",
                    "reason": "pending",
                    "order": None,
                    "filters": {},
                    "indicators": {},
                    "blocked_by": [],
                }

                if not self.state.can_trade(symbol):
                    symbol_result["reason"] = "cooldown active"
                    symbol_result["blocked_by"].append("cooldown")
                    results.append(symbol_result)
                    continue

                held_position = position_map.get(symbol)
                if symbol in open_orders_by_symbol:
                    symbol_result["reason"] = "open order pending"
                    symbol_result["blocked_by"].append("open_order")
                    results.append(symbol_result)
                    continue

                bars, fetch_error = await self._fetch_bars_with_retry(
                    symbol, self.settings.default_timeframe, self.settings.bar_limit
                )
                if bars is None:
                    symbol_result["reason"] = fetch_error
                    results.append(symbol_result)
                    continue

                higher_bars = None
                if self.settings.higher_timeframe_confirmation:
                    higher_bars, _ = await self._fetch_bars_with_retry(
                        symbol, self.settings.higher_timeframe, self.settings.bar_limit
                    )

                try:
                    signal = evaluate_signal(
                        bars,
                        self.settings,
                        higher_bars=higher_bars,
                    )
                except Exception as exc:
                    symbol_result["reason"] = f"signal error: {exc}"
                    symbol_result["blocked_by"].append("signal_error")
                    results.append(symbol_result)
                    continue

                symbol_result["signal"] = signal.signal
                symbol_result["reason"] = signal.reason
                symbol_result["filters"] = signal.filters
                symbol_result["indicators"] = signal.indicators
                symbol_result["blocked_by"] = signal.blocked_by
                self.state.record_signal(symbol, signal.signal)

                if signal.signal == "BUY":
                    if not self.settings.trading_allowed:
                        symbol_result["reason"] = "trading disabled"
                        symbol_result["blocked_by"].append("trading_disabled")
                    elif held_position is not None:
                        symbol_result["reason"] = "already holding symbol"
                        symbol_result["blocked_by"].append("already_holding")
                    elif self.state.daily_order_count >= self.settings.max_daily_orders:
                        symbol_result["reason"] = "daily order limit reached"
                        symbol_result["blocked_by"].append("daily_order_limit")
                    elif self.state.daily_symbol_trade_count.get(symbol, 0) >= self.settings.max_trades_per_symbol_per_day:
                        symbol_result["reason"] = "symbol trade limit reached"
                        symbol_result["blocked_by"].append("symbol_trade_limit")
                    elif self.state.daily_equity_drawdown_usd >= self.settings.max_daily_loss_usd:
                        self.state.halted_reason = "max daily loss exceeded"
                        symbol_result["reason"] = "halted by max daily loss"
                        symbol_result["blocked_by"].append("drawdown_limit")
                        logger.warning(
                            "max daily loss exceeded: %.2f USD (limit: %.2f)",
                            self.state.daily_equity_drawdown_usd,
                            self.settings.max_daily_loss_usd,
                        )
                    elif self.state.total_portfolio_exposure_usd >= self.settings.max_portfolio_exposure_usd:
                        symbol_result["reason"] = "portfolio exposure limit reached"
                        symbol_result["blocked_by"].append("portfolio_exposure")
                    else:
                        notional = self._calculate_order_notional(equity, bars)
                        if notional <= 0:
                            symbol_result["reason"] = "invalid position sizing"
                            symbol_result["blocked_by"].append("sizing")
                        elif notional > self.settings.max_position_notional_usd:
                            symbol_result["reason"] = "notional exceeds max position notional"
                            symbol_result["blocked_by"].append("max_position_notional")
                        elif self.state.total_portfolio_exposure_usd + notional > self.settings.max_portfolio_exposure_usd:
                            symbol_result["reason"] = "would exceed portfolio exposure limit"
                            symbol_result["blocked_by"].append("portfolio_exposure")
                        else:
                            try:
                                order = await self.trading_service.submit_market_buy_notional(symbol, notional)
                                symbol_result["order"] = order
                                symbol_result["reason"] = "buy order submitted"
                                entry_price = self._extract_filled_price(order, float(bars.iloc[-1]["Close"]))
                                self.state.record_entry_price(symbol, entry_price)
                                self.state.record_trade(symbol, self.settings.cooldown_seconds_per_symbol)
                                self.state.record_order(symbol, order)
                                self.persistence.save_order(symbol, order, "BUY", symbol_result["reason"])
                                self.persistence.save_journal_entry(
                                    symbol=symbol,
                                    action="BUY",
                                    reason=symbol_result["reason"],
                                    entry_price=entry_price,
                                    exit_price=None,
                                    quantity=float(order.get("filled_qty", 0)) or None,
                                    notional=float(order.get("notional", notional)) if order.get("notional") is not None else notional,
                                    realized_pnl=None,
                                    drawdown=self.state.daily_equity_drawdown_usd,
                                    raw=order,
                                )
                                logger.info("submitted buy order for %s at %.2f: %s", symbol, entry_price, order)
                            except Exception as exc:
                                symbol_result["reason"] = f"buy error: {exc}"
                                symbol_result["blocked_by"].append("execution_error")
                                logger.warning("buy error for %s: %s", symbol, exc)

                elif signal.signal == "SELL":
                    if not self.settings.trading_allowed:
                        symbol_result["reason"] = "trading disabled"
                        symbol_result["blocked_by"].append("trading_disabled")
                    elif held_position is None:
                        symbol_result["reason"] = "no position to exit"
                        symbol_result["blocked_by"].append("no_position")
                    else:
                        qty = float(held_position.get("qty", 0))
                        if qty <= 0:
                            symbol_result["reason"] = "position quantity invalid"
                            symbol_result["blocked_by"].append("invalid_qty")
                        else:
                            try:
                                order = await self.trading_service.submit_market_sell_qty(symbol, qty)
                                symbol_result["order"] = order
                                symbol_result["reason"] = "sell order submitted"
                                exit_price = self._extract_filled_price(order, float(held_position.get("current_price", 0)))
                                entry_price = self.state.position_entry_price.get(symbol)
                                realized = None
                                if entry_price is not None:
                                    realized = (exit_price - entry_price) * qty
                                self.state.record_trade(symbol, self.settings.cooldown_seconds_per_symbol)
                                self.state.record_order(symbol, order)
                                self.state.clear_entry_price(symbol)
                                self.persistence.save_order(symbol, order, "SELL", symbol_result["reason"])
                                self.persistence.save_journal_entry(
                                    symbol=symbol,
                                    action="SELL",
                                    reason=symbol_result["reason"],
                                    entry_price=entry_price,
                                    exit_price=exit_price,
                                    quantity=qty,
                                    notional=float(order.get("filled_avg_price", exit_price)) * qty if exit_price else None,
                                    realized_pnl=realized,
                                    drawdown=self.state.daily_equity_drawdown_usd,
                                    raw=order,
                                )
                                logger.info("submitted sell order for %s: %s", symbol, order)
                            except Exception as exc:
                                symbol_result["reason"] = f"sell error: {exc}"
                                symbol_result["blocked_by"].append("execution_error")
                                logger.warning("sell error for %s: %s", symbol, exc)

                else:
                    symbol_result["reason"] = signal.reason

                results.append(symbol_result)

            response = self._build_response(run_time, results, account, positions)
            self.state.consecutive_failures = 0
            self.persistence.save_state(self.state)
            return response

    def _extract_filled_price(self, order: dict[str, Any], fallback: float) -> float:
        try:
            filled_avg = order.get("filled_avg_price")
            if filled_avg is not None and filled_avg != 0:
                return float(filled_avg)
        except (TypeError, ValueError):
            pass
        return fallback

    def _build_response(self, run_time: datetime, results: list[dict[str, Any]], account: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any]:
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
                    self.state.consecutive_failures += 1
                    self.state.set_error(str(exc))
                    logger.exception("background scan failed")
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
        logger.info("bot resumed")

    def status(self) -> dict[str, Any]:
        return {
            "running": self.state.running,
            "mode": self.state.mode,
            "trading_enabled": self.state.trading_enabled,
            "halted_reason": self.state.halted_reason,
            "last_run_time": self.state.last_run_time,
            "last_loop_time": self.state.last_loop_time,
            "last_error": self.state.last_error,
            "consecutive_failures": self.state.consecutive_failures,
            "last_results": self.state.last_results,
            "cooldowns": self.state.cooldowns,
            "open_orders": self.state.open_orders,
            "risk_profile": self.state.risk_profile,
            "daily_order_count": self.state.daily_order_count,
            "daily_equity_drawdown_usd": self.state.daily_equity_drawdown_usd,
            "total_portfolio_exposure_usd": self.state.total_portfolio_exposure_usd,
            "daily_symbol_trade_count": self.state.daily_symbol_trade_count,
            "last_signal_by_symbol": self.state.last_signal_by_symbol,
            "last_order_by_symbol": self.state.last_order_by_symbol,
        }
