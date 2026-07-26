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
    bad["sources"][0]["url"] = "https://a.example/gone"
    bad["scenes"][0]["cited_source_urls"] = ["https://a.example/gone"]
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
