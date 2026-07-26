from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ALLOWED_LICENSE_MARKERS = (
    "public domain",
    "cc0",
    "cc by",
    "creative commons attribution",
)


def license_name_allowed(name: str) -> bool:
    normalized = name.lower()
    if "share alike" in normalized or "share-alike" in normalized or "by-sa" in normalized:
        return False
    return any(marker in normalized for marker in ALLOWED_LICENSE_MARKERS)


@dataclass
class Source:
    title: str
    url: str
    publisher: str
    published_at: str = ""
    authority: str = "secondary"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Source:
        return cls(**{key: data.get(key, "") for key in cls.__annotations__})


@dataclass
class Scene:
    heading: str
    narration: str
    visual_query: str
    cited_source_urls: list[str] = field(default_factory=list)
    on_screen_fact: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scene:
        return cls(
            heading=str(data.get("heading", "")).strip(),
            narration=str(data.get("narration", "")).strip(),
            visual_query=str(data.get("visual_query", "")).strip(),
            cited_source_urls=[str(v) for v in data.get("cited_source_urls", [])],
            on_screen_fact=str(data.get("on_screen_fact", "")).strip(),
        )


@dataclass
class Story:
    title: str
    thumbnail_text: str
    hook: str
    description: str
    tags: list[str]
    sources: list[Source]
    scenes: list[Scene]
    factual_risk_notes: list[str] = field(default_factory=list)
    synthetic_media_disclosure: str = "Narration uses synthetic speech."

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Story:
        return cls(
            title=str(data.get("title", "")).strip(),
            thumbnail_text=str(data.get("thumbnail_text", "")).strip(),
            hook=str(data.get("hook", "")).strip(),
            description=str(data.get("description", "")).strip(),
            tags=[str(v).strip() for v in data.get("tags", []) if str(v).strip()],
            sources=[Source.from_dict(v) for v in data.get("sources", [])],
            scenes=[Scene.from_dict(v) for v in data.get("scenes", [])],
            factual_risk_notes=[str(v) for v in data.get("factual_risk_notes", [])],
            synthetic_media_disclosure=str(
                data.get("synthetic_media_disclosure", "Narration uses synthetic speech.")
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not 12 <= len(self.title) <= 90:
            errors.append("title length must be 12-90 characters")
        if not 2 <= len(self.thumbnail_text) <= 16:
            errors.append("thumbnail text length must be 2-16 characters")
        if len(self.hook) < 30:
            errors.append("hook is too short")
        if len(self.scenes) < 10:
            errors.append("at least 10 scenes are required")
        narration_length = sum(len(scene.narration) for scene in self.scenes)
        if not 2400 <= narration_length <= 4200:
            errors.append("total narration must be 2400-4200 Chinese characters")
        if len(self.sources) < 3:
            errors.append("at least 3 sources are required")
        domains = {source.publisher.lower().strip() for source in self.sources}
        if len(domains) < 3:
            errors.append("at least 3 distinct publishers are required")
        if sum(source.authority == "primary" for source in self.sources) < 2:
            errors.append("at least 2 primary/authoritative sources are required")
        known_urls = {source.url for source in self.sources}
        for index, scene in enumerate(self.scenes, start=1):
            if len(scene.narration) < 80:
                errors.append(f"scene {index} narration is too short")
            if not scene.visual_query:
                errors.append(f"scene {index} has no visual query")
            if not scene.cited_source_urls:
                errors.append(f"scene {index} has no source citation")
            unknown = set(scene.cited_source_urls) - known_urls
            if unknown:
                errors.append(f"scene {index} cites unknown source URLs")
        return errors


@dataclass
class VisualAsset:
    scene_index: int
    local_path: Path
    media_type: str
    source_url: str
    file_url: str
    title: str
    creator: str
    license_name: str
    license_url: str
    attribution: str
    provider: str = "Wikimedia Commons"

    def validate_license(self) -> bool:
        return bool(self.source_url and self.creator and self.license_url) and (
            license_name_allowed(self.license_name)
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["local_path"] = str(self.local_path)
        return data
