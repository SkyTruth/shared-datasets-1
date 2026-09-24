from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]


class PublishDatasetWorkflowTests(unittest.TestCase):
    def test_only_expected_executor_checkouts_are_present(self):
        workflow = load_workflow(ROOT / ".github/workflows/publish-dataset.yml")
        checkouts = [
            (job_name, step.get("with", {}), step.get("if"))
            for job_name, job in workflow["jobs"].items()
            for step in job.get("steps", [])
            if step.get("uses", "").startswith("actions/checkout@")
        ]
        self.assertEqual(checkouts, [
            ("reviewed_pr_plans", {"ref": "${{ github.workflow_sha }}"}, None),
            ("apply-approved-pr-plans", {"ref": "${{ needs.reviewed_pr_plans.outputs.executor_sha }}"}, None),
        ])

    def test_capture_and_apply_share_exact_artifact_and_executor(self):
        workflow = load_workflow(ROOT / ".github/workflows/publish-dataset.yml")
        self.assertEqual(
            workflow_triggers(workflow)["pull_request"]["types"], ["closed"]
        )
        gate = workflow_steps_by_name(workflow, "reviewed_pr_plans")
        apply = workflow_steps_by_name(workflow, "apply-approved-pr-plans")
        self.assertEqual(
            gate["Check out immutable workflow code"]["with"]["ref"],
            "${{ github.workflow_sha }}",
        )
        self.assertIn(
            "dataset_mutation_authorization.py capture",
            gate["Capture accepted immutable mutation plan"]["run"],
        )
        self.assertNotIn("if", gate["Preserve exact authorization envelope"])
        self.assertIn(
            "needs.reviewed_pr_plans.outputs.executor_sha",
            apply["Check out captured immutable executor"]["with"]["ref"],
        )
        self.assertIn(
            "outputs.artifact_id",
            apply["Download captured authorization"]["with"]["artifact-ids"],
        )
        self.assertEqual(
            workflow["jobs"]["apply-approved-pr-plans"]["environment"],
            "shared-datasets-production",
        )
        names = list(apply)
        self.assertLess(
            names.index("Verify captured authorization"),
            names.index("Authenticate to Google Cloud"),
        )
        self.assertLess(
            names.index("Recheck acceptance immediately before mutation"),
            names.index("Promote approved staged objects"),
        )
        all_runs = "\n".join(step.get("run", "") for step in apply.values())
        self.assertNotIn("event-from-pr", all_runs)
        self.assertNotIn("extract publish", all_runs)
        self.assertNotIn("pulls/", all_runs)
        self.assertIn(
            "--expected-sha256", apply["Verify captured authorization"]["run"]
        )

    def test_existing_execution_order_and_no_direct_dispatch_fallback(self):
        workflow = load_workflow(ROOT / ".github/workflows/publish-dataset.yml")
        apply = workflow_steps_by_name(workflow, "apply-approved-pr-plans")
        names = list(apply)
        required = [
            "Check approved schema compatibility",
            "Promote approved staged objects",
            "Finalize promoted release metadata",
            "Rebuild promoted release index",
            "Delete approved canonical objects",
        ]
        self.assertEqual(
            [names.index(name) for name in required],
            sorted(names.index(name) for name in required),
        )
        self.assertEqual(
            set(workflow_triggers(workflow)["workflow_dispatch"]["inputs"]),
            {"pr_number"},
        )
        self.assertIn(
            "github.actor == 'jonaraphael'", workflow["jobs"]["reviewed_pr_plans"]["if"]
        )

    def test_preview_reads_committed_head_document_without_mutation(self):
        workflow = load_workflow(
            ROOT / ".github/workflows/dataset-breaking-change-alert.yml"
        )
        steps = workflow_steps_by_name(workflow, "alert")
        self.assertEqual(
            steps["Check out trusted repository code"]["with"]["ref"],
            "${{ github.event.pull_request.base.sha }}",
        )
        self.assertIn(
            "dataset_mutation_authorization.py preview",
            steps["Validate checked-in proposed mutation plan"]["run"],
        )
        text = str(steps)
        self.assertNotIn("publish_workflow.py promote", text)
        self.assertNotIn("extract publish", text)
        self.assertIn("GCP_READONLY_SERVICE_ACCOUNT", text)

    def test_mutation_routing_has_no_checkout_or_elevated_permissions(self):
        workflow = load_workflow(ROOT / ".github/workflows/dataset-breaking-change-alert.yml")
        route, alert = (workflow["jobs"][name] for name in ("route", "alert"))
        self.assertEqual(workflow["permissions"], {})
        self.assertEqual(route["permissions"], {"pull-requests": "read"})
        self.assertEqual(len(route["steps"]), 1)
        self.assertEqual(route["steps"][0]["uses"], "actions/github-script@v7")
        self.assertNotIn("secrets.", str(route))
        self.assertNotIn("checkout", str(route))
        self.assertEqual(alert["needs"], "route")
        self.assertEqual(alert["if"], "${{ needs.route.result == 'success' && needs.route.outputs.potential_mutation == 'true' }}")
        preview = workflow_steps_by_name(workflow, "alert")["Validate checked-in proposed mutation plan"]
        self.assertEqual(preview["run"], "python scripts/dataset_mutation_authorization.py preview")
        self.assertNotIn("continue-on-error", preview)
        self.assertNotIn("continue-on-error", alert)


ROUTE_HARNESS = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const outputs = {}, calls = [];
let reads = 0;
const github = { rest: { pulls: {
  get: async request => {
    calls.push(['get', request]);
    reads++;
    if (input.api_error === `get${reads}`) throw new Error('GitHub unavailable');
    return { data: reads === 1 ? input.pr : input.current };
  },
  listFiles: 'listFiles'
} }, paginate: async (method, request) => {
  calls.push(['paginate', method, request]);
  if (input.api_error === 'paginate') throw new Error('GitHub unavailable');
  return input.files;
} };
const run = new (Object.getPrototypeOf(async function() {}).constructor)('github', 'context', 'core', input.script);
run(github, input.context, { setOutput: (key, value) => { outputs[key] = value; } })
  .then(() => process.stdout.write(JSON.stringify({ outputs, calls })))
  .catch(error => process.stdout.write(JSON.stringify({ outputs, calls, error: error.message })));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required to execute the workflow's routing script")
class MutationIntentRoutingTests(unittest.TestCase):
    def fixture(self, *, files=None, body=None):
        if files is None:
            files = [{"filename": "README.md", "status": "modified"}]
        repository = {"id": 100, "full_name": "SkyTruth/shared-datasets-1"}
        pr = {
            "number": 7, "state": "open", "draft": False, "body": body,
            "changed_files": len(files),
            "head": {"sha": "a" * 40, "repo": deepcopy(repository)},
            "base": {"ref": "main", "sha": "b" * 40, "repo": deepcopy(repository)},
        }
        return {
            "pr": pr, "current": deepcopy(pr), "files": files,
            "context": {"repo": {"owner": "SkyTruth", "repo": "shared-datasets-1"},
                        "payload": {"repository": repository, "pull_request": deepcopy(pr)}},
        }

    def route(self, fixture):
        workflow = load_workflow(ROOT / ".github/workflows/dataset-breaking-change-alert.yml")
        fixture["script"] = workflow["jobs"]["route"]["steps"][0]["with"]["script"]
        result = subprocess.run([shutil.which("node"), "-e", ROUTE_HARNESS],
                                input=json.dumps(fixture), text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def assert_route(self, fixture, expected):
        result = self.route(fixture)
        self.assertNotIn("error", result)
        self.assertEqual(result["outputs"], {"potential_mutation": expected})
        self.assertEqual([call[0] for call in result["calls"]], ["get", "paginate", "get"])
        self.assertEqual(result["calls"][1][1:], ["listFiles", {"owner": "SkyTruth", "repo": "shared-datasets-1", "pull_number": 7, "per_page": 100}])

    def assert_refused(self, fixture):
        result = self.route(fixture)
        self.assertIn("error", result)
        self.assertEqual(result["outputs"], {})

    def test_introducing_code_pr_does_not_require_validator_in_base(self):
        self.assert_route(self.fixture(files=[{"filename": path, "status": "added"} for path in (
            "scripts/dataset_mutation_authorization.py", ".github/dataset-plans/README.md",
            "tests/fixtures/dataset-mutation-authorization-v1.json",
        )]), False)

    def test_current_previous_and_later_page_plan_paths_require_full_preview(self):
        plan = ".github/dataset-plans/demo/proposal/abc.json"
        for status in ("added", "modified", "removed", "renamed"):
            with self.subTest(status=status):
                self.assert_route(self.fixture(files=[{"filename": plan, "status": status}]), True)
        self.assert_route(self.fixture(files=[{"filename": "archive/abc.json", "previous_filename": plan, "status": "renamed"}]), True)
        files = [{"filename": f"docs/page-{index}.md"} for index in range(100)]
        self.assert_route(self.fixture(files=[*files, {"filename": plan}]), True)

    def test_body_markers_route_even_malformed_or_unclosed_plans(self):
        for kind in ("publish", "delete"):
            name = f"shared-datasets-{kind}-plan"
            for fence in (name, name.upper(), f"json {name}", f"JSON {name.upper()}"):
                for suffix in ("\n{}\n```", "\n{invalid", "", "\n\n"):
                    with self.subTest(fence=fence, suffix=suffix):
                        self.assert_route(self.fixture(body=f"```{fence}{suffix}"), True)
        self.assert_route(self.fixture(body="```  shared-datasets-publish-plan\n{}"), True)

    def test_incomplete_duplicate_malformed_and_capped_lists_refuse(self):
        cases = [None, [], [None], [{"filename": False}], [{"filename": "README.md", "previous_filename": 9}],
                 [{"filename": "README.md"}, {"filename": "README.md"}]]
        for files in cases:
            with self.subTest(files=files):
                fixture = self.fixture()
                fixture["files"] = files
                if isinstance(files, list) and files:
                    fixture["pr"]["changed_files"] = fixture["current"]["changed_files"] = len(files)
                self.assert_refused(fixture)
        self.assert_refused(self.fixture(files=[{"filename": f"docs/{i}.md"} for i in range(3000)]))
        fixture = self.fixture()
        fixture["pr"]["changed_files"] = True
        self.assert_refused(fixture)

    def test_identity_changes_and_api_failures_never_output_no_mutation(self):
        for target in ("pr", "current"):
            for path, value in (("number", 8), ("state", "closed"), ("draft", True),
                                ("head.sha", "c" * 40), ("head.repo.id", 101),
                                ("base.repo.full_name", "other/repository"), ("base.ref", "other"),
                                ("base.sha", "d" * 40), ("body", {})):
                with self.subTest(target=target, path=path):
                    fixture = self.fixture()
                    owner = fixture[target]
                    parts = path.split(".")
                    for part in parts[:-1]:
                        owner = owner[part]
                    owner[parts[-1]] = value
                    self.assert_refused(fixture)
        for changed in ({"body": "```shared-datasets-publish-plan"}, {"changed_files": 2}):
            fixture = self.fixture()
            fixture["current"].update(changed)
            self.assert_refused(fixture)
        for method in ("get1", "paginate", "get2"):
            with self.subTest(method=method):
                fixture = self.fixture()
                fixture["api_error"] = method
                self.assert_refused(fixture)
