from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image

from .media import ffprobe_duration
from .models import Story, VisualAsset


class QualityGate:
    def __init__(self, min_duration: int, max_duration: int):
        self.min_duration = min_duration
        self.max_duration = max_duration

    def inspect(
        self,
        story: Story,
        assets: list[VisualAsset],
        outputs: dict[str, Path],
        run_dir: Path,
    ) -> dict[str, object]:
        checks: dict[str, object] = {}
        errors: list[str] = []

        story_errors = story.validate()
        checks["editorial_errors"] = story_errors
        errors.extend(story_errors)

        rights_failures = [
            asset.source_url for asset in assets if not asset.validate_license()
        ]
        checks["rights_failures"] = rights_failures
        errors.extend(f"invalid asset license: {url}" for url in rights_failures)
        unique_assets = len({asset.file_url for asset in assets})
        checks["unique_visuals"] = unique_assets
        if unique_assets != len(assets):
            errors.append("visual assets are duplicated")
        video_count = sum(asset.media_type == "video" for asset in assets)
        checks["motion_clip_count"] = video_count
        required_motion = max(2, len(assets) // 4)
        if video_count < required_motion:
            errors.append(
                f"not enough real motion clips: {video_count}, required {required_motion}"
            )

        clean_duration = ffprobe_duration(outputs["clean"])
        subtitled_duration = ffprobe_duration(outputs["subtitled"])
        checks["clean_duration_seconds"] = round(clean_duration, 3)
        checks["subtitled_duration_seconds"] = round(subtitled_duration, 3)
        if not self.min_duration <= clean_duration <= self.max_duration:
            errors.append(
                f"duration {clean_duration:.1f}s outside "
                f"{self.min_duration}-{self.max_duration}s"
            )
        if abs(clean_duration - subtitled_duration) > 0.5:
            errors.append("clean and subtitled durations differ")

        for label in ("clean", "subtitled"):
            stream_data = _probe_streams(outputs[label])
            checks[f"{label}_streams"] = stream_data
            videos = [item for item in stream_data if item.get("codec_type") == "video"]
            audios = [item for item in stream_data if item.get("codec_type") == "audio"]
            if not videos or not audios:
                errors.append(f"{label} master lacks audio or video")
                continue
            video = videos[0]
            if video.get("width") != 1920 or video.get("height") != 1080:
                errors.append(f"{label} master is not 1920x1080")
            if video.get("pix_fmt") != "yuv420p":
                errors.append(f"{label} master is not yuv420p")

        with Image.open(outputs["thumbnail"]) as thumbnail:
            checks["thumbnail_dimensions"] = list(thumbnail.size)
            checks["thumbnail_mode"] = thumbnail.mode
            if thumbnail.size != (1280, 720) or thumbnail.mode != "RGB":
                errors.append("thumbnail must be 1280x720 RGB")
        if outputs["thumbnail"].stat().st_size > 2 * 1024 * 1024:
            errors.append("thumbnail exceeds YouTube 2MB limit")
        if outputs["captions"].stat().st_size < 500:
            errors.append("captions appear incomplete")

        checks["status"] = "pass" if not errors else "fail"
        checks["errors"] = errors
        report = run_dir / "qa-report.json"
        report.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
        if errors:
            raise RuntimeError("quality gate failed: " + "; ".join(errors))
        print("[qa] editorial, rights, motion, format, and duration gates passed", flush=True)
        return checks


def _probe_streams(path: Path) -> list[dict[str, object]]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"ffprobe failed for {path}: {completed.stderr}")
    return json.loads(completed.stdout).get("streams", [])
