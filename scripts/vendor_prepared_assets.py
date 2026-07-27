from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "BeyondTheRulesRightsSafeVideo/1.0 "
                "(https://github.com/Masterora/beyond-the-rules-pipeline)"
            )
        }
    )
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=5,
                backoff_factor=2,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=("GET",),
                respect_retry_after_header=True,
            )
        ),
    )
    return session


def _download(session: requests.Session, url: str, target: Path) -> None:
    with session.get(url, stream=True, timeout=90) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                handle.write(chunk)
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"empty download: {url}")


def _prepare_image(source: Path, target: Path) -> None:
    with Image.open(source) as opened:
        opened.seek(0)
        image = opened.convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        background.alpha_composite(image)
        rgb = background.convert("RGB")
        rgb.thumbnail((2560, 1440), Image.Resampling.LANCZOS)
        rgb.save(target, format="JPEG", quality=90, optimize=True, progressive=True)


def _prepare_video(source: Path, target: Path, *, start_time: float = 0) -> None:
    seek = ["-ss", f"{start_time:.3f}"] if start_time > 0 else []
    completed = subprocess.run(
        [
            os.getenv("BTR_FFMPEG", "ffmpeg"),
            "-y",
            *seek,
            "-i",
            str(source),
            "-t",
            "45",
            "-vf",
            (
                "scale='min(1280,iw)':-2:force_original_aspect_ratio=decrease,"
                "fps=30,format=yuv420p"
            ),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "24",
            "-movflags",
            "+faststart",
            str(target),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"ffmpeg failed: {completed.stderr[-2000:]}")


def vendor(manifest_path: Path, output_dir: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise TypeError("manifest must contain an assets list")
    output_dir.mkdir(parents=True, exist_ok=True)
    prepared: dict[tuple[str, float], str] = {}
    session = _session()
    unique_count = len(
        {
            (str(asset["file_url"]), float(asset.get("start_time", 0)))
            for asset in assets
        }
    )
    with tempfile.TemporaryDirectory(prefix="btr-vendor-") as temp_name:
        temp_dir = Path(temp_name)
        for asset in assets:
            url = str(asset["file_url"])
            start_time = float(asset.get("start_time", 0))
            signature = (url, start_time)
            bundled = prepared.get(signature)
            if bundled is None:
                index = len(prepared) + 1
                media_type = str(asset["media_type"])
                extension = ".mp4" if media_type == "video" else ".jpg"
                digest_input = (
                    url if start_time <= 0 else f"{url}#start={start_time:.3f}"
                )
                filename = (
                    hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
                    + extension
                )
                target = output_dir / filename
                if not target.is_file():
                    raw = temp_dir / (filename + ".source")
                    print(f"[{index}/{unique_count}] downloading verified {media_type}")
                    _download(session, url, raw)
                    if media_type == "video":
                        _prepare_video(raw, target, start_time=start_time)
                    elif media_type == "image":
                        _prepare_image(raw, target)
                    else:
                        raise RuntimeError(f"unsupported media type: {media_type}")
                bundled = target.as_posix()
                prepared[signature] = bundled
            asset["bundled_path"] = bundled
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    vendor(args.manifest, args.output_dir)


if __name__ == "__main__":
    main()
