from btr_pipeline.editorial import EditorialPipeline

from .test_models import valid_story


class FakeEditorialClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


def test_unavailable_sources_are_repaired_before_retention(tmp_path, monkeypatch):
    story = valid_story()
    bad = story.as_dict()
    old_url = bad["sources"][0]["url"]
    bad["sources"][0]["url"] = "https://a.example/gone"
    for scene in bad["scenes"]:
        scene["cited_source_urls"] = [
            "https://a.example/gone" if url == old_url else url
            for url in scene["cited_source_urls"]
        ]
    repaired = story.as_dict()
    client = FakeEditorialClient([bad, bad, repaired, repaired])
    pipeline = EditorialPipeline(client)

    monkeypatch.setattr(
        pipeline,
        "_verify_sources",
        lambda item: (
            ["source returned HTTP 404: https://a.example/gone"]
            if any(source.url.endswith("/gone") for source in item.sources)
            else []
        ),
    )
    result = pipeline.build_story(tmp_path)

    assert result.title == story.title
    assert len(client.calls) == 4
    assert client.calls[2]["web_search"] is True
    assert (tmp_path / "03-source-repair-1.json").exists()


def test_source_repair_accepts_story_wrapper(tmp_path, monkeypatch):
    story = valid_story()
    bad = story.as_dict()
    old_url = bad["sources"][0]["url"]
    bad["sources"][0]["url"] = "https://a.example/gone"
    for scene in bad["scenes"]:
        scene["cited_source_urls"] = [
            "https://a.example/gone" if url == old_url else url
            for url in scene["cited_source_urls"]
        ]
    repaired = story.as_dict()
    client = FakeEditorialClient([bad, bad, {"story": repaired}, repaired])
    pipeline = EditorialPipeline(client)

    monkeypatch.setattr(
        pipeline,
        "_verify_sources",
        lambda item: (
            ["source returned HTTP 404: https://a.example/gone"]
            if any(source.url.endswith("/gone") for source in item.sources)
            else []
        ),
    )

    result = pipeline.build_story(tmp_path)

    assert result.title == story.title
    saved = (tmp_path / "03-source-repair-1.json").read_text(encoding="utf-8")
    assert '"story"' not in saved


def test_invalid_retention_edits_fall_back_to_verified_story(tmp_path, monkeypatch):
    story = valid_story()
    malformed = {"story": {"title": "empty"}}
    client = FakeEditorialClient([story.as_dict(), story.as_dict(), malformed, malformed])
    pipeline = EditorialPipeline(client)
    monkeypatch.setattr(pipeline, "_verify_sources", lambda item: [])

    result = pipeline.build_story(tmp_path)

    assert result.as_dict() == story.as_dict()
    assert len(client.calls) == 4
