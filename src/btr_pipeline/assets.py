from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Scene, VisualAsset, license_name_allowed

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
VIDEO_SUFFIXES = (".webm", ".ogv", ".ogg", ".mp4")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")
MAX_ASSET_BYTES = 250 * 1024 * 1024


def _plain(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


class CommonsAssetProvider:
    def __init__(self, timeout: int = 45, assets_per_scene: int = 2):
        self.timeout = timeout
        self.assets_per_scene = assets_per_scene
        self._last_download_at = 0.0
        self.download_interval_seconds = 2.0
        self.cache_dir = Path(
            os.getenv("BTR_ASSET_CACHE_DIR", ".cache/btr-assets")
        )
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "BeyondTheRulesRightsSafeVideo/1.0 (GitHub Actions)"}
        )
        retry = Retry(
            total=8,
            backoff_factor=3.0,
            backoff_max=120,
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
        failed_urls: set[str] = set()
        required_motion = max(2, len(scenes) // 4)
        motion_count = 0
        era_query = self._era_context_query(scenes)
        context_query = self._context_query(scenes)
        search_cache: dict[tuple[str, bool], list[dict[str, str]]] = {}

        def cached_search(query: str, prefer_video: bool) -> list[dict[str, str]]:
            key = (query, prefer_video)
            if key not in search_cache:
                search_cache[key] = self.search(query, prefer_video=prefer_video)
            return search_cache[key]

        for index, scene in enumerate(scenes):
            scene_era_query = self._scene_era_query(scene, era_query)
            scene_context_query = self._scene_context_query(
                scene, scene_era_query, context_query
            )
            for shot_index in range(self.assets_per_scene):
                print(
                    f"[visuals scene {index + 1}/{len(scenes)}, "
                    f"shot {shot_index + 1}/{self.assets_per_scene}] finding licensed media",
                    flush=True,
                )
                motion_needed = required_motion - motion_count
                prefer_video = shot_index == 0 and motion_needed > 0
                plans: list[tuple[str, bool]] = []
                variants = self._query_variants(scene.visual_query)
                if prefer_video:
                    plans.extend(
                        (query, True)
                        for query in self._video_query_variants(scene.visual_query)
                    )
                plans.extend((query, False) for query in variants[:6])
                plans.append((scene_context_query, False))
                plans.append((context_query, False))
                selected = None
                local_path = None
                for query, video_only in plans:
                    candidates = cached_search(query, video_only)
                    eligible = [
                        candidate
                        for candidate in candidates
                        if candidate["file_url"] not in used_urls
                        and candidate["file_url"] not in failed_urls
                        and self._license_allowed(candidate)
                        and self._candidate_downloadable(candidate)
                        and self._candidate_has_semantic_overlap(candidate, query)
                        and self._candidate_relevance_score(
                            candidate, query, scene_context_query, scene.visual_query
                        )
                        >= self._minimum_relevance_score(query, scene_context_query)
                    ]
                    ranked = sorted(
                        eligible,
                        key=lambda candidate: self._candidate_relevance_score(
                            candidate, query, scene_context_query, scene.visual_query
                        ),
                        reverse=True,
                    )
                    for candidate in ranked:
                        suffix = self._suffix(
                            candidate["file_url"], candidate["media_type"]
                        )
                        candidate_path = asset_dir / (
                            f"scene-{index + 1:02d}-shot-{shot_index + 1:02d}{suffix}"
                        )
                        try:
                            self._download(candidate["file_url"], candidate_path)
                        except (requests.RequestException, RuntimeError) as exc:
                            failed_urls.add(candidate["file_url"])
                            candidate_path.unlink(missing_ok=True)
                            print(
                                f"[visuals scene {index + 1}/{len(scenes)}, "
                                f"shot {shot_index + 1}/{self.assets_per_scene}] skipped "
                                f"unusable asset: {type(exc).__name__}",
                                flush=True,
                            )
                            continue
                        selected = candidate
                        local_path = candidate_path
                        break
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
                if local_path is None:
                    raise RuntimeError("selected visual has no downloaded local file")
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

    def collect_from_manifest(
        self, manifest_path: Path, scenes: list[Scene], run_dir: Path
    ) -> list[VisualAsset]:
        """Resume a previously rights-verified selection without searching again."""
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_assets = data.get("assets", [])
        if not isinstance(raw_assets, list):
            raise TypeError("asset manifest must contain an assets list")
        expected = len(scenes) * self.assets_per_scene
        if len(raw_assets) != expected:
            raise RuntimeError(
                f"asset manifest has {len(raw_assets)} entries; expected {expected}"
            )

        asset_dir = run_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        assets: list[VisualAsset] = []
        downloaded: dict[str, Path] = {}
        shots_per_scene: dict[int, int] = {}
        for item in raw_assets:
            scene_index = int(item["scene_index"])
            if not 0 <= scene_index < len(scenes):
                raise RuntimeError(f"manifest has invalid scene index: {scene_index}")
            shots_per_scene[scene_index] = shots_per_scene.get(scene_index, 0) + 1
            shot_index = shots_per_scene[scene_index]
            file_url = str(item["file_url"])
            media_type = str(item["media_type"])
            local_path = downloaded.get(file_url)
            if local_path is None:
                bundled_path = str(item.get("bundled_path", "")).strip()
                suffix_source = bundled_path or file_url
                suffix = self._suffix(suffix_source, media_type)
                local_path = asset_dir / (
                    f"scene-{scene_index + 1:02d}-shot-{shot_index:02d}{suffix}"
                )
                if bundled_path:
                    source_path = self._resolve_bundled_path(bundled_path)
                    shutil.copyfile(source_path, local_path)
                    print(
                        f"[visuals resume {len(assets) + 1}/{expected}] restored "
                        f"bundled verified {media_type}",
                        flush=True,
                    )
                else:
                    print(
                        f"[visuals resume {len(assets) + 1}/{expected}] downloading "
                        f"verified {media_type}",
                        flush=True,
                    )
                    self._download(file_url, local_path)
                downloaded[file_url] = local_path
            asset = VisualAsset(
                scene_index=scene_index,
                local_path=local_path,
                media_type=media_type,
                source_url=str(item["source_url"]),
                file_url=file_url,
                title=str(item["title"]),
                creator=str(item["creator"]),
                license_name=str(item["license_name"]),
                license_url=str(item["license_url"]),
                attribution=str(item["attribution"]),
                provider=str(item.get("provider", "Wikimedia Commons")),
            )
            if not asset.validate_license():
                raise RuntimeError(f"license validation failed for {asset.source_url}")
            assets.append(asset)
        if any(count != self.assets_per_scene for count in shots_per_scene.values()):
            raise RuntimeError("asset manifest must provide two cuts for every scene")
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
            if not creator and (
                "public domain" in license_name.lower()
                or "cc0" in license_name.lower()
            ):
                creator = "Creator not specified on the source page"
                attribution = attribution or creator
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
                    "byte_size": str(
                        0
                        if media_type == "image" and file_url != original_url
                        else int(info.get("size", 0) or 0)
                    ),
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
            "data",
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
            "official",
            "original",
            "overlay",
            "pages",
            "paragraphs",
            "pdf",
            "photograph",
            "photo",
            "historical",
            "screenshot",
            "scene",
            "shot",
            "split-screen",
            "text",
            "the",
            "united",
            "states",
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
        lower = query.lower()
        semantic: list[str] = []
        if "bretton woods" in lower:
            semantic.append("Bretton Woods Conference")
        if "nixon" in lower:
            semantic.extend(("Richard Nixon 1971", "Richard Nixon"))
        if "bank of england" in lower:
            semantic.append("Bank of England London")
        if "london" in lower and "gold" in lower:
            semantic.append("London gold market")
        years = re.findall(r"\b(?:18|19|20)\d{2}\b", lower)
        if "gold" in lower and years:
            semantic.append(f"gold price {years[0]}")
        if "gold" in lower and any(
            marker in lower for marker in ("bar", "reserve", "vault")
        ):
            semantic.extend(
                (
                    "gold reserve vault",
                    "gold bars",
                    "gold bullion",
                    "gold ingot",
                    "bank vault gold",
                )
            )
        if "treasury" in lower:
            semantic.append("United States Treasury")
        if "foreign exchange" in lower:
            semantic.append("foreign exchange market")
        if "cpi" in lower or "inflation" in lower:
            semantic.append("CPI inflation")
        if "international monetary fund" in lower:
            semantic.append("International Monetary Fund")
        if "federal reserve" in lower:
            semantic.append("Federal Reserve building")
        for value in semantic:
            normalized = value.lower()
            if normalized not in seen:
                variants.append(value)
                seen.add(normalized)
        return variants or [query]

    @classmethod
    def _video_query_variants(cls, query: str) -> list[str]:
        lower = query.lower()
        variants = cls._query_variants(query)[:2]
        if "nixon" in lower:
            variants.extend(("Nixon speech", "Nixon"))
        if "gold" in lower and any(
            marker in lower for marker in ("bar", "reserve", "vault")
        ):
            variants.append("gold bars")
        if "federal reserve" in lower:
            variants.append("Federal Reserve")
        if "treasury" in lower:
            variants.append("United States Treasury")
        if "foreign exchange" in lower:
            variants.append("foreign exchange")
        deduplicated: list[str] = []
        seen: set[str] = set()
        for value in variants:
            normalized = value.lower()
            if normalized not in seen:
                deduplicated.append(value)
                seen.add(normalized)
        return deduplicated[:6]

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
            "data",
            "document",
            "exterior",
            "film",
            "image",
            "photograph",
            "photo",
            "screenshot",
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

    @classmethod
    def _scene_era_query(cls, scene: Scene, fallback: str) -> str:
        """Keep generic fallback footage aligned with the scene's own period."""
        match = re.search(r"\b((?:18|19|20)\d{2}s?)\b", scene.visual_query)
        if not match:
            return fallback
        period = match.group(1)
        if re.search(
            r"United States|American|U\.S\.|Washington DC",
            scene.visual_query,
            re.IGNORECASE,
        ):
            place = "United States"
        elif re.search(
            r"United Kingdom|British|London",
            scene.visual_query,
            re.IGNORECASE,
        ):
            place = "United Kingdom"
        elif "united states" in fallback.lower():
            place = "United States"
        else:
            place = "archive"
        return f"{period} {place}"

    @classmethod
    def _scene_context_query(
        cls, scene: Scene, scene_era: str, story_context: str
    ) -> str:
        variants = cls._query_variants(scene.visual_query)
        subject = variants[-1] if len(variants) > 1 else variants[0]
        context_subject = story_context.split()[-1]
        if context_subject.lower() not in subject.lower():
            subject = f"{subject} {context_subject}"
        return f"{scene_era} {subject}"

    @staticmethod
    def _minimum_relevance_score(query: str, era_query: str) -> int:
        meaningful = {
            token
            for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) >= 4
            and token
            not in {
                "archive",
                "archival",
                "historical",
                "states",
                "united",
                "video",
            }
        }
        if len(meaningful) == 1:
            return 10
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
    def _candidate_downloadable(candidate: dict[str, str]) -> bool:
        try:
            byte_size = int(candidate.get("byte_size", "0") or 0)
        except (TypeError, ValueError):
            return False
        return byte_size <= MAX_ASSET_BYTES

    @staticmethod
    def _candidate_has_semantic_overlap(
        candidate: dict[str, str], query: str
    ) -> bool:
        ignored = {
            "archive",
            "archival",
            "historical",
            "official",
            "states",
            "united",
            "video",
        }
        query_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) >= 4 and token not in ignored
        }
        if not query_tokens:
            return False
        title_tokens = set(re.findall(r"[a-z0-9]+", candidate["title"].lower()))
        required = 1 if len(query_tokens) == 1 else 2
        return len(query_tokens & title_tokens) >= required

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
        if any(
            marker in title
            for marker in (
                "erotic",
                "harlot",
                "nude",
                "nudity",
                "porn",
                "racist",
                "art society",
                "striptease",
            )
        ):
            return -100
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
        context_decades = set(
            re.findall(r"\b((?:18|19|20)\d)0s\b", context_query.lower())
        )
        requested_decades = query_decades or scene_decades or context_decades
        if requested_decades and explicit_years:
            if any(
                year // 10 == int(decade)
                for year in explicit_years
                for decade in requested_decades
            ):
                score += 20 if query_decades else 10
            else:
                score -= 30
        query_years = [
            int(value) for value in re.findall(r"\b(?:18|19|20)\d{2}\b", query_lower)
        ]
        scene_years = [
            int(value)
            for value in re.findall(r"\b(?:18|19|20)\d{2}\b", scene_query.lower())
        ]
        requested_years = query_years or scene_years
        if requested_years and explicit_years:
            nearest = min(
                abs(requested - actual)
                for requested in requested_years
                for actual in explicit_years
            )
            if nearest <= 1:
                score += 15
            elif nearest > 5:
                score -= 40
        ignored = {
            "archive",
            "film",
            "historical",
            "video",
        }
        tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", query_lower)
            if len(token) >= 4
            and token not in ignored
            and token not in {"official", "states", "united"}
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
                    "china",
                    "chinese",
                    "england",
                    "iran",
                    "islamabad",
                    "new zealand",
                    "ontario",
                    "ottawa",
                    "pakistan",
                    "queensland",
                    "tehran",
                    "united kingdom",
                )
            ):
                score -= 30
        if "united kingdom" in context:
            if any(marker in title for marker in ("england", "london", "united kingdom")):
                score += 10
            if any(
                marker in title
                for marker in (
                    "canada",
                    "china",
                    "islamabad",
                    "pakistan",
                    "united states",
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

    @staticmethod
    def _resolve_bundled_path(value: str) -> Path:
        root = Path.cwd().resolve()
        candidate = (root / value).resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise RuntimeError("bundled asset path escapes the repository")
        if not candidate.is_file():
            raise RuntimeError(f"bundled asset is missing: {value}")
        return candidate

    def _download(self, url: str, target: Path) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cached = self.cache_dir / f"{cache_key}{target.suffix.lower()}"
        if cached.is_file() and 0 < cached.stat().st_size <= MAX_ASSET_BYTES:
            shutil.copyfile(cached, target)
            print("[visuals cache] restored verified media", flush=True)
            return

        elapsed = time.monotonic() - self._last_download_at
        if elapsed < self.download_interval_seconds:
            time.sleep(self.download_interval_seconds - elapsed)
        with self.session.get(url, stream=True, timeout=self.timeout) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length", "0") or 0)
            if content_length > MAX_ASSET_BYTES:
                raise RuntimeError(f"asset exceeds {MAX_ASSET_BYTES} bytes: {url}")
            with target.open("wb") as handle:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > MAX_ASSET_BYTES:
                        raise RuntimeError(
                            f"asset exceeds {MAX_ASSET_BYTES} bytes while downloading: {url}"
                        )
                    handle.write(chunk)
        cache_part = cached.with_suffix(cached.suffix + ".part")
        shutil.copyfile(target, cache_part)
        cache_part.replace(cached)
        self._last_download_at = time.monotonic()

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
