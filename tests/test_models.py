from pathlib import Path

from btr_pipeline.models import Scene, Source, Story, VisualAsset


def valid_story() -> Story:
    sources = [
        Source("A", "https://a.example/doc", "A", authority="primary"),
        Source("B", "https://b.example/doc", "B", authority="primary"),
        Source("C", "https://c.example/doc", "C", authority="secondary"),
    ]
    scenes = [
        Scene(
            f"Scene {index}",
            "这是一段经过来源核验的真实叙述，它包含制度如何运作、谁承担代价，以及后来发生了什么变化。" * 7,
            f"historic archive object {index}",
            [sources[index % 3].url],
        )
        for index in range(10)
    ]
    return Story(
        title="一条规则如何改变了整座城市的选择",
        thumbnail_text="谁在买单",
        hook="一张看似普通的账单，让数万人在同一天发现，真正改变他们命运的不是价格。",
        description="真实制度故事。",
        tags=["规则", "纪录片"],
        sources=sources,
        scenes=scenes,
    )


def test_story_requires_citations_and_primary_sources():
    story = valid_story()
    assert story.validate() == []
    story.scenes[0].cited_source_urls = []
    assert any("scene 1 has no source" in error for error in story.validate())


def test_rights_gate_accepts_attribution_license():
    asset = VisualAsset(
        scene_index=0,
        local_path=Path("asset.jpg"),
        media_type="image",
        source_url="https://commons.wikimedia.org/wiki/File:X.jpg",
        file_url="https://upload.wikimedia.org/x.jpg",
        title="X",
        creator="Jane Doe",
        license_name="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="Jane Doe",
    )
    assert asset.validate_license()
    asset.license_name = "All rights reserved"
    assert not asset.validate_license()


def test_story_rejects_unknown_citation():
    story = valid_story()
    story.scenes[2].cited_source_urls = ["https://unknown.example/"]
    assert any("unknown source" in error for error in story.validate())
