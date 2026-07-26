import json

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


def test_known_source_move_updates_source_and_scene_citations():
    story = valid_story()
    old_url = (
        "https://www.cia.gov/readingroom/collection/"
        "project-azorian-hughes-glomar-explorer/"
    )
    new_url = "https://www.cia.gov/legacy/museum/exhibit/project-azorian/"
    replaced = story.sources[0].url
    story.sources[0].url = old_url
    for scene in story.scenes:
        scene.cited_source_urls = [
            old_url if url == replaced else url for url in scene.cited_source_urls
        ]

    EditorialPipeline._canonicalize_known_source_moves(story)

    assert story.sources[0].url == new_url
    assert any(new_url in scene.cited_source_urls for scene in story.scenes)
    assert all(old_url not in scene.cited_source_urls for scene in story.scenes)


def test_prepared_story_is_reverified_and_written(tmp_path, monkeypatch):
    story = valid_story()
    source_path = tmp_path / "prepared.json"
    source_path.write_text(
        json.dumps(story.as_dict(), ensure_ascii=False), encoding="utf-8"
    )
    pipeline = EditorialPipeline(FakeEditorialClient([]))
    monkeypatch.setattr(pipeline, "_verify_sources", lambda item: [])

    result = pipeline.load_verified_story(source_path, tmp_path / "run")

    assert result.as_dict() == story.as_dict()
    assert (tmp_path / "run" / "story.json").exists()
    assert (tmp_path / "run" / "sources.md").exists()
