"""Serialized atomic access filters; service acceptance stays a rollout check."""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path
import re

import pytest

from scripts import operational_log_views as views
from scripts.terraform_target_contracts import validate_operational_views

ROOT = Path(__file__).resolve().parents[1]
PAIRS = {
    'run-stderr': ('cloud_run_job', 'run.googleapis.com/stderr'),
    'run-activity': ('cloud_run_job', 'cloudaudit.googleapis.com/activity'),
    'run-data-access': ('cloud_run_job', 'cloudaudit.googleapis.com/data_access'),
    'scheduler-executions': ('cloud_scheduler_job', 'cloudscheduler.googleapis.com/executions'),
}
ATOM_FILTER = re.compile(r'resource.labels.project_id="([^"]+)" AND resource.labels.location="([^"]+)" AND resource.type="([^"]+)" AND log_id\("([^"]+)"\)')


def serialized_views():
    # The lint gate obtains this shape from pinned Terraform's actual console,
    # not a Python substitute. Unit cases independently pin the approved pairs.
    return json.loads(json.dumps({
        bucket + '-' + family: {'bucket': bucket, 'name': 'shared-datasets-operations-' + family,
                                 'filter': views.filter_for(family)}
        for bucket in views.BUCKETS for family in PAIRS
    }))


def test_registered_view_union_is_exact_and_each_api_filter_is_one_conjunction():
    value = serialized_views()
    actual = validate_operational_views(value, ROOT)
    assert len(actual) == 8 and set(actual) == set(views.VIEWS)
    assert {family: tuple(row[key] for key in ('resource_type', 'log_id')) for family, row in views.FAMILIES.items()} == PAIRS
    for bucket in ('_Default', '_Required'):
        predicates = [ATOM_FILTER.fullmatch(row['filter']).groups() for row in value.values() if row['bucket'] == bucket]
        families = {pair[1] for pair in PAIRS.values()} | {'run.googleapis.com/stdout', 'cloudaudit.googleapis.com/system_event', 'unrelated/log'}
        for project_id, region, resource_type, log_id in product(
            ('shared-datasets-1', 'other-project', None), ('us-central1', 'europe-west1', None),
            ('cloud_run_job', 'cloud_scheduler_job', 'cloud_run_revision', 'gce_instance'), families,
        ):
            candidate = (project_id, region, resource_type, log_id)
            expected = project_id == 'shared-datasets-1' and region == 'us-central1' and (resource_type, log_id) in PAIRS.values()
            assert (candidate in predicates) == expected


@pytest.mark.parametrize('filter', [
    # Exact OR-of-AND structure rejected by both protected creates in
    # 39714035844/1, despite one parenthesized grouping level.
    '''(resource.labels.project_id="shared-datasets-1" AND resource.labels.location="us-central1" AND resource.type="cloud_run_job" AND log_id("run.googleapis.com/stderr"))
OR (resource.labels.project_id="shared-datasets-1" AND resource.labels.location="us-central1" AND resource.type="cloud_run_job" AND log_id("cloudaudit.googleapis.com/activity"))
OR (resource.labels.project_id="shared-datasets-1" AND resource.labels.location="us-central1" AND resource.type="cloud_run_job" AND log_id("cloudaudit.googleapis.com/data_access"))
OR (resource.labels.project_id="shared-datasets-1" AND resource.labels.location="us-central1" AND resource.type="cloud_scheduler_job" AND log_id("cloudscheduler.googleapis.com/executions"))''',
    'resource.type="cloud_run_job"',
    views.filter_for('run-stderr').replace('cloud_run_job', 'cloud_scheduler_job'),
    views.filter_for('run-stderr').replace('shared-datasets-1', 'other-project'),
    views.filter_for('run-stderr').replace('us-central1', 'europe-west1'),
    views.filter_for('run-stderr').replace('run.googleapis.com/stderr', 'run.googleapis.com/stdout'),
    '(' + views.filter_for('run-stderr') + ')',
    views.filter_for('run-stderr') + ' AND NOT severity="INFO"',
])
def test_serialized_filters_reject_observed_failure_and_scope_changes(filter):
    value = serialized_views()
    value['_Default-run-stderr']['filter'] = filter
    with pytest.raises(ValueError, match='flat conjunction'):
        validate_operational_views(value, ROOT)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'foreign-bucket', 'legacy-name', 'cross-bucket-key', 'extra-field'])
def test_serialized_view_inventory_is_exact(mutation):
    value = deepcopy(serialized_views())
    if mutation == 'missing':
        value.pop('_Required-scheduler-executions')
    elif mutation == 'extra':
        value['unexpected'] = value['_Default-run-stderr']
    elif mutation == 'foreign-bucket':
        value['_Default-run-stderr']['bucket'] = 'foreign'
    elif mutation == 'legacy-name':
        value['_Default-run-stderr']['name'] = 'shared-datasets-operations'
    elif mutation == 'cross-bucket-key':
        value['_Default-run-stderr']['bucket'] = '_Required'
    else:
        value['_Default-run-stderr']['log_id'] = 'unreviewed-duplicate'
    with pytest.raises(ValueError):
        validate_operational_views(value, ROOT)


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'extra', 'swapped-resource', 'wrong-log', 'malformed-row', 'extra-field'])
def test_reviewed_registry_rejects_ambiguous_or_unapproved_pairs(tmp_path, mutation):
    raw = (ROOT / views.CONTRACT).read_text()
    value = json.loads(raw)
    if mutation == 'duplicate':
        raw = '{"run-stderr":' + json.dumps(value['run-stderr']) + ',' + raw[1:]
    else:
        if mutation == 'missing':
            value.pop('run-stderr')
        elif mutation == 'extra':
            value['stdout'] = value['run-stderr']
        elif mutation == 'swapped-resource':
            value['run-stderr']['resource_type'] = 'cloud_scheduler_job'
        elif mutation == 'wrong-log':
            value['run-stderr']['log_id'] = 'run.googleapis.com/stdout'
        elif mutation == 'malformed-row':
            value['run-stderr'] = None
        else:
            value['run-stderr']['extra'] = 'unreviewed'
        raw = json.dumps(value)
    (tmp_path / 'catalog').mkdir()
    (tmp_path / views.CONTRACT).write_text(raw)
    with pytest.raises(ValueError):
        views.load_families(tmp_path)
