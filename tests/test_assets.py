from btr_pipeline.assets import CommonsAssetProvider


def test_public_domain_metadata_gets_canonical_status_url():
    assert CommonsAssetProvider._canonical_license_url("Public domain", "") == (
        "https://creativecommons.org/publicdomain/mark/1.0/"
    )


def test_cc_license_without_versioned_url_stays_rejected():
    assert CommonsAssetProvider._canonical_license_url("CC BY-SA", "") == ""


def test_video_query_broadening_keeps_concrete_terms():
    assert CommonsAssetProvider._broaden_video_query(
        "1960s European chicken market archival footage Hamburg"
    ) == "1960s European chicken market Hamburg archive film"
