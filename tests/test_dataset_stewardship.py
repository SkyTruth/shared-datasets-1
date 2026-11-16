from datetime import date

import pytest

from scripts import dataset_stewardship as policy
from scripts import catalog_docs
from tests.test_catalog_docs import DOC


@pytest.mark.parametrize("steward", ["", "SkyTruth", "Shared datasets maintainers", None])
def test_organization_names_do_not_establish_personal_accountability(steward):
    with pytest.raises(ValueError, match="accountable person"):
        policy.validate(steward, "daily", None)


@pytest.mark.parametrize("cadence", ["manual", "annual", "static", "ad hoc"])
def test_unscheduled_assets_need_a_review_date(cadence):
    with pytest.raises(ValueError, match="review_by is required"):
        policy.validate("Jonathan Raphael (@jonaraphael)", cadence, "")


@pytest.mark.parametrize("review_by", ["2026-02-30", "20261115", "sometime next year"])
def test_review_dates_are_real_and_unambiguous(review_by):
    with pytest.raises(ValueError):
        policy.validate("Jonathan <steward@skytruth.org>", "manual", review_by)


def test_due_review_is_not_a_claim_that_static_source_data_changed():
    row = {"asset_slug": "historic-asset", "status": "active", "steward": "@jonaraphael", "update_cadence": "manual", "review_by": "2026-11-15"}
    assert policy.review_problems([row], today=date(2026, 11, 15)) == []
    assert policy.review_problems([row], today=date(2026, 11, 16)) == [
        "historic-asset: stewardship review overdue since 2026-11-15; contact @jonaraphael"]
    assert policy.review_problems([{**row, "status": "retired"}], today=date(2027, 11, 16)) == []
    assert policy.review_problems([{**row, "update_cadence": "daily", "review_by": ""}], today=date(2027, 11, 16)) == []


def test_catalog_roundtrip_keeps_explicit_review_date_instead_of_advancing_it(tmp_path):
    path = tmp_path / 'example-asset.md'
    path.write_text(DOC.replace('2027-11-15', '2020-01-01'))
    categories = catalog_docs.load_categories(catalog_docs.Path('catalog/categories.yaml'))
    doc = catalog_docs.read_asset_docs(docs_dir=tmp_path, categories=categories)[0]
    rendered = catalog_docs.render_asset_doc(doc)
    path.write_text(rendered)
    reread = catalog_docs.read_asset_docs(docs_dir=tmp_path, categories=categories)[0]
    assert reread.metadata['review_by'] == '2020-01-01'
    row = catalog_docs.catalog_row(reread.metadata, catalog_docs.DEFAULT_BUCKET)
    assert row['steward'] == 'Jonathan Raphael (@jonaraphael)'
    assert policy.review_problems([row], today=date(2026, 11, 15))
