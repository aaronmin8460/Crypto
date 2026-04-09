from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (
    AccountResponse,
    BotStatusResponse,
    ConfigResponse,
    HealthResponse,
    OrderResponse,
    PositionResponse,
    RunOnceResponse,
)

router = APIRouter()


def _get_bot(request: Request):
    return request.app.state.bot


def _get_settings(request: Request):
    return request.app.state.settings


def _get_trading(request: Request):
    return request.app.state.trading_service


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/config", response_model=ConfigResponse)
async def config(request: Request):
    settings = _get_settings(request)
    return ConfigResponse(
        app_env=settings.app_env,
        log_level=settings.log_level,
        default_symbols=settings.default_symbols,
        default_timeframe=settings.default_timeframe,
        scan_interval_seconds=settings.scan_interval_seconds,
        order_notional_usd=settings.order_notional_usd,
        max_open_positions=settings.max_open_positions,
        cooldown_seconds_per_symbol=settings.cooldown_seconds_per_symbol,
        bar_limit=settings.bar_limit,
        paper_trading=settings.paper_trading,
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


@router.get("/bot/status", response_model=BotStatusResponse)
async def bot_status(request: Request):
    bot = _get_bot(request)
    return BotStatusResponse(**bot.status())
