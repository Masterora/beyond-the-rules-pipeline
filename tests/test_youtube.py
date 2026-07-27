from datetime import UTC, datetime
from pathlib import Path

import requests

from btr_pipeline.youtube import YouTubeUploader

from .test_models import valid_story


def test_description_discloses_synthetic_narration_and_sources():
    description = YouTubeUploader._description(valid_story(), [])
    assert "合成语音" in description
    assert "https://a.example/doc" in description
    assert "不使用盗版" in description


def test_thumbnail_forbidden_keeps_both_successful_upload_receipts(
    tmp_path, monkeypatch
):
    uploader = YouTubeUploader("client", "secret", "refresh")
    monkeypatch.setattr(uploader, "_access_token", lambda: "access")
    uploads = iter(({"id": "clean-id"}, {"id": "publish-id"}))
    monkeypatch.setattr(uploader, "_resumable_upload", lambda *_args: next(uploads))
    response = requests.Response()
    response.status_code = 403
    response._content = b'{"error":{"errors":[{"reason":"forbidden"}]}}'

    def reject_thumbnail(*_args):
        raise requests.HTTPError(response=response)

    monkeypatch.setattr(uploader, "_upload_thumbnail", reject_thumbnail)
    placeholder = Path(tmp_path / "placeholder")
    result = uploader.upload(
        story=valid_story(),
        assets=[],
        video_path=placeholder,
        clean_master_path=placeholder,
        thumbnail_path=placeholder,
        publish_at=datetime.now(UTC),
        public_upload_enabled=False,
        run_dir=tmp_path,
    )

    assert result["video_id"] == "publish-id"
    assert result["clean_master"]["video_id"] == "clean-id"
    assert result["thumbnail_response"]["status"] == "warning"
    assert (tmp_path / "upload-receipt.json").exists()
