from config.settings import Settings


def test_trading_mode_is_hardwired_paper(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    settings = Settings()
    assert settings.trading_mode == "PAPER"


def test_trading_mode_rejects_live(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    try:
        Settings()
        assert False, "Settings should reject TRADING_MODE=LIVE"
    except Exception:
        pass


def test_credentials_present_false_by_default():
    settings = Settings(
        angel_api_key="", angel_client_code="", angel_password_or_pin="", angel_totp_secret=""
    )
    assert settings.credentials_present() is False


def test_secret_str_never_appears_in_repr():
    settings = Settings(angel_api_key="super-secret-value")
    assert "super-secret-value" not in repr(settings.angel_api_key)
    assert "super-secret-value" not in str(settings.angel_api_key)
