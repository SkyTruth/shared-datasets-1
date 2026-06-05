from __future__ import annotations

import re
import unittest
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"


NATIVE_TOOL_TESTS = {
    "tests/test_localized_vector_asset.py",
    "tests/test_raster_standards.py",
    "tests/test_wdpa_monthly.py",
    "tests/test_sea_ice_daily.py",
    "tests/test_eamlis_monthly.py",
}


class GeospatialCiTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow(CI_WORKFLOW)

    def test_geospatial_job_runs_all_native_tool_integration_tests(self):
        run = workflow_steps_by_name(self.workflow, "geospatial-integration")[
            "Run geospatial integration tests"
        ]["run"]

        self.assertIn("-e RUN_GDAL_INTEGRATION_TESTS=1", run)
        self.assertIn("--junitxml=test-results/geospatial-pytest.xml", run)
        for test_path in sorted(NATIVE_TOOL_TESTS):
            with self.subTest(test_path=test_path):
                self.assertIn(test_path, run)

    def test_geospatial_change_filter_covers_native_tool_tests_and_sources(self):
        run = workflow_steps_by_name(self.workflow, "geospatial-changes")[
            "Detect geospatial changes"
        ]["run"]
        match = re.search(r"geospatial_pattern='(?P<pattern>[^']+)'", run)

        self.assertIsNotNone(match)
        pattern = re.compile(match.group("pattern"))
        expected_matches = {
            ".github/docker/geospatial-ci.Dockerfile",
            ".github/workflows/ci.yml",
            "ingestion/wdpa_monthly/run.py",
            "scripts/vector_asset.py",
            "scripts/localized_vector_asset.py",
            "scripts/raster_asset.py",
            "scripts/dataset_alerts.py",
            *NATIVE_TOOL_TESTS,
        }
        for path in sorted(expected_matches):
            with self.subTest(path=path):
                self.assertTrue(pattern.match(path), path)
        self.assertFalse(pattern.match("docs/assets/wdpa-marine.md"))


if __name__ == "__main__":
    unittest.main()
