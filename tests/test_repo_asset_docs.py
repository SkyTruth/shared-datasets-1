from __future__ import annotations

import unittest
from pathlib import Path

from scripts import catalog_docs


REPO_ROOT = Path(__file__).resolve().parents[1]


class RepoAssetDocsTests(unittest.TestCase):
    """Validate the repository's real asset docs, not fixtures.

    This is the pre-merge gate for the frontmatter schema: every doc under
    docs/assets/ must parse, use only canonical frontmatter/admission field
    names, and match the generated catalog CSV and index.
    """

    def test_real_asset_docs_validate_and_generated_outputs_are_current(self):
        categories = catalog_docs.load_categories(REPO_ROOT / "catalog/categories.yaml")
        docs = catalog_docs.read_asset_docs(
            docs_dir=REPO_ROOT / "docs/assets",
            categories=categories,
        )
        self.assertGreater(len(docs), 0)
        errors, _warnings = catalog_docs.check_outputs(
            docs=docs,
            catalog_path=REPO_ROOT / "catalog/shared-datasets-catalog.csv",
            index_path=REPO_ROOT / "docs/assets/index.md",
            bucket=catalog_docs.DEFAULT_BUCKET,
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
