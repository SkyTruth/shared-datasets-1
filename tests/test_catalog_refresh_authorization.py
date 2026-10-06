"""The catalog refresh consumes authorization and independently checks mutation success."""

from unittest.mock import Mock

import pytest

from scripts.catalog_refresh_authorization import verify_completed_mutation
from scripts.dataset_mutation_authorization import Error


def envelope():
    return {"repository": {"full_name": "SkyTruth/shared-datasets-1"},
            "trusted_executor_sha": "a" * 40, "pr_number": 208, "outcome": "mutation",
            "source_run": {"id": 123, "run_attempt": 2}}


def job(**changes):
    return {"name": "publish / Apply approved PR mutation plans (PR #208)",
            "status": "completed", "conclusion": "success", **changes}


def verify(jobs, **changes):
    api = Mock()
    api.pages.return_value = jobs
    verify_completed_mutation(api, {**envelope(), **changes}, "a" * 40, 208)
    api.pages.assert_called_once_with(
        "repos/SkyTruth/shared-datasets-1/actions/runs/123/attempts/2/jobs?per_page=100", field="jobs")


def test_exact_successful_publication_can_refresh_catalog():
    verify([job(), job(name="publish / Apply approved PR mutation plans (PR #209)", conclusion="failure")])


@pytest.mark.parametrize("jobs", [[], [job(), job()], [job(conclusion="failure")],
    [job(conclusion="cancelled")], [job(conclusion="skipped")],
    [job(status="in_progress", conclusion=None)],
    [job(name="publish / Apply approved PR mutation plans (PR #209)")]])
def test_incomplete_or_another_publication_cannot_refresh_catalog(jobs):
    with pytest.raises(Error):
        verify(jobs)


@pytest.mark.parametrize("changes", [{"outcome": "noop"}, {"pr_number": 209},
                                      {"trusted_executor_sha": "b" * 40}])
def test_catalog_inputs_cannot_change_the_verified_authorization(changes):
    with pytest.raises(Error):
        verify([job()], **changes)
