from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Scene, VisualAsset, license_name_allowed

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
VIDEO_SUFFIXES = (".webm", ".ogv", ".ogg", ".mp4")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")


def _plain(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


class CommonsAssetProvider:
    def __init__(self, timeout: int = 45, assets_per_scene: int = 2):
        self.timeout = timeout
        self.assets_per_scene = assets_per_scene
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "BeyondTheRulesRightsSafeVideo/1.0 (GitHub Actions)"}
        )
        retry = Retry(
            total=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def collect(self, scenes: list[Scene], run_dir: Path) -> list[VisualAsset]:
        asset_dir = run_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        assets: list[VisualAsset] = []
        used_urls: set[str] = set()
        required_motion = max(2, len(scenes) // 4)
        motion_count = 0
        interval = max(1, len(scenes) // required_motion)
        video_slots = {
            min(1 + offset * interval, len(scenes) - 1)
            for offset in range(required_motion)
        }
        era_query = self._era_context_query(scenes)
        context_query = self._context_query(scenes)
        search_cache: dict[tuple[str, bool], list[dict[str, str]]] = {}

        def cached_search(query: str, prefer_video: bool) -> list[dict[str, str]]:
            key = (query, prefer_video)
            if key not in search_cache:
                search_cache[key] = self.search(query, prefer_video=prefer_video)
            return search_cache[key]

        for index, scene in enumerate(scenes):
            for shot_index in range(self.assets_per_scene):
                print(
                    f"[visuals scene {index + 1}/{len(scenes)}, "
                    f"shot {shot_index + 1}/{self.assets_per_scene}] finding licensed media",
                    flush=True,
                )
                motion_needed = required_motion - motion_count
                scenes_remaining = len(scenes) - index
                prefer_video = shot_index == 0 and motion_needed > 0 and (
                    index in video_slots or scenes_remaining <= motion_needed
                )
                plans: list[tuple[str, bool]] = []
                variants = self._query_variants(scene.visual_query)
                if prefer_video:
                    plans.extend((query, True) for query in variants[:2])
                    plans.append((context_query, True))
                    plans.append((era_query, True))
                plans.extend((query, False) for query in variants[:4])
                plans.append((context_query, False))
                plans.append((era_query, False))
                selected = None
                for query, video_only in plans:
                    candidates = cached_search(query, video_only)
                    eligible = [
                        candidate
                        for candidate in candidates
                        if candidate["file_url"] not in used_urls
                        and self._license_allowed(candidate)
                        and self._candidate_relevance_score(
                            candidate, query, context_query, scene.visual_query
                        )
                        >= self._minimum_relevance_score(query, era_query)
                    ]
                    selected = max(
                        eligible,
                        key=lambda candidate: self._candidate_relevance_score(
                            candidate, query, context_query, scene.visual_query
                        ),
                        default=None,
                    )
                    if selected is not None:
                        break
                if selected is None:
                    prior_scene_assets = [
                        asset for asset in assets if asset.scene_index == index
                    ]
                    if shot_index > 0 and prior_scene_assets:
                        base = prior_scene_assets[0]
                        assets.append(
                            VisualAsset(
                                scene_index=index,
                                local_path=base.local_path,
                                media_type=base.media_type,
                                source_url=base.source_url,
                                file_url=base.file_url,
                                title=base.title,
                                creator=base.creator,
                                license_name=base.license_name,
                                license_url=base.license_url,
                                attribution=base.attribution,
                            )
                        )
                        print(
                            f"[visuals scene {index + 1}/{len(scenes)}, "
                            f"shot {shot_index + 1}/{self.assets_per_scene}] using "
                            "a separately animated detail cut of the licensed scene asset",
                            flush=True,
                        )
                        continue
                    raise RuntimeError(
                        f"no rights-safe visual found for scene {index + 1}, "
                        f"shot {shot_index + 1}: {scene.visual_query}"
                    )
                suffix = self._suffix(selected["file_url"], selected["media_type"])
                local_path = asset_dir / (
                    f"scene-{index + 1:02d}-shot-{shot_index + 1:02d}{suffix}"
                )
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
                    f"[visuals scene {index + 1}/{len(scenes)}, "
                    f"shot {shot_index + 1}/{self.assets_per_scene}] accepted "
                    f"{asset.media_type}, {asset.license_name}: {asset.title[:90]}",
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
            "gsrlimit": "30",
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata|mediatype",
            "iiurlwidth": "2160",
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
            original_url = str(info.get("url", ""))
            media_type = self._media_type(
                original_url, str(info.get("mediatype", ""))
            )
            file_url = original_url
            if media_type == "image":
                file_url = str(info.get("thumburl", "")) or original_url
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
    def _compact_query(query: str) -> str:
        """Turn a shot description into a Wikimedia-style subject query."""
        words = re.findall(r"[A-Za-z0-9'-]+", query.replace("U.S.", "United States"))
        stop = {
            "a",
            "an",
            "and",
            "archive",
            "archival",
            "case",
            "close-up",
            "closeup",
            "crowd",
            "display",
            "document",
            "documents",
            "exhibit",
            "footage",
            "film",
            "graphic",
            "video",
            "highlighted",
            "label",
            "macro",
            "museum",
            "of",
            "original",
            "overlay",
            "pages",
            "paragraphs",
            "pdf",
            "photograph",
            "photo",
            "historical",
            "scene",
            "shot",
            "split-screen",
            "text",
            "the",
        }
        concrete: list[str] = []
        seen: set[str] = set()
        for word in words:
            normalized = word.lower()
            if normalized in stop or normalized in seen:
                continue
            concrete.append(word)
            seen.add(normalized)
        if not concrete:
            return query
        return " ".join(concrete[:3])

    @classmethod
    def _query_variants(cls, query: str) -> list[str]:
        segments = [query, *re.split(r"[,;]", query)]
        variants: list[str] = []
        seen: set[str] = set()
        for segment in segments:
            compact = cls._compact_query(segment)
            normalized = compact.lower()
            if len(compact.split()) < 2 or normalized in seen:
                continue
            variants.append(compact)
            seen.add(normalized)
        return variants or [query]

    @classmethod
    def _era_context_query(cls, scenes: list[Scene]) -> str:
        combined = " ".join(scene.visual_query for scene in scenes)
        years = re.findall(r"\b(?:18|19|20)\d{2}s?\b", combined)
        year = max(set(years), key=years.count) if years else "historical"
        if re.search(r"United States|American|U\.S\.", combined, re.IGNORECASE):
            place = "United States"
        elif re.search(r"United Kingdom|British", combined, re.IGNORECASE):
            place = "United Kingdom"
        elif re.search(r"Europe|European", combined, re.IGNORECASE):
            place = "Europe"
        else:
            place = "archive"
        return f"{year} {place}"

    @classmethod
    def _context_query(cls, scenes: list[Scene]) -> str:
        era = cls._era_context_query(scenes)
        combined = " ".join(scene.visual_query for scene in scenes).lower()
        ignored = {
            "american",
            "archive",
            "archival",
            "building",
            "close",
            "document",
            "film",
            "photograph",
            "states",
            "united",
            "video",
        }
        words = [
            word
            for word in re.findall(r"[a-z]+", combined)
            if len(word) >= 4 and word not in ignored
        ]
        if not words:
            return era
        counts = {word: words.count(word) for word in set(words)}
        subject = max(words, key=lambda word: (counts[word], -words.index(word)))
        return f"{era} {subject}"

    @staticmethod
    def _minimum_relevance_score(query: str, era_query: str) -> int:
        if query == era_query:
            return 20
        if re.search(r"\b(?:18|19|20)\d0s\b", query):
            return 30
        return 20

    @classmethod
    def _broaden_video_query(cls, query: str) -> str:
        return f"{cls._compact_query(query)} archive film"

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
        return bool(
            license_name_allowed(candidate["license_name"])
            and candidate["creator"]
            and candidate["license_url"]
            and candidate["source_url"]
            and candidate["file_url"]
        )

    @staticmethod
    def _candidate_relevant(candidate: dict[str, str], query: str) -> bool:
        return CommonsAssetProvider._candidate_relevance_score(candidate, query) >= 20

    @staticmethod
    def _candidate_relevance_score(
        candidate: dict[str, str],
        query: str,
        context_query: str = "",
        scene_query: str = "",
    ) -> int:
        """Rank by visible title, era, and country rather than hidden keyword noise."""
        title = candidate["title"].lower()
        title_tokens = set(re.findall(r"[a-z0-9]+", title))
        query_lower = query.lower()
        score = 0
        explicit_years = [
            int(value) for value in re.findall(r"\b(?:18|19|20)\d{2}\b", title)
        ]
        query_decades = set(re.findall(r"\b((?:18|19|20)\d)0s\b", query_lower))
        scene_decades = set(
            re.findall(r"\b((?:18|19|20)\d)0s\b", scene_query.lower())
        )
        requested_decades = query_decades or scene_decades
        if requested_decades and explicit_years:
            if any(
                year // 10 == int(decade)
                for year in explicit_years
                for decade in requested_decades
            ):
                score += 20 if query_decades else 10
            else:
                score -= 30
        ignored = {
            "archive",
            "film",
            "historical",
            "video",
        }
        tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", query_lower)
            if len(token) >= 4 and token not in ignored
        }
        matches = sum(token in title_tokens for token in tokens)
        score += matches * 10
        compact_phrase = " ".join(re.findall(r"[a-z0-9]+", query_lower))
        if compact_phrase and compact_phrase in " ".join(re.findall(r"[a-z0-9]+", title)):
            score += 20

        context = context_query.lower()
        context_subjects = {
            token
            for token in re.findall(r"[a-z]+", context)
            if len(token) >= 4
            and token not in {"archive", "historical", "states", "united"}
        }
        score += 5 * sum(token in title_tokens for token in context_subjects)
        if "united states" in context:
            if any(marker in title for marker in ("united states", "u.s.", "american")):
                score += 10
            if any(
                marker in title
                for marker in (
                    "australia",
                    "brisbane",
                    "canada",
                    "canadian",
                    "england",
                    "new zealand",
                    "ontario",
                    "ottawa",
                    "queensland",
                    "united kingdom",
                )
            ):
                score -= 30
        return score

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
            "policy": "Only Public Domain, CC0, or CC BY assets are accepted.",
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
