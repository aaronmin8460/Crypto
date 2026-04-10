from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pandas as pd
from app.config.settings import AppSettings
from app.services.alpaca_crypto_data import AlpacaCryptoData
from app.services.alpaca_trading import AlpacaTrading
from app.services.persistence import Persistence
from app.services.state import BotState
from app.services.strategy import evaluate_signal

logger = logging.getLogger(__name__)

BROKER_LIST_ORDERS_PATH = "broker.list_orders"
BROKER_SUBMIT_ORDER_PATH = "broker.submit_order"
LOCAL_PENDING_ORDER_PATH = "local.submit_response_pending_reconcile"
OPEN_ORDER_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "held",
    "new",
    "partially_filled",
    "pending_new",
    "pending_replace",
}


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
        self.state.reset_daily()
        await self.reconcile_broker_state(trigger="startup")
        self.persistence.save_state(self.state)
        logger.info("bot initialized with persisted state")

    async def shutdown(self) -> None:
        await self.stop()
        self.persistence.save_state(self.state)
        self.persistence.close()
        logger.info("bot persistence closed")

    async def reconcile_broker_state(self, trigger: str = "manual") -> dict[str, Any]:
        return await self._refresh_broker_state(trigger)

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
                notional = float(
                    equity
                    * Decimal(str(self.settings.position_size_percent))
                    * Decimal(str(volatility_factor))
                )
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

            reconciliation = await self._refresh_broker_state("run_once_preflight")
            if reconciliation.get("account") is not None:
                account = reconciliation["account"]
                positions = reconciliation["positions"]
                open_orders_by_symbol = dict(self.state.open_orders)
            else:
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
                self.state.confirmed_open_orders = len(open_orders_by_symbol)
                self.state.confirmed_positions = len(
                    [position for position in positions if position.get("symbol")]
                )
                self.state.total_portfolio_exposure_usd = sum(
                    abs(float(position.get("market_value", 0))) for position in positions
                )

            position_map = {position["symbol"]: position for position in positions if position.get("symbol")}

            equity = Decimal(str(account.get("equity", account.get("cash", "0"))))
            self.state.record_equity_change(equity)
            if self.state.max_intraday_drawdown_usd >= self.settings.max_daily_loss_usd:
                self.state.risk_stop_latched = True
                self.state.halted_reason = "max daily loss exceeded"
                logger.warning(
                    "daily loss stop latched: current_drawdown=%.2f peak_equity=%.2f limit=%.2f",
                    self.state.current_equity_drawdown_usd,
                    self.state.day_peak_equity or 0.0,
                    self.settings.max_daily_loss_usd,
                )

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
                    "submission_attempted": False,
                    "broker_order_accepted": False,
                    "broker_order_id": None,
                    "broker_order_status": None,
                    "cooldown_applied": False,
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
                    signal = evaluate_signal(bars, self.settings, higher_bars=higher_bars)
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
                    elif (
                        self.state.daily_symbol_trade_count.get(symbol, 0)
                        >= self.settings.max_trades_per_symbol_per_day
                    ):
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
                    elif (
                        self.state.total_portfolio_exposure_usd
                        >= self.settings.max_portfolio_exposure_usd
                    ):
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
                        elif (
                            self.state.total_portfolio_exposure_usd + notional
                            > self.settings.max_portfolio_exposure_usd
                        ):
                            symbol_result["reason"] = "would exceed portfolio exposure limit"
                            symbol_result["blocked_by"].append("portfolio_exposure")
                        else:
                            try:
                                order = await self.trading_service.submit_market_buy_notional(
                                    symbol, notional
                                )
                                symbol_result["submission_attempted"] = True
                                symbol_result["broker_order_id"] = order.get("id")
                                symbol_result["broker_order_status"] = order.get("status")
                                if not self._validate_order_response(
                                    order,
                                    symbol,
                                    "buy",
                                    confirmation_path=BROKER_SUBMIT_ORDER_PATH,
                                ):
                                    symbol_result["reason"] = "broker order validation failed"
                                    symbol_result["blocked_by"].append("broker_validation")
                                    logger.warning(
                                        "broker order validation failed for %s: %s",
                                        symbol,
                                        order,
                                    )
                                else:
                                    confirmed_order, post_trade = await self._confirm_submitted_order(
                                        symbol,
                                        "buy",
                                        order,
                                        trigger=f"post_buy_submit:{symbol}",
                                    )
                                    if post_trade.get("positions") is not None:
                                        positions = post_trade["positions"]
                                        position_map = {
                                            position["symbol"]: position
                                            for position in positions
                                            if position.get("symbol")
                                        }
                                        open_orders_by_symbol = dict(self.state.open_orders)

                                    if confirmed_order is None:
                                        symbol_result["reason"] = "buy submission not confirmed by broker"
                                        symbol_result["blocked_by"].append("broker_reconciliation")
                                        self.state.record_local_order_attempt(
                                            symbol,
                                            self._build_local_order_attempt(order, symbol, "buy"),
                                        )
                                        logger.warning(
                                            "buy submission for %s was not confirmed by broker reconciliation",
                                            symbol,
                                        )
                                    else:
                                        symbol_result["order"] = confirmed_order
                                        symbol_result["reason"] = "buy order confirmed by broker"
                                        symbol_result["broker_order_accepted"] = True
                                        symbol_result["broker_order_id"] = confirmed_order.get("id")
                                        symbol_result["broker_order_status"] = confirmed_order.get("status")
                                        symbol_result["cooldown_applied"] = symbol in self.state.cooldowns
                                        entry_price = (
                                            self.state.position_entry_price.get(symbol)
                                            or self._extract_filled_price(
                                                confirmed_order,
                                                float(bars.iloc[-1]["Close"]),
                                            )
                                        )
                                        quantity = self._safe_float(
                                            confirmed_order.get("filled_qty")
                                        )
                                        notional_value = self._safe_float(
                                            confirmed_order.get("notional")
                                        )
                                        if notional_value is None:
                                            notional_value = notional
                                        self.persistence.save_order(
                                            symbol,
                                            confirmed_order,
                                            "BUY",
                                            symbol_result["reason"],
                                        )
                                        self.persistence.save_journal_entry(
                                            symbol=symbol,
                                            action="BUY",
                                            reason=symbol_result["reason"],
                                            entry_price=entry_price,
                                            exit_price=None,
                                            quantity=quantity,
                                            notional=notional_value,
                                            realized_pnl=None,
                                            drawdown=self.state.daily_equity_drawdown_usd,
                                            raw=confirmed_order,
                                        )
                                        logger.info(
                                            "confirmed buy order for %s at %.2f: %s",
                                            symbol,
                                            entry_price,
                                            confirmed_order,
                                        )
                            except Exception as exc:
                                symbol_result["reason"] = f"buy error: {exc}"
                                symbol_result["blocked_by"].append("execution_error")
                                symbol_result["submission_attempted"] = True
                                symbol_result["broker_order_accepted"] = False
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
                            entry_price_before_exit = self.state.position_entry_price.get(symbol)
                            if entry_price_before_exit is None:
                                entry_price_before_exit = self._safe_float(
                                    held_position.get("avg_entry_price")
                                )
                            try:
                                order = await self.trading_service.submit_market_sell_qty(symbol, qty)
                                symbol_result["submission_attempted"] = True
                                symbol_result["broker_order_id"] = order.get("id")
                                symbol_result["broker_order_status"] = order.get("status")
                                if not self._validate_order_response(
                                    order,
                                    symbol,
                                    "sell",
                                    confirmation_path=BROKER_SUBMIT_ORDER_PATH,
                                ):
                                    symbol_result["reason"] = "broker order validation failed"
                                    symbol_result["blocked_by"].append("broker_validation")
                                    logger.warning(
                                        "broker order validation failed for %s: %s",
                                        symbol,
                                        order,
                                    )
                                else:
                                    confirmed_order, post_trade = await self._confirm_submitted_order(
                                        symbol,
                                        "sell",
                                        order,
                                        trigger=f"post_sell_submit:{symbol}",
                                    )
                                    if post_trade.get("positions") is not None:
                                        positions = post_trade["positions"]
                                        position_map = {
                                            position["symbol"]: position
                                            for position in positions
                                            if position.get("symbol")
                                        }
                                        open_orders_by_symbol = dict(self.state.open_orders)

                                    if confirmed_order is None:
                                        symbol_result["reason"] = "sell submission not confirmed by broker"
                                        symbol_result["blocked_by"].append("broker_reconciliation")
                                        self.state.record_local_order_attempt(
                                            symbol,
                                            self._build_local_order_attempt(order, symbol, "sell"),
                                        )
                                        logger.warning(
                                            "sell submission for %s was not confirmed by broker reconciliation",
                                            symbol,
                                        )
                                    else:
                                        symbol_result["order"] = confirmed_order
                                        symbol_result["reason"] = "sell order confirmed by broker"
                                        symbol_result["broker_order_accepted"] = True
                                        symbol_result["broker_order_id"] = confirmed_order.get("id")
                                        symbol_result["broker_order_status"] = confirmed_order.get("status")
                                        symbol_result["cooldown_applied"] = symbol in self.state.cooldowns
                                        exit_price = self._extract_filled_price(
                                            confirmed_order,
                                            float(held_position.get("current_price", 0)),
                                        )
                                        entry_price = entry_price_before_exit
                                        realized = None
                                        if entry_price is not None:
                                            realized = (exit_price - entry_price) * qty
                                        notional_value = self._safe_float(
                                            confirmed_order.get("notional")
                                        )
                                        if notional_value is None and exit_price:
                                            notional_value = exit_price * qty
                                        self.persistence.save_order(
                                            symbol,
                                            confirmed_order,
                                            "SELL",
                                            symbol_result["reason"],
                                        )
                                        self.persistence.save_journal_entry(
                                            symbol=symbol,
                                            action="SELL",
                                            reason=symbol_result["reason"],
                                            entry_price=entry_price,
                                            exit_price=exit_price,
                                            quantity=qty,
                                            notional=notional_value,
                                            realized_pnl=realized,
                                            drawdown=self.state.daily_equity_drawdown_usd,
                                            raw=confirmed_order,
                                        )
                                        logger.info("confirmed sell order for %s: %s", symbol, confirmed_order)
                            except Exception as exc:
                                symbol_result["reason"] = f"sell error: {exc}"
                                symbol_result["blocked_by"].append("execution_error")
                                symbol_result["submission_attempted"] = True
                                symbol_result["broker_order_accepted"] = False
                                logger.warning("sell error for %s: %s", symbol, exc)

                else:
                    symbol_result["reason"] = signal.reason

                results.append(symbol_result)

            response = self._build_response(run_time, results, account, positions)
            self.state.consecutive_failures = 0
            self.persistence.save_state(self.state)
            return response

    async def _confirm_submitted_order(
        self,
        symbol: str,
        side: str,
        submission_order: dict[str, Any],
        trigger: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        self.state.clear_local_order_attempt(symbol)
        reconciliation = await self._refresh_broker_state(trigger)
        confirmed_order = self.state.last_order_by_symbol.get(symbol)
        if reconciliation.get("error"):
            return None, reconciliation
        if confirmed_order is None:
            return None, reconciliation
        if confirmed_order.get("id") != submission_order.get("id"):
            return None, reconciliation
        if confirmed_order.get("side") != side:
            return None, reconciliation
        self.state.clear_local_order_attempt(symbol)
        return confirmed_order, reconciliation

    async def _fetch_broker_snapshot(self) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        account = await self.trading_service.get_account()
        positions = await self.trading_service.list_positions()
        recent_orders = await self.trading_service.list_orders(status="all", limit=100)
        return account, positions, recent_orders

    async def _refresh_broker_state(self, trigger: str) -> dict[str, Any]:
        try:
            account, positions, recent_orders = await self._fetch_broker_snapshot()
        except Exception as exc:
            logger.warning("Broker reconciliation failed during %s: %s", trigger, exc)
            self.state.broker_state_consistent = False
            return {
                "trigger": trigger,
                "error": str(exc),
                "broker_state_consistent": False,
            }

        summary = self._rebuild_state_from_broker_truth(
            account=account,
            positions=positions,
            recent_orders=recent_orders,
            trigger=trigger,
        )
        logger.info("reconciled broker state: %s", summary)
        return summary

    def _rebuild_state_from_broker_truth(
        self,
        account: dict[str, Any],
        positions: list[dict[str, Any]],
        recent_orders: list[dict[str, Any]],
        trigger: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        current_day = self.state.daily_order_date or date.today()

        confirmed_orders: list[dict[str, Any]] = []
        invalid_broker_orders_ignored = 0
        for order in recent_orders:
            normalized_order = self._normalize_broker_order(
                order,
                confirmation_path=BROKER_LIST_ORDERS_PATH,
            )
            if normalized_order is None:
                invalid_broker_orders_ignored += 1
                continue
            confirmed_orders.append(normalized_order)

        confirmed_orders.sort(key=self._order_sort_key, reverse=True)
        confirmed_positions = [position for position in positions if position.get("symbol")]
        broker_positions = {
            position["symbol"]: position for position in confirmed_positions if position.get("symbol")
        }

        last_order_by_symbol: dict[str, dict[str, Any]] = {}
        open_orders_by_symbol: dict[str, dict[str, Any]] = {}
        for order in confirmed_orders:
            symbol = order["symbol"]
            last_order_by_symbol.setdefault(symbol, order)
            if self._is_open_order(order) and symbol not in open_orders_by_symbol:
                open_orders_by_symbol[symbol] = order

        position_entry_price: dict[str, float] = {}
        for symbol, position in broker_positions.items():
            entry_price = position.get("avg_entry_price") or position.get("current_price")
            parsed_entry_price = self._safe_float(entry_price)
            if parsed_entry_price is not None:
                position_entry_price[symbol] = parsed_entry_price

        daily_order_count, daily_symbol_trade_count = self._compute_daily_counts(
            confirmed_orders,
            current_day,
        )

        cooldowns: dict[str, datetime] = {}
        for order in confirmed_orders:
            submitted_at = self._parse_timestamp(order.get("submitted_at"))
            if submitted_at is None:
                continue
            cooldown_expires_at = submitted_at + timedelta(
                seconds=self._cooldown_seconds_for_order(order)
            )
            if cooldown_expires_at <= now:
                continue
            symbol = order["symbol"]
            existing_expires_at = cooldowns.get(symbol)
            if existing_expires_at is None or cooldown_expires_at > existing_expires_at:
                cooldowns[symbol] = cooldown_expires_at

        previous_last_orders = dict(self.state.last_order_by_symbol)
        previous_cooldowns = dict(self.state.cooldowns)
        previous_entry_prices = dict(self.state.position_entry_price)
        previous_daily_order_count = self.state.daily_order_count
        previous_daily_symbol_trade_count = dict(self.state.daily_symbol_trade_count)
        previous_local_attempts = dict(self.state.local_order_attempts_by_symbol)

        stale_orders_cleared = 0
        untrusted_local_orders_discarded = len(previous_local_attempts)
        for symbol, order in previous_last_orders.items():
            if not self._is_confirmed_order_state(order):
                stale_orders_cleared += 1
                untrusted_local_orders_discarded += 1
                continue
            confirmed_order = last_order_by_symbol.get(symbol)
            if confirmed_order is None or confirmed_order.get("id") != order.get("id"):
                stale_orders_cleared += 1

        cooldowns_cleared = sum(
            1 for symbol in previous_cooldowns if symbol not in cooldowns
        )
        entry_prices_cleared = sum(
            1 for symbol in previous_entry_prices if symbol not in position_entry_price
        )
        trade_counts_recomputed = (
            previous_daily_order_count != daily_order_count
            or previous_daily_symbol_trade_count != daily_symbol_trade_count
        )

        stale_state_detected = any(
            [
                stale_orders_cleared,
                cooldowns_cleared,
                entry_prices_cleared,
                untrusted_local_orders_discarded,
                trade_counts_recomputed,
            ]
        )
        stale_state_cleared_count = (
            stale_orders_cleared
            + cooldowns_cleared
            + entry_prices_cleared
            + untrusted_local_orders_discarded
            + int(trade_counts_recomputed)
        )

        self.state.last_order_by_symbol = last_order_by_symbol
        self.state.local_order_attempts_by_symbol = {}
        self.state.recent_orders = confirmed_orders[:50]
        self.state.open_orders = open_orders_by_symbol
        self.state.cooldowns = cooldowns
        self.state.position_entry_price = position_entry_price
        self.state.total_portfolio_exposure_usd = sum(
            abs(float(position.get("market_value", 0))) for position in confirmed_positions
        )
        self.state.daily_order_count = daily_order_count
        self.state.daily_symbol_trade_count = daily_symbol_trade_count
        self.state.confirmed_open_orders = len(open_orders_by_symbol)
        self.state.confirmed_positions = len(broker_positions)
        self.state.untrusted_local_orders_discarded = untrusted_local_orders_discarded
        self.state.stale_state_detected = stale_state_detected
        self.state.stale_state_cleared_count = stale_state_cleared_count
        self.state.last_reconciled_at = now
        self.state.broker_state_consistent = True

        self.persistence.save_positions(confirmed_positions)
        for order in confirmed_orders:
            self.persistence.save_order(
                order.get("symbol", ""),
                order,
                str(order.get("side", "order")).upper(),
                f"broker reconciliation ({trigger})",
            )

        return {
            "trigger": trigger,
            "stale_orders_cleared": stale_orders_cleared,
            "cooldowns_cleared": cooldowns_cleared,
            "entry_prices_cleared": entry_prices_cleared,
            "trade_counts_recomputed": trade_counts_recomputed,
            "positions_synced": len(confirmed_positions),
            "confirmed_positions": len(broker_positions),
            "open_orders_synced": len(open_orders_by_symbol),
            "confirmed_open_orders": len(open_orders_by_symbol),
            "daily_order_count_recomputed": daily_order_count,
            "daily_symbol_trade_count_recomputed": daily_symbol_trade_count,
            "stale_state_detected": stale_state_detected,
            "stale_state_cleared_count": stale_state_cleared_count,
            "untrusted_local_orders_discarded": untrusted_local_orders_discarded,
            "invalid_broker_orders_ignored": invalid_broker_orders_ignored,
            "broker_state_consistent": True,
            "state_last_reconciled_at": now,
            "account": account,
            "positions": confirmed_positions,
            "confirmed_orders": confirmed_orders,
        }

    def _compute_daily_counts(
        self,
        orders: list[dict[str, Any]],
        current_day: date,
    ) -> tuple[int, dict[str, int]]:
        total = 0
        per_symbol: dict[str, int] = {}
        for order in orders:
            submitted_at = self._parse_timestamp(order.get("submitted_at"))
            if submitted_at is None:
                continue
            if submitted_at.astimezone().date() != current_day:
                continue
            total += 1
            symbol = order.get("symbol")
            if symbol:
                per_symbol[symbol] = per_symbol.get(symbol, 0) + 1
        return total, per_symbol

    def _cooldown_seconds_for_order(self, order: dict[str, Any]) -> int:
        if order.get("side") == "sell":
            return self.settings.post_exit_cooldown_seconds
        return self.settings.cooldown_seconds_per_symbol

    def _is_open_order(self, order: dict[str, Any]) -> bool:
        status = str(order.get("status", "")).lower()
        return status in OPEN_ORDER_STATUSES

    def _order_sort_key(self, order: dict[str, Any]) -> float:
        submitted_at = self._parse_timestamp(order.get("submitted_at"))
        if submitted_at is None:
            return 0.0
        return submitted_at.timestamp()

    def _validate_order_response(
        self,
        order: dict[str, Any],
        expected_symbol: str,
        expected_side: str,
        confirmation_path: str = BROKER_SUBMIT_ORDER_PATH,
    ) -> bool:
        return (
            self._normalize_broker_order(
                order,
                confirmation_path=confirmation_path,
                expected_symbol=expected_symbol,
                expected_side=expected_side,
            )
            is not None
        )

    def _normalize_broker_order(
        self,
        order: dict[str, Any],
        confirmation_path: str,
        expected_symbol: str | None = None,
        expected_side: str | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(order, dict):
            return None

        order_id = order.get("id")
        symbol = order.get("symbol")
        side = order.get("side")
        status = order.get("status")
        submitted_at = self._parse_timestamp(order.get("submitted_at"))
        if not self._looks_like_broker_order_id(order_id):
            return None
        if not isinstance(symbol, str) or not symbol:
            return None
        if side not in {"buy", "sell"}:
            return None
        if not isinstance(status, str) or not status:
            return None
        if submitted_at is None:
            return None
        if expected_symbol is not None and symbol != expected_symbol:
            return None
        if expected_side is not None and side != expected_side:
            return None

        normalized_order = dict(order)
        normalized_order["submitted_at"] = self._format_timestamp(submitted_at)
        normalized_order["source"] = "broker"
        normalized_order["confirmation_path"] = confirmation_path
        normalized_order["broker_confirmed"] = True
        return normalized_order

    def _is_confirmed_order_state(self, order: dict[str, Any]) -> bool:
        if not isinstance(order, dict):
            return False
        if order.get("source") != "broker":
            return False
        if order.get("broker_confirmed") is not True:
            return False
        if order.get("confirmation_path") != BROKER_LIST_ORDERS_PATH:
            return False
        normalized_order = self._normalize_broker_order(
            order,
            confirmation_path=BROKER_LIST_ORDERS_PATH,
            expected_symbol=order.get("symbol"),
            expected_side=order.get("side"),
        )
        return normalized_order is not None

    def _build_local_order_attempt(
        self,
        order: dict[str, Any],
        symbol: str,
        side: str,
    ) -> dict[str, Any]:
        submitted_at = self._parse_timestamp(order.get("submitted_at")) or datetime.now(timezone.utc)
        return {
            "id": order.get("id"),
            "symbol": symbol,
            "side": side,
            "status": order.get("status"),
            "submitted_at": self._format_timestamp(submitted_at),
            "source": "local",
            "confirmation_path": LOCAL_PENDING_ORDER_PATH,
            "broker_confirmed": False,
        }

    def _looks_like_broker_order_id(self, order_id: Any) -> bool:
        if not isinstance(order_id, str) or not order_id.strip():
            return False
        try:
            normalized_uuid = str(UUID(order_id))
        except (TypeError, ValueError):
            return False
        return normalized_uuid == order_id.lower()

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _format_timestamp(self, value: datetime) -> str:
        normalized = value.astimezone(timezone.utc).replace(microsecond=0)
        return normalized.isoformat().replace("+00:00", "Z")

    def _safe_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _extract_filled_price(self, order: dict[str, Any], fallback: float) -> float:
        filled_avg_price = self._safe_float(order.get("filled_avg_price"))
        if filled_avg_price is None or filled_avg_price <= 0:
            return fallback
        return filled_avg_price

    def has_suspicious_state(self) -> bool:
        return self._count_untrusted_runtime_orders() > 0

    def _count_untrusted_runtime_orders(self) -> int:
        count = 0
        for order in self.state.last_order_by_symbol.values():
            if not self._is_confirmed_order_state(order):
                count += 1
        for order in self.state.open_orders.values():
            if not self._is_confirmed_order_state(order):
                count += 1
        return count

    def _build_response(
        self,
        run_time: datetime,
        results: list[dict[str, Any]],
        account: dict[str, Any],
        positions: list[dict[str, Any]],
    ) -> dict[str, Any]:
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
        if self.state.risk_stop_latched and self.state.halted_reason == "max daily loss exceeded":
            logger.warning(
                "resume requested but max daily loss stop remains latched; call /bot/reset-risk to clear"
            )
            return
        self.state.resume()
        logger.info("bot resumed")

    async def reset_risk(self) -> None:
        reconciliation = await self._refresh_broker_state("reset_risk")
        account = reconciliation.get("account")
        if account is None:
            account = await self.trading_service.get_account()

        equity = Decimal(str(account.get("equity", account.get("cash", "0"))))
        self.state.reset_risk_state(float(equity))
        if self.state.halted_reason == "max daily loss exceeded":
            self.state.halted_reason = None
        self.persistence.save_state(self.state)
        logger.info("risk state reset using current equity %.2f", float(equity))

    def status(self) -> dict[str, Any]:
        suspicious_state = self.has_suspicious_state()
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
            "day_peak_equity": self.state.day_peak_equity,
            "current_equity_drawdown_usd": self.state.current_equity_drawdown_usd,
            "max_intraday_drawdown_usd": self.state.max_intraday_drawdown_usd,
            "risk_stop_latched": self.state.risk_stop_latched,
            "total_portfolio_exposure_usd": self.state.total_portfolio_exposure_usd,
            "daily_symbol_trade_count": self.state.daily_symbol_trade_count,
            "last_signal_by_symbol": self.state.last_signal_by_symbol,
            "last_order_by_symbol": self.state.last_order_by_symbol,
            "local_order_attempts_by_symbol": self.state.local_order_attempts_by_symbol,
            "state_last_reconciled_at": self.state.last_reconciled_at,
            "broker_state_consistent": self.state.broker_state_consistent and not suspicious_state,
            "stale_state_detected": self.state.stale_state_detected or suspicious_state,
            "stale_state_cleared_count": self.state.stale_state_cleared_count,
            "confirmed_open_orders": self.state.confirmed_open_orders,
            "confirmed_positions": self.state.confirmed_positions,
            "untrusted_local_orders_discarded": self.state.untrusted_local_orders_discarded,
        }
