from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from scripts.check_geospatial_test_results import REQUIRED_TESTS, check_results
from scripts.ci_contract import NATIVE_TESTS, select_suites
from scripts.ci_preflight import suite_commands

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
RESULT_CHECKER = REPO_ROOT / "scripts/check_geospatial_test_results.py"
TEST_WORK_ROOT = Path(
    os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1")
) / "_scratch/native-ci-contract-tests"


NATIVE_TOOL_TESTS = {
    "tests/test_feature_metadata_localization.py",
    "tests/test_raster_standards.py",
    "tests/test_wdpa_monthly.py",
    "tests/test_wdpa_disk_processing.py",
    "tests/test_sea_ice_daily.py",
    "tests/test_eamlis_monthly.py",
}


class GeospatialCiTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow(CI_WORKFLOW)

    def test_complete_benchmark_is_manual_read_only_and_resource_constrained(self):
        inputs = workflow_triggers(self.workflow)["workflow_dispatch"]["inputs"]
        self.assertIs(inputs["wdpa_full_benchmark"]["default"], False)
        self.assertEqual(inputs["wdpa_benchmark_fraction"]["default"], "1")
        self.assertEqual(inputs["wdpa_benchmark_fraction"]["options"], ["1", "0.001"])
        self.assertIs(inputs["wdpa_inputs_probe"]["default"], False)
        job = self.workflow["jobs"]["wdpa-full-benchmark"]
        self.assertIn("github.event_name == 'workflow_dispatch'", job["if"])
        self.assertIn("inputs.wdpa_full_benchmark", job["if"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(job["needs"], ["wdpa-benchmark-image", "wdpa-small-smoke"])
        self.assertEqual(job["env"]["WDPA_BENCHMARK_REPLAY"], "1")
        steps = workflow_steps_by_name(self.workflow, "wdpa-full-benchmark")
        run = steps["Run marine WDPA with 4 CPU and 8 GiB"]["run"]
        self.assertIn("--asset wdpa-marine", run)
        self.assertIn("--cpus=4 --memory=8g --memory-swap=8g", run)
        self.assertIn("CLOUD_RUN_EXECUTION=wdpa-benchmark", run)
        self.assertIn("--translation-sources", run)
        self.assertIn('--fraction "$WDPA_BENCHMARK_FRACTION"', run)
        self.assertIn("wdpa-inputs:/inputs:ro", run)
        self.assertNotIn("SNAPSHOT_READ_TOKEN", run)
        self.assertNotIn("--genesis", run)
        upload = steps["Upload measurements only"]["with"]
        self.assertEqual(upload["path"], "${{ runner.temp }}/wdpa-reports/")
        self.assertIn("compatibility.log", steps["Retain report if a replay failed"]["run"])
        self.assertIn("wdpa-marine-benchmark-reports", upload["name"])
        image_steps = workflow_steps_by_name(self.workflow, "wdpa-benchmark-image")
        self.assertIn("docker save", image_steps["Build the deployment image once"]["run"])
        image_artifact = image_steps["Share the deployment image with staged checks"]["with"]["name"]
        self.assertEqual(steps["Download the shared deployment image"]["with"]["name"], image_artifact)
        self.assertIn("containerimage.config.digest", steps["Load and verify the identical deployment image"]["run"])
        self.assertIn('[[ "$actual" == "$expected" ]]', steps["Load and verify the identical deployment image"]["run"])
        self.assertIn("fallocate -l 100G", steps["Provision a 100 GiB disk scratch filesystem"]["run"])

    def test_benchmark_uses_public_frozen_inputs_without_credentials(self):
        steps = workflow_steps_by_name(self.workflow, "wdpa-full-benchmark")
        run = steps["Download reviewed public frozen inputs without credentials"]["run"]
        self.assertIn("scripts/download_public_wdpa_benchmark.py", run)
        self.assertIn("docs/wdpa-processing-public-inputs.json", run)
        self.assertNotIn("secrets.", str(self.workflow["jobs"]["wdpa-full-benchmark"]))
        self.assertIn("!inputs.wdpa_inputs_probe", steps["Run marine WDPA with 4 CPU and 8 GiB"]["if"])
        self.assertIn("--cpus=4 --memory=8g --memory-swap=8g", steps["Measure input preparation only"]["run"])
        self.assertIn("wdpa_input_memory_probe.py:/app/scripts/wdpa_input_memory_probe.py:ro", steps["Measure input preparation only"]["run"])

    def test_geospatial_job_runs_shared_native_contract(self):
        job = self.workflow['jobs']['geospatial-integration']
        self.assertEqual(job['needs'], 'geospatial-changes')
        self.assertEqual(job['if'], "needs.geospatial-changes.outputs.should_run == 'true'")
        run = workflow_steps_by_name(self.workflow, 'geospatial-integration')['Run shared native validation']['run']
        self.assertIn('scripts/ci_preflight.py run-suite', run)
        self.assertIn('--suite geospatial-integration', run)
        self.assertNotIn('continue-on-error', job)
        commands = suite_commands('geospatial-integration', REPO_ROOT, {'base':'base','tested_sha':'head'}, Path('/evidence'))
        arguments = next(args for args, _ in commands if 'pytest' in args)
        self.assertTrue(NATIVE_TOOL_TESTS <= set(arguments))
        self.assertIn('xfail_strict=true', arguments)
        self.assertIn('tests/test_translation_local_io.py', arguments)
        self.assertIn(['gdal_calc.py', '--help'], [args for args, _ in commands])

    def test_shared_change_classifier_covers_native_dependencies(self):
        for path in [*NATIVE_TESTS, '.github/docker/geospatial-ci.Dockerfile', '.github/workflows/ci.yml', 'pyproject.toml', 'uv.lock', 'ingestion/wdpa_monthly/run.py', 'scripts/vector_asset.py', 'scripts/release_feature_model.py', 'scripts/dataset_alerts.py']:
            with self.subTest(path=path):
                self.assertIn('geospatial-integration', select_suites([path])[0])
        self.assertNotIn('geospatial-integration', select_suites(['docs/assets/wdpa-marine.md'])[0])


class GeospatialResultTests(unittest.TestCase):
    def setUp(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT)
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.report = self.work / "results.xml"

    def passing_report(self):
        root = ElementTree.Element("testsuites")
        suite = ElementTree.SubElement(root, "testsuite")
        for classname, name in REQUIRED_TESTS:
            ElementTree.SubElement(suite, "testcase", classname=classname, name=name)
        return root, suite

    def write_report(self, root):
        ElementTree.ElementTree(root).write(self.report, encoding="utf-8")

    def run_checker(self):
        return subprocess.run(
            [sys.executable, str(RESULT_CHECKER), str(self.report)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_all_required_tests_pass_with_additional_unrelated_results(self):
        root, suite = self.passing_report()
        ElementTree.SubElement(suite, "testcase", classname="other", name="test_other")
        self.write_report(root)
        check_results(self.report)
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"All {len(REQUIRED_TESTS)} required native geospatial tests passed", result.stdout)

    def test_each_required_test_must_be_present_once_and_pass(self):
        for index, (classname, name) in enumerate(REQUIRED_TESTS):
            for defect in ("missing", "renamed", "duplicate", "skipped", "failure", "error"):
                with self.subTest(name=name, defect=defect):
                    root, suite = self.passing_report()
                    case = suite[index]
                    if defect == "missing":
                        suite.remove(case)
                    elif defect == "renamed":
                        case.set("name", "test_other")
                    elif defect == "duplicate":
                        suite.append(copy.deepcopy(case))
                    else:
                        ElementTree.SubElement(case, defect)
                    self.write_report(root)
                    with self.assertRaisesRegex(ValueError, re.escape(f"{classname}.{name}")):
                        check_results(self.report)

    def test_wrong_classname_cannot_satisfy_required_identity(self):
        root, suite = self.passing_report()
        suite[0].set("classname", "test_raster_standards.RasterCogIntegrationTests")
        self.write_report(root)
        with self.assertRaisesRegex(ValueError, "expected exactly one result, found 0"):
            check_results(self.report)

    def test_claimed_summary_totals_do_not_replace_test_results(self):
        self.report.write_text('<testsuite tests="94" failures="0" skipped="0"/>')
        result = self.run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.count("expected exactly one result, found 0"), len(REQUIRED_TESTS))

    def test_invalid_or_missing_report_fails_closed(self):
        for content in (None, "", "<testsuite>"):
            with self.subTest(content=content):
                if content is not None:
                    self.report.write_text(content)
                result = self.run_checker()
                self.assertEqual(result.returncode, 1)
                self.assertIn("Native geospatial test requirement failed", result.stderr)

    def test_real_pytest_reports_distinguish_pass_skip_xfail_failure_and_error(self):
        # These miniature suites exercise pytest's XML contract without native binaries.
        for outcome, pytest_exit in (
            ("passed", 0), ("skipped", 0), ("xfailed", 0),
            ("xpassed", 1), ("failure", 1), ("error", 1),
        ):
            with self.subTest(outcome=outcome):
                (self.work / "tests").mkdir(exist_ok=True)
                (self.work / "pytest.ini").write_text("[pytest]\n")
                for index, (classname, name) in enumerate(REQUIRED_TESTS):
                    if classname.count(".") == 1:
                        path = self.work / f"{classname.replace('.', '/')}.py"
                        path.write_text(f"def {name}():\n    pass\n")
                        continue
                    module, class_name = classname.rsplit(".", 1)
                    body = "pass"
                    decorator = ""
                    setup = ""
                    if index == 0:
                        body = {
                            "passed": "pass",
                            "skipped": "pytest.skip('tool unavailable')",
                            "xfailed": "pytest.xfail('known failure')",
                            "xpassed": "pass",
                            "failure": "assert False",
                            "error": "pass",
                        }[outcome]
                        if outcome == "xpassed":
                            decorator = "    @pytest.mark.xfail(reason='known failure')\n"
                        if outcome == "error":
                            setup = "    def setUp(self):\n        raise RuntimeError('setup failed')\n"
                    path = self.work / f"{module.replace('.', '/')}.py"
                    path.write_text(
                        "import pytest\nimport unittest\n\n"
                        f"class {class_name}(unittest.TestCase):\n"
                        f"{setup}{decorator}    def {name}(self):\n        {body}\n"
                    )
                run = subprocess.run(
                    [
                        sys.executable, "-m", "pytest", "-q", "-c", "pytest.ini",
                        "--rootdir=.", "tests", "-o", "xfail_strict=true",
                        f"--junitxml={self.report}",
                    ],
                    cwd=self.work,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(run.returncode, pytest_exit, run.stdout + run.stderr)
                cases = list(ElementTree.parse(self.report).iter("testcase"))
                self.assertEqual(
                    {(case.get("classname"), case.get("name")) for case in cases},
                    set(REQUIRED_TESTS),
                )
                result = self.run_checker()
                self.assertEqual(result.returncode, 0 if outcome == "passed" else 1, result.stderr)


if __name__ == "__main__":
    unittest.main()
