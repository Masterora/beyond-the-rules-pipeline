import json

from btr_pipeline.assets import CommonsAssetProvider
from btr_pipeline.models import Scene


class _DownloadResponse:
    def __init__(self):
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        assert chunk_size == 1024 * 1024
        yield b"rights-safe-media"


def test_public_domain_metadata_gets_canonical_status_url():
    assert CommonsAssetProvider._canonical_license_url("Public domain", "") == (
        "https://creativecommons.org/publicdomain/mark/1.0/"
    )


def test_cc_license_without_versioned_url_stays_rejected():
    assert CommonsAssetProvider._canonical_license_url("CC BY-SA", "") == ""


def test_share_alike_asset_is_rejected_for_youtube_composite():
    candidate = {
        "license_name": "CC BY-SA 4.0",
        "creator": "Example creator",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Example.webm",
        "file_url": "https://upload.wikimedia.org/example.webm",
    }

    assert CommonsAssetProvider._license_allowed(candidate) is False


def test_video_query_broadening_keeps_concrete_terms():
    assert CommonsAssetProvider._broaden_video_query(
        "1960s European chicken market archival footage Hamburg"
    ) == "1960s European chicken archive film"


def test_shot_description_becomes_subject_queries():
    assert CommonsAssetProvider._query_variants(
        "Executive Order 6102 original document April 5 1933 National Archives, "
        "close-up of paragraphs and exemptions"
    ) == ["Executive Order 6102"]


def test_story_context_query_uses_dominant_period_and_place():
    from btr_pipeline.models import Scene

    scenes = [
        Scene("one", "n" * 100, "1933 bank run United States", ["https://a"]),
        Scene("two", "n" * 100, "1933 Treasury Washington", ["https://a"]),
        Scene("three", "n" * 100, "1930s American family", ["https://a"]),
    ]

    assert CommonsAssetProvider._era_context_query(scenes) == "1933 United States"
    assert CommonsAssetProvider._context_query(scenes) == "1933 United States bank"


def test_scene_period_overrides_story_period_for_fallback_footage():
    from btr_pipeline.models import Scene

    scene = Scene(
        "one",
        "n" * 100,
        "Bretton Woods Conference 1944 Mount Washington Hotel",
        ["https://a"],
    )

    assert CommonsAssetProvider._scene_era_query(
        scene, "1970s United States"
    ) == "1944 United States"


def test_search_metadata_false_positive_is_rejected_by_visible_title():
    candidate = {"title": "Address Before Congress Barack Obama 2009.webm"}

    assert CommonsAssetProvider._candidate_relevant(candidate, "Great Depression") is False


def test_decade_context_accepts_a_title_from_that_decade():
    candidate = {"title": "Golden Gate Bridge Opening (1936).ogv"}

    assert CommonsAssetProvider._candidate_relevant(candidate, "1930s United States")


def test_explicit_year_outside_requested_decade_is_penalized():
    candidate = {"title": "United States Marines on Parade 1942.webm"}

    assert CommonsAssetProvider._candidate_relevance_score(
        candidate, "1930s United States", "1930s United States gold"
    ) < 20


def test_explicit_scene_year_rejects_unrelated_era_fallback():
    candidate = {"title": "Harlot (1971).webm"}

    assert CommonsAssetProvider._candidate_relevance_score(
        candidate,
        "1970s United States",
        "1970s United States gold",
        "Bretton Woods Conference 1944 Mount Washington Hotel",
    ) < 20


def test_oversized_video_is_rejected_before_download():
    candidate = {"byte_size": str(251 * 1024 * 1024)}

    assert CommonsAssetProvider._candidate_downloadable(candidate) is False


def test_country_and_one_ambiguous_word_do_not_replace_subject_match():
    candidate = {"title": "U.S. military medics at Cobra Gold 2015.webm"}

    assert CommonsAssetProvider._candidate_has_semantic_overlap(
        candidate, "gold reserve vault"
    ) is False


def test_two_subject_terms_are_enough_for_contextual_b_roll():
    candidate = {"title": "How to detect tungsten filled gold bars.ogv"}

    assert CommonsAssetProvider._candidate_has_semantic_overlap(
        candidate, "gold reserve vault bars"
    )


def test_story_period_penalizes_modern_video_when_scene_omits_year():
    candidate = {"title": "New U.S. Treasury Secretary in 2013.ogv"}

    assert CommonsAssetProvider._candidate_relevance_score(
        candidate,
        "United States Treasury",
        "1970s United States Treasury gold",
        "U.S. Treasury reserve dataset",
    ) < 20


def test_scene_decade_rejects_a_modern_landmark_photo():
    candidate = {"title": "US Capitol dome January 2006.jpg"}

    assert CommonsAssetProvider._candidate_relevance_score(
        candidate,
        "US Capitol building",
        "1930s United States gold",
        "1970s gold coins, US Capitol building",
    ) < 20


def test_country_context_penalizes_foreign_same_name_landmark():
    us = {"title": "Supreme Court of the United States.jpg"}
    canada = {"title": "Supreme Court of Canada.jpg"}

    assert CommonsAssetProvider._candidate_relevance_score(
        us, "Supreme Court building", "1930s United States"
    ) > CommonsAssetProvider._candidate_relevance_score(
        canada, "Supreme Court building", "1930s United States"
    )


def test_single_name_collision_does_not_pass_two_term_query():
    candidate = {"title": "Perry Iowa welcome sign.jpg"}

    assert CommonsAssetProvider._candidate_relevant(candidate, "Perry United States") is False


def test_verified_manifest_resumes_without_search(monkeypatch, tmp_path):
    scenes = [
        Scene("one", "n" * 100, "gold one", ["https://a"]),
        Scene("two", "n" * 100, "gold two", ["https://a"]),
    ]
    base = {
        "media_type": "image",
        "source_url": "https://commons.wikimedia.org/wiki/File:Gold.jpg",
        "file_url": "https://upload.wikimedia.org/gold.jpg",
        "title": "Gold.jpg",
        "creator": "Archivist",
        "license_name": "CC0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution": "Own work",
    }
    entries = [
        {**base, "scene_index": 0},
        {**base, "scene_index": 0},
        {
            **base,
            "scene_index": 1,
            "file_url": "https://upload.wikimedia.org/gold-2.jpg",
        },
        {
            **base,
            "scene_index": 1,
            "file_url": "https://upload.wikimedia.org/gold-3.jpg",
        },
    ]
    manifest = tmp_path / "assets.json"
    manifest.write_text(json.dumps({"assets": entries}), encoding="utf-8")
    downloads = []

    def fake_download(url, target):
        downloads.append(url)
        target.write_bytes(b"asset")

    provider = CommonsAssetProvider()
    monkeypatch.setattr(provider, "_download", fake_download)
    assets = provider.collect_from_manifest(manifest, scenes, tmp_path / "run")

    assert len(assets) == 4
    assert len(downloads) == 3
    assert assets[0].local_path == assets[1].local_path
    assert (tmp_path / "run" / "rights-manifest.json").exists()


def test_verified_media_download_is_reused_from_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("BTR_ASSET_CACHE_DIR", str(cache_dir))
    first = CommonsAssetProvider()
    first.download_interval_seconds = 0
    monkeypatch.setattr(first.session, "get", lambda *_args, **_kwargs: _DownloadResponse())
    first_target = tmp_path / "first.jpg"
    first._download("https://upload.wikimedia.org/example.jpg", first_target)

    second = CommonsAssetProvider()
    monkeypatch.setattr(
        second.session,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )
    second_target = tmp_path / "second.jpg"
    second._download("https://upload.wikimedia.org/example.jpg", second_target)

    assert second_target.read_bytes() == b"rights-safe-media"
