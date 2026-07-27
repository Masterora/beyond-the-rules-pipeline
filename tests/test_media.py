from pathlib import Path

from btr_pipeline.media import (
    _select_thumbnail_asset,
    _srt_time,
    _still_input_args,
    _subtitle_chunks,
    _wrap_thumbnail_text,
)
from btr_pipeline.models import VisualAsset


def test_chinese_subtitles_are_readable_chunks():
    text = "这是一句很长的中文旁白，它需要被切成更短、更容易在手机上阅读的字幕。第二句话必须保留。"
    chunks = _subtitle_chunks(text, maximum=16)
    assert len(chunks) >= 3
    assert max(map(len, chunks)) <= 16
    assert "".join(chunks).replace(" ", "") == text


def test_srt_clock():
    assert _srt_time(3661.234) == "01:01:01,234"


def test_thumbnail_copy_wraps_to_two_lines():
    assert _wrap_thumbnail_text("谁在为规则买单") == "谁在为规\n则买单"


def test_animated_gif_uses_generic_stream_loop():
    assert _still_input_args(Path("chart.gif")) == [
        "-stream_loop",
        "-1",
        "-i",
        "chart.gif",
    ]
    assert _still_input_args(Path("archive.jpg")) == [
        "-loop",
        "1",
        "-i",
        "archive.jpg",
    ]


def test_thumbnail_prefers_named_rights_safe_subject():
    def asset(title):
        return VisualAsset(
            0,
            Path("asset.jpg"),
            "image",
            "https://commons.example/source",
            "https://commons.example/file",
            title,
            "Archivist",
            "CC0",
            "https://creativecommons.org/publicdomain/zero/1.0/",
            "Archivist",
        )

    generic = asset("Generic archive")
    gold = asset("Gold bullion bars")

    assert _select_thumbnail_asset(
        [generic, gold], preferred=("gold bullion bars",)
    ) is gold
