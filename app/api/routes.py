from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.models.schemas import (
    AccountResponse,
    BotLogSummaryResponse,
    BotStatusResponse,
    ConfigResponse,
    HealthResponse,
    JournalEntry,
    JournalResponse,
    MetricsResponse,
    OrderResponse,
    PositionResponse,
    RunOnceResponse,
    UsageResponse,
)

router = APIRouter()


def _get_bot(request: Request):
    return request.app.state.bot


def _get_settings(request: Request):
    return request.app.state.settings


def _get_trading(request: Request):
    return request.app.state.trading_service


@router.get("/", response_model=UsageResponse)
async def root(request: Request):
    """Return API usage guidance."""
    settings = _get_settings(request)
    return UsageResponse(
        mode=settings.broker_mode,
        trading_enabled=settings.trading_enabled,
    )


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/config", response_model=ConfigResponse)
async def config(request: Request):
    settings = _get_settings(request)
    return ConfigResponse(
        app_env=settings.app_env,
        broker_mode=settings.broker_mode,
        trading_enabled=settings.trading_enabled,
        allow_live_trading=settings.allow_live_trading,
        default_symbols=settings.default_symbols,
        default_timeframe=settings.default_timeframe,
        scan_interval_seconds=settings.scan_interval_seconds,
        order_notional_usd=settings.order_notional_usd,
        position_sizing_mode=settings.position_sizing_mode,
        position_size_percent=settings.position_size_percent,
        max_open_positions=settings.max_open_positions,
        cooldown_seconds_per_symbol=settings.cooldown_seconds_per_symbol,
        post_exit_cooldown_seconds=settings.post_exit_cooldown_seconds,
        max_trades_per_symbol_per_day=settings.max_trades_per_symbol_per_day,
        bar_limit=settings.bar_limit,
        max_daily_orders=settings.max_daily_orders,
        max_daily_loss_usd=settings.max_daily_loss_usd,
        max_position_notional_usd=settings.max_position_notional_usd,
        max_symbol_exposure_usd=settings.max_symbol_exposure_usd,
        max_portfolio_exposure_usd=settings.max_portfolio_exposure_usd,
        require_healthy_account=settings.require_healthy_account,
        paper_trading=settings.paper_trading,
        trade_time_in_force=settings.trade_time_in_force,
        stop_loss_pct=settings.stop_loss_pct,
        take_profit_pct=settings.take_profit_pct,
        stop_loss_mode=settings.stop_loss_mode,
        atr_length=settings.atr_length,
        atr_stop_multiplier=settings.atr_stop_multiplier,
        enable_trailing_stop=settings.enable_trailing_stop,
        strategy_fast_sma=settings.strategy_fast_sma,
        strategy_slow_sma=settings.strategy_slow_sma,
        rsi_length=settings.rsi_length,
        rsi_oversold=settings.rsi_oversold,
        rsi_overbought=settings.rsi_overbought,
        min_volume=settings.min_volume,
        min_volatility_pct=settings.min_volatility_pct,
        higher_timeframe_confirmation=settings.higher_timeframe_confirmation,
        higher_timeframe=settings.higher_timeframe,
    )


@router.get("/account", response_model=AccountResponse)
async def account(request: Request):
    trading = _get_trading(request)
    return AccountResponse(raw=await trading.get_account())


@router.get("/positions", response_model=PositionResponse)
async def positions(request: Request):
    trading = _get_trading(request)
    return PositionResponse(raw=await trading.list_positions())


@router.get("/orders", response_model=OrderResponse)
async def orders(request: Request):
    trading = _get_trading(request)
    return OrderResponse(raw=await trading.list_orders())


@router.post("/run-once", response_model=RunOnceResponse)
async def run_once(request: Request):
    bot = _get_bot(request)
    try:
        result = await bot.run_once()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return RunOnceResponse(**result)


@router.post("/bot/start")
async def bot_start(request: Request):
    bot = _get_bot(request)
    await bot.start()
    return {"status": "started"}


@router.post("/bot/stop")
async def bot_stop(request: Request):
    bot = _get_bot(request)
    await bot.stop()
    return {"status": "stopped"}


@router.post("/bot/halt")
async def bot_halt(request: Request):
    bot = _get_bot(request)
    await bot.halt("manual emergency stop")
    return {"status": "halted", "reason": bot.state.halted_reason}


@router.post("/bot/resume")
async def bot_resume(request: Request):
    bot = _get_bot(request)
    await bot.resume()
    return {
        "status": "resumed",
        "halted_reason": bot.state.halted_reason,
        "risk_stop_latched": bot.state.risk_stop_latched,
    }


@router.post("/bot/reset-risk")
async def bot_reset_risk(request: Request):
    bot = _get_bot(request)
    await bot.reset_risk()
    return {
        "status": "risk reset",
        "halted_reason": bot.state.halted_reason,
        "risk_stop_latched": bot.state.risk_stop_latched,
        "day_peak_equity": bot.state.day_peak_equity,
    }


@router.post("/bot/reconcile-state")
async def bot_reconcile_state(request: Request):
    bot = _get_bot(request)
    summary = await bot.reconcile_broker_state()
    bot.persistence.save_state(bot.state)
    summary = {
        key: value
        for key, value in summary.items()
        if key not in {"account", "positions", "confirmed_orders"}
    }
    return {
        "status": "state reconciled",
        **summary,
    }


@router.get("/bot/status", response_model=BotStatusResponse)
async def bot_status(request: Request):
    bot = _get_bot(request)
    if bot.has_suspicious_state():
        await bot.reconcile_broker_state(trigger="status_suspicion")
        bot.persistence.save_state(bot.state)
    return BotStatusResponse(**bot.status())


@router.get("/bot/log-summary", response_model=BotLogSummaryResponse)
async def bot_log_summary(request: Request):
    bot = _get_bot(request)
    status = bot.status()
    return BotLogSummaryResponse(
        running=status["running"],
        mode=status["mode"],
        halted_reason=status["halted_reason"],
        daily_order_count=status["daily_order_count"],
        daily_equity_drawdown_usd=status["daily_equity_drawdown_usd"],
        last_run_time=status["last_run_time"],
        last_results=status["last_results"],
    )


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(request: Request):
    bot = _get_bot(request)
    metrics = bot.persistence.get_metrics()
    return MetricsResponse(**metrics)


@router.get("/journal", response_model=JournalResponse)
async def journal(request: Request, limit: int = Query(50, ge=1, le=200)):
    bot = _get_bot(request)
    entries = bot.persistence.get_journal(limit)
    return JournalResponse(entries=[JournalEntry(**entry) for entry in entries])


@router.get("/performance", response_model=MetricsResponse)
async def performance(request: Request):
    bot = _get_bot(request)
    metrics = bot.persistence.get_metrics()
    return MetricsResponse(**metrics)
