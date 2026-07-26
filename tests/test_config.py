from datetime import UTC, datetime

from btr_pipeline.config import Settings


def test_public_upload_defaults_to_locked(monkeypatch):
    monkeypatch.delenv("YOUTUBE_PUBLIC_UPLOAD_ENABLED", raising=False)
    settings = Settings.from_env(require_secrets=False)
    assert settings.public_upload_enabled is False


def test_publish_time_uses_shanghai_and_rolls_forward():
    settings = Settings(openrouter_api_key="")
    # 10:00 UTC = 18:00 in Shanghai, leaving more than 45 minutes.
    assert settings.next_publish_at(datetime(2026, 7, 27, 10, tzinfo=UTC)) == datetime(
        2026, 7, 27, 11, 30, tzinfo=UTC
    )
    # At 19:00 local the 45-minute safety margin pushes to the next day.
    assert settings.next_publish_at(datetime(2026, 7, 27, 11, tzinfo=UTC)) == datetime(
        2026, 7, 28, 11, 30, tzinfo=UTC
    )


def test_missing_secrets_fail_closed(monkeypatch):
    for name in (
        "OPENROUTER_API_KEY",
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    try:
        Settings.from_env(require_secrets=True)
    except RuntimeError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)
        assert "YOUTUBE_REFRESH_TOKEN" in str(exc)
    else:
        raise AssertionError("missing production secrets should fail")
