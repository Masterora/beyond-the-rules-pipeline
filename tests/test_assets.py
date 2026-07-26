from btr_pipeline.assets import CommonsAssetProvider


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
