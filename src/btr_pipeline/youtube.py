from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import requests

from .models import Story, VisualAsset

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"


class YouTubeUploader:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

    def upload(
        self,
        *,
        story: Story,
        assets: list[VisualAsset],
        video_path: Path,
        clean_master_path: Path,
        thumbnail_path: Path,
        publish_at: datetime,
        public_upload_enabled: bool,
        run_dir: Path,
    ) -> dict[str, object]:
        token = self._access_token()
        privacy_status = "private"
        status: dict[str, object] = {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
            "embeddable": True,
        }
        # Un-audited API projects are restricted to private uploads. This switch is
        # intentionally opt-in and should only be enabled after audit approval.
        if public_upload_enabled:
            status["publishAt"] = publish_at.isoformat().replace("+00:00", "Z")

        description = self._description(story, assets)
        if len(description) > 5000:
            raise RuntimeError("complete source and CC BY attribution exceeds 5000 characters")
        metadata = {
            "snippet": {
                "title": story.title[:100],
                "description": description,
                "tags": story.tags[:30],
                "categoryId": "27",
                "defaultLanguage": "zh-CN",
                "defaultAudioLanguage": "zh-CN",
            },
            "status": status,
        }
        # Keep the clean master in the owner's YouTube account as a private cloud
        # deliverable. This avoids exposing an unpublished master in artifacts of
        # the public compliance repository.
        clean_metadata = {
            "snippet": {
                "title": f"[无字幕母版] {story.title}"[:100],
                "description": "规则之外内部无字幕母版。保持私密，不用于公开分发。",
                "categoryId": "27",
                "defaultLanguage": "zh-CN",
                "defaultAudioLanguage": "zh-CN",
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
                "embeddable": False,
            },
        }
        print("[upload 1/2] sending clean master as private", flush=True)
        clean_receipt = self._resumable_upload(token, clean_master_path, clean_metadata)
        print("[upload 2/2] sending subtitled publish master as private", flush=True)
        receipt = self._resumable_upload(token, video_path, metadata)
        video_id = str(receipt["id"])
        result: dict[str, object] = {
            "video_id": video_id,
            "watch_url": f"https://www.youtube.com/watch?v={video_id}",
            "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
            "privacy_status": privacy_status,
            "requested_publish_at": status.get("publishAt"),
            "clean_master": {
                "video_id": clean_receipt["id"],
                "studio_url": (
                    f"https://studio.youtube.com/video/{clean_receipt['id']}/edit"
                ),
                "privacy_status": "private",
            },
            "api_response": receipt,
            "thumbnail_response": None,
        }
        receipt_path = run_dir / "upload-receipt.json"
        # Persist both video IDs before the optional thumbnail call. Some new
        # channels cannot use custom thumbnails until feature eligibility is
        # enabled; that must not erase successful private upload evidence.
        receipt_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            result["thumbnail_response"] = self._upload_thumbnail(
                token, video_id, thumbnail_path
            )
        except requests.HTTPError as exc:
            response = exc.response
            warning: dict[str, object] = {
                "status": "warning",
                "http_status": response.status_code if response is not None else None,
                "reason": "custom thumbnail was not accepted; video uploads succeeded",
            }
            if response is not None:
                try:
                    warning["api_error"] = response.json().get("error", {})
                except ValueError:
                    warning["api_error"] = response.text[:1000]
            result["thumbnail_response"] = warning
            print(
                "[thumbnail warning] custom thumbnail unavailable; uploads retained",
                flush=True,
            )
        receipt_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

    def _access_token(self) -> str:
        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        response.raise_for_status()
        return str(response.json()["access_token"])

    @staticmethod
    def _resumable_upload(
        token: str, video_path: Path, metadata: dict[str, object]
    ) -> dict[str, object]:
        size = video_path.stat().st_size
        init = requests.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": "video/mp4",
            },
            json=metadata,
            timeout=60,
        )
        init.raise_for_status()
        location = init.headers["Location"]
        chunk_size = 8 * 1024 * 1024
        offset = 0
        with video_path.open("rb") as handle:
            while offset < size:
                chunk = handle.read(chunk_size)
                end = offset + len(chunk) - 1
                response = requests.put(
                    location,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                    data=chunk,
                    timeout=300,
                )
                if response.status_code in {200, 201}:
                    return response.json()
                if response.status_code != 308:
                    response.raise_for_status()
                offset = end + 1
        raise RuntimeError("resumable upload ended without a video response")

    @staticmethod
    def _upload_thumbnail(token: str, video_id: str, path: Path) -> dict[str, object]:
        with path.open("rb") as handle:
            response = requests.post(
                THUMBNAIL_URL,
                params={"videoId": video_id},
                headers={"Authorization": f"Bearer {token}"},
                files={"media": (path.name, handle, "image/jpeg")},
                timeout=120,
            )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _description(story: Story, assets: list[VisualAsset]) -> str:
        lines = [
            story.description.strip(),
            "",
            "制作说明：旁白使用合成语音；画面来自逐项核验许可的真实档案或素材。",
            "本片不使用盗版影视、新闻台片段或未授权社交媒体素材。",
            "",
            "资料来源：",
        ]
        for source in story.sources:
            lines.append(f"• {source.title} — {source.url}")
        lines.extend(["", "CC BY画面署名："])
        attributed_urls: set[str] = set()
        for asset in assets:
            if asset.source_url in attributed_urls:
                continue
            attributed_urls.add(asset.source_url)
            if "cc by" not in asset.license_name.lower():
                continue
            lines.append(
                f"• {asset.title} / {asset.creator} / {asset.license_name} / "
                f"{asset.source_url}"
            )
        lines.extend(["", "#规则之外 #纪录片 #真实故事"])
        return "\n".join(lines)
