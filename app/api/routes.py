from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (
    AccountResponse,
    BotLogSummaryResponse,
    BotStatusResponse,
    ConfigResponse,
    HealthResponse,
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
        max_open_positions=settings.max_open_positions,
        max_daily_orders=settings.max_daily_orders,
        max_daily_loss_usd=settings.max_daily_loss_usd,
        max_position_notional_usd=settings.max_position_notional_usd,
        cooldown_seconds_per_symbol=settings.cooldown_seconds_per_symbol,
        bar_limit=settings.bar_limit,
        require_healthy_account=settings.require_healthy_account,
        paper_trading=settings.paper_trading,
        trade_time_in_force=settings.trade_time_in_force,
        stop_loss_pct=settings.stop_loss_pct,
        take_profit_pct=settings.take_profit_pct,
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
    return {"status": "resumed"}


@router.get("/bot/status", response_model=BotStatusResponse)
async def bot_status(request: Request):
    bot = _get_bot(request)
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
