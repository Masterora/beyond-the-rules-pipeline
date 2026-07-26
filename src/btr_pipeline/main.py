from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .assets import CommonsAssetProvider
from .config import Settings, make_run_dir
from .editorial import EditorialPipeline
from .media import NarrationProducer, VideoRenderer
from .openrouter import OpenRouterClient
from .qa import QualityGate
from .youtube import YouTubeUploader


def produce(
    run_dir: Path,
    settings: Settings,
    *,
    skip_upload: bool = False,
    story_json: Path | None = None,
    assets_json: Path | None = None,
) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    _require_binaries("ffmpeg", "ffprobe")
    recent_topics = _load_recent_topics(Path(".state/recent-topics.json"))
    client = OpenRouterClient(settings.openrouter_api_key, settings.editorial_model)

    editorial = EditorialPipeline(client)
    if story_json is not None:
        story = editorial.load_verified_story(story_json, run_dir)
    else:
        story = editorial.build_story(run_dir, recent_topics)
    asset_provider = CommonsAssetProvider()
    if assets_json is not None:
        assets = asset_provider.collect_from_manifest(assets_json, story.scenes, run_dir)
    else:
        assets = asset_provider.collect(story.scenes, run_dir)
    speech = NarrationProducer(
        client, model=settings.tts_model, voice=settings.tts_voice
    ).synthesize(story, run_dir)
    outputs = VideoRenderer(run_dir).render(story, assets, speech)
    qa = QualityGate(
        settings.min_duration_seconds, settings.max_duration_seconds
    ).inspect(story, assets, outputs, run_dir)

    receipt = None
    if not skip_upload:
        uploader = YouTubeUploader(
            settings.youtube_client_id,
            settings.youtube_client_secret,
            settings.youtube_refresh_token,
        )
        receipt = uploader.upload(
            story=story,
            assets=assets,
            video_path=outputs["subtitled"],
            clean_master_path=outputs["clean"],
            thumbnail_path=outputs["thumbnail"],
            publish_at=settings.next_publish_at(),
            public_upload_enabled=settings.public_upload_enabled,
            run_dir=run_dir,
        )
    _save_recent_topic(Path(".state/recent-topics.json"), story.title)
    summary = {
        "status": "uploaded" if receipt else "rendered",
        "title": story.title,
        "outputs": {key: str(value) for key, value in outputs.items()},
        "qa": qa,
        "upload": receipt,
    }
    (run_dir / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def dry_run(run_dir: Path) -> dict:
    settings = Settings.from_env(require_secrets=False)
    publish_at = settings.next_publish_at()
    checks = {
        "status": "pass",
        "mode": "dry-run",
        "public_upload_enabled": settings.public_upload_enabled,
        "privacy_default": "private",
        "next_publish_at_utc": publish_at.isoformat(),
        "required_binaries": {
            name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe")
        },
        "required_secrets_present": {
            name: bool(value)
            for name, value in {
                "OPENROUTER_API_KEY": settings.openrouter_api_key,
                "YOUTUBE_CLIENT_ID": settings.youtube_client_id,
                "YOUTUBE_CLIENT_SECRET": settings.youtube_client_secret,
                "YOUTUBE_REFRESH_TOKEN": settings.youtube_refresh_token,
            }.items()
        },
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dry-run.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--story-json", type=Path)
    parser.add_argument("--assets-json", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir or make_run_dir(args.runs_root)
    if args.dry_run:
        result = dry_run(run_dir)
    else:
        result = produce(
            run_dir,
            Settings.from_env(require_secrets=not args.skip_upload),
            skip_upload=args.skip_upload,
            story_json=args.story_json,
            assets_json=args.assets_json,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _require_binaries(*names: str) -> None:
    missing = [name for name in names if not shutil.which(name)]
    if missing:
        raise RuntimeError(f"missing binaries: {', '.join(missing)}")


def _load_recent_topics(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(item) for item in data.get("topics", [])][-30:]


def _save_recent_topic(path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    topics = _load_recent_topics(path)
    topics.append(title)
    path.write_text(
        json.dumps({"topics": topics[-30:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
