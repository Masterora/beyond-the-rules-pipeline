from btr_pipeline.media import _srt_time, _subtitle_chunks, _wrap_thumbnail_text


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
