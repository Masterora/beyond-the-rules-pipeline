from btr_pipeline.youtube import YouTubeUploader

from .test_models import valid_story


def test_description_discloses_synthetic_narration_and_sources():
    description = YouTubeUploader._description(valid_story(), [])
    assert "合成语音" in description
    assert "https://a.example/doc" in description
    assert "不使用盗版" in description
