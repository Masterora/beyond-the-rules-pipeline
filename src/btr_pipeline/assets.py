from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import quote

import requests

from .models import Scene, VisualAsset

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
VIDEO_SUFFIXES = (".webm", ".ogv", ".ogg", ".mp4")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")


def _plain(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


class CommonsAssetProvider:
    def __init__(self, timeout: int = 45):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "BeyondTheRulesRightsSafeVideo/1.0 (GitHub Actions)"}
        )

    def collect(self, scenes: list[Scene], run_dir: Path) -> list[VisualAsset]:
        asset_dir = run_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        assets: list[VisualAsset] = []
        used_urls: set[str] = set()
        required_motion = max(2, len(scenes) // 4)
        motion_count = 0
        for index, scene in enumerate(scenes):
            print(
                f"[visuals {index + 1}/{len(scenes)}] finding licensed archival media",
                flush=True,
            )
            prefer_video = motion_count < required_motion
            candidate_groups: list[list[dict[str, str]]] = []
            if prefer_video:
                candidate_groups.append(
                    self.search(scene.visual_query, prefer_video=True)
                )
                broad_query = self._broaden_video_query(scene.visual_query)
                if broad_query != scene.visual_query:
                    candidate_groups.append(
                        self.search(broad_query, prefer_video=True)
                    )
            candidate_groups.append(
                self.search(scene.visual_query, prefer_video=False)
            )
            candidates = [
                candidate for group in candidate_groups for candidate in group
            ]
            selected = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate["file_url"] not in used_urls
                    and self._license_allowed(candidate)
                ),
                None,
            )
            if selected is None:
                raise RuntimeError(
                    f"no rights-safe visual found for scene {index + 1}: {scene.visual_query}"
                )
            suffix = self._suffix(selected["file_url"], selected["media_type"])
            local_path = asset_dir / f"scene-{index + 1:02d}{suffix}"
            self._download(selected["file_url"], local_path)
            asset = VisualAsset(
                scene_index=index,
                local_path=local_path,
                media_type=selected["media_type"],
                source_url=selected["source_url"],
                file_url=selected["file_url"],
                title=selected["title"],
                creator=selected["creator"],
                license_name=selected["license_name"],
                license_url=selected["license_url"],
                attribution=selected["attribution"],
            )
            if not asset.validate_license():
                raise RuntimeError(f"license validation failed for {asset.source_url}")
            used_urls.add(asset.file_url)
            assets.append(asset)
            if asset.media_type == "video":
                motion_count += 1
            print(
                f"[visuals {index + 1}/{len(scenes)}] accepted "
                f"{asset.media_type}, {asset.license_name}",
                flush=True,
            )
        self._write_manifest(run_dir / "rights-manifest.json", assets)
        self._write_attribution(run_dir / "ATTRIBUTION.md", assets)
        return assets

    def search(self, query: str, *, prefer_video: bool) -> list[dict[str, str]]:
        search_query = f"{query} filetype:video" if prefer_video else query
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": search_query,
            "gsrnamespace": "6",
            "gsrlimit": "20",
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata|mediatype",
        }
        response = self.session.get(COMMONS_API, params=params, timeout=self.timeout)
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
        results: list[dict[str, str]] = []
        for page in pages:
            info_items = page.get("imageinfo", [])
            if not info_items:
                continue
            info = info_items[0]
            metadata = {
                key: str(item.get("value", ""))
                for key, item in info.get("extmetadata", {}).items()
            }
            file_url = str(info.get("url", ""))
            media_type = self._media_type(file_url, str(info.get("mediatype", "")))
            if prefer_video and media_type != "video":
                continue
            if not prefer_video and media_type not in {"image", "video"}:
                continue
            source_url = str(info.get("descriptionurl", "")) or (
                "https://commons.wikimedia.org/wiki/" + quote(str(page.get("title", "")))
            )
            creator = _plain(metadata.get("Artist", ""))
            license_name = _plain(
                metadata.get("LicenseShortName", "") or metadata.get("UsageTerms", "")
            )
            license_url = self._canonical_license_url(
                license_name, str(metadata.get("LicenseUrl", "")).strip()
            )
            attribution = _plain(
                metadata.get("Attribution", "")
                or metadata.get("Credit", "")
                or creator
            )
            results.append(
                {
                    "title": _plain(str(page.get("title", ""))).removeprefix("File:"),
                    "file_url": file_url,
                    "source_url": source_url,
                    "media_type": media_type,
                    "creator": creator,
                    "license_name": license_name,
                    "license_url": license_url,
                    "attribution": attribution,
                }
            )
        return results

    @staticmethod
    def _broaden_video_query(query: str) -> str:
        """Keep concrete subject terms while removing brittle archive phrasing."""
        words = re.findall(r"[A-Za-z0-9'-]+", query)
        stop = {
            "archive",
            "archival",
            "footage",
            "film",
            "video",
            "photograph",
            "photo",
            "historical",
            "scene",
        }
        concrete = [word for word in words if word.lower() not in stop]
        if not concrete:
            return query
        return " ".join(concrete[:6] + ["archive", "film"])

    @staticmethod
    def _canonical_license_url(license_name: str, license_url: str) -> str:
        if license_url:
            return license_url
        probe = license_name.lower()
        if "public domain" in probe:
            return "https://creativecommons.org/publicdomain/mark/1.0/"
        if "cc0" in probe:
            return "https://creativecommons.org/publicdomain/zero/1.0/"
        return ""

    @staticmethod
    def _license_allowed(candidate: dict[str, str]) -> bool:
        probe = candidate["license_name"].lower()
        allowed = any(
            marker in probe
            for marker in (
                "public domain",
                "cc0",
                "cc by",
                "creative commons attribution",
            )
        )
        return bool(
            allowed
            and candidate["creator"]
            and candidate["license_url"]
            and candidate["source_url"]
            and candidate["file_url"]
        )

    @staticmethod
    def _media_type(url: str, api_type: str) -> str:
        lower = url.lower().split("?")[0]
        if lower.endswith(VIDEO_SUFFIXES) or api_type.upper() == "VIDEO":
            return "video"
        if lower.endswith(IMAGE_SUFFIXES) or api_type.upper() in {"BITMAP", "DRAWING"}:
            return "image"
        return "unknown"

    @staticmethod
    def _suffix(url: str, media_type: str) -> str:
        suffix = Path(url.split("?")[0]).suffix.lower()
        if suffix and len(suffix) <= 6:
            return suffix
        return ".webm" if media_type == "video" else ".jpg"

    def _download(self, url: str, target: Path) -> None:
        with self.session.get(url, stream=True, timeout=self.timeout) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length", "0") or 0)
            if content_length > 250 * 1024 * 1024:
                raise RuntimeError(f"asset exceeds 250MB: {url}")
            with target.open("wb") as handle:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > 250 * 1024 * 1024:
                        raise RuntimeError(f"asset exceeds 250MB while downloading: {url}")
                    handle.write(chunk)

    @staticmethod
    def _write_manifest(path: Path, assets: list[VisualAsset]) -> None:
        data = {
            "policy": "Only Public Domain, CC0, CC BY, or CC BY-SA assets are accepted.",
            "assets": [asset.as_dict() for asset in assets],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _write_attribution(path: Path, assets: list[VisualAsset]) -> None:
        lines = ["# Visual attribution", ""]
        for asset in assets:
            lines.append(
                f"- Scene {asset.scene_index + 1}: [{asset.title}]({asset.source_url}) — "
                f"{asset.attribution}; [{asset.license_name}]({asset.license_url})"
            )
        lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
