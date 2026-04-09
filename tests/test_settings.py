from __future__ import annotations

from app.config.settings import AppSettings


def test_app_settings_reads_uppercase_env_vars(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DEFAULT_SYMBOLS", '["BTC/USD","ETH/USD"]')
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "45")
    monkeypatch.setenv("ORDER_NOTIONAL_USD", "250.5")

    settings = AppSettings()

    assert settings.app_env == "production"
    assert settings.log_level == "DEBUG"
    assert settings.alpaca_api_key == "test-key"
    assert settings.alpaca_secret_key == "test-secret"
    assert settings.default_symbols == ["BTC/USD", "ETH/USD"]
    assert settings.paper_trading is False
    assert settings.scan_interval_seconds == 45
    assert settings.order_notional_usd == 250.5


def test_app_settings_reads_uppercase_dotenv_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "APP_ENV=production\n"
        "ALPACA_API_KEY=test-key\n"
        "ALPACA_SECRET_KEY=test-secret\n"
        "DEFAULT_SYMBOLS=[\"BTC/USD\",\"ETH/USD\"]\n"
        "PAPER_TRADING=true\n"
        "SCAN_INTERVAL_SECONDS=30\n"
        "ORDER_NOTIONAL_USD=150\n"
    )
    for key in [
        "APP_ENV",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "DEFAULT_SYMBOLS",
        "PAPER_TRADING",
        "SCAN_INTERVAL_SECONDS",
        "ORDER_NOTIONAL_USD",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    from app.config.settings import AppSettings

    settings = AppSettings()

    assert settings.app_env == "production"
    assert settings.alpaca_api_key == "test-key"
    assert settings.alpaca_secret_key == "test-secret"
    assert settings.default_symbols == ["BTC/USD", "ETH/USD"]
    assert settings.paper_trading is True
    assert settings.scan_interval_seconds == 30
    assert settings.order_notional_usd == 150.0


def test_live_broker_mode_requires_allow_live_trading():
    settings = AppSettings(
        broker_mode="live",
        trading_enabled=True,
        allow_live_trading=False,
    )

    assert settings.is_live_mode is True
    assert settings.trading_allowed is False
    assert settings.alpaca_base_url == "https://api.alpaca.markets"
    assert settings.paper_trading is False
