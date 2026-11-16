"""Complete bounded transport never converts API faults into absent proof."""
from unittest.mock import Mock

import pytest

from scripts.dataset_mutation_authorization import GitHub
from scripts.monitoring_slack_sweep_api import BoundedGitHub, GET_LIMIT


def test_complete_pagination_and_budget(monkeypatch):
    read = Mock(side_effect=[[{'id': i} for i in range(100)], [{'id': 101}]])
    monkeypatch.setattr(GitHub, 'get', read)
    api = BoundedGitHub()
    assert len(api.pages('repos/a/b/deployments?per_page=100')) == 101
    assert api.remaining == GET_LIMIT - 2
    assert read.call_args_list[-1].args[0].endswith('per_page=100&page=2')
    api.remaining = 0
    with pytest.raises(ValueError, match='budget exhausted'):
        api.get('repos/a/b')


@pytest.mark.parametrize('bad', [[], {'total_count': 101, 'jobs': [{'id': 1}]},
                              {'total_count': 1, 'jobs': ['corrupt']}])
def test_named_page_corruption_fails(monkeypatch, bad):
    monkeypatch.setattr(GitHub, 'get', Mock(return_value=bad))
    with pytest.raises(ValueError):
        BoundedGitHub().pages('repos/a/b/jobs', field='jobs')


def test_api_failure_propagates(monkeypatch):
    monkeypatch.setattr(GitHub, 'get', Mock(side_effect=OSError('read unavailable')))
    with pytest.raises(OSError):
        BoundedGitHub().pages('repos/a/b/deployments')


def test_budget_exhaustion_inside_enumeration_never_accepts_partial_history(monkeypatch):
    read = Mock(return_value=[{'id': i} for i in range(100)])
    monkeypatch.setattr(GitHub, 'get', read)
    api = BoundedGitHub()
    api.remaining = 2
    with pytest.raises(ValueError, match='GitHub read budget exhausted'):
        api.pages('repos/a/b/deployments')
    assert read.call_count == 2
