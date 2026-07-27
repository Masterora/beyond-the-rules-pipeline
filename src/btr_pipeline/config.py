from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_refresh_token: str = ""
    pexels_api_key: str = ""
    editorial_model: str = "openai/gpt-5.2"
    tts_model: str = "qwen/qwen-audio-3.0-tts-plus"
    tts_voice: str = "longanlingxin"
    public_upload_enabled: bool = False
    timezone_name: str = "Asia/Shanghai"
    publish_hour: int = 19
    publish_minute: int = 30
    # A 6.5-minute documentary is already standard long-form YouTube content.
    # Keep the gate strict enough to reject short drafts without discarding a
    # complete edit because synthesized speech is a few seconds faster.
    min_duration_seconds: int = 390
    max_duration_seconds: int = 900

    @classmethod
    def from_env(cls, *, require_secrets: bool = True) -> Settings:
        settings = cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            youtube_client_id=os.getenv("YOUTUBE_CLIENT_ID", ""),
            youtube_client_secret=os.getenv("YOUTUBE_CLIENT_SECRET", ""),
            youtube_refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN", ""),
            pexels_api_key=os.getenv("PEXELS_API_KEY", ""),
            editorial_model=os.getenv("EDITORIAL_MODEL", "openai/gpt-5.2"),
            tts_model=os.getenv("TTS_MODEL", "qwen/qwen-audio-3.0-tts-plus"),
            tts_voice=os.getenv("TTS_VOICE", "longanlingxin"),
            public_upload_enabled=os.getenv(
                "YOUTUBE_PUBLIC_UPLOAD_ENABLED", "false"
            ).lower()
            == "true",
        )
        if require_secrets:
            missing = [
                name
                for name, value in {
                    "OPENROUTER_API_KEY": settings.openrouter_api_key,
                    "YOUTUBE_CLIENT_ID": settings.youtube_client_id,
                    "YOUTUBE_CLIENT_SECRET": settings.youtube_client_secret,
                    "YOUTUBE_REFRESH_TOKEN": settings.youtube_refresh_token,
                }.items()
                if not value
            ]
            if missing:
                raise RuntimeError(f"Missing required secrets: {', '.join(missing)}")
        return settings

    def next_publish_at(self, now: datetime | None = None) -> datetime:
        zone = ZoneInfo(self.timezone_name)
        local_now = (now or datetime.now(UTC)).astimezone(zone)
        candidate = local_now.replace(
            hour=self.publish_hour,
            minute=self.publish_minute,
            second=0,
            microsecond=0,
        )
        if candidate <= local_now + timedelta(minutes=45):
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)


def make_run_dir(base: Path) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = base / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path
