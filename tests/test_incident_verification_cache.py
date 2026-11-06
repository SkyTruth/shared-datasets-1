"""Only a worker's in-memory batch may reuse a verified signature statement."""
from unittest import mock

import pytest

from scripts import deployment_emission as emissions, slack_incidents as incidents
from test_operational_safety_check import OperationalAPI, acked_job_state, signed_transport
from test_operational_safety_check import receipt_workspace  # noqa: F401 - autouse fixture


@pytest.mark.parametrize('change', [None, 'payload', 'status'])
def test_batch_reuses_exact_statement_but_rejects_changed_checkpoint(tmp_path, change):
    api = OperationalAPI()
    api.install_state(acked_job_state(api))
    ledger = incidents.Ledger(api, api.current, tmp_path)
    with signed_transport(api) as crypto:
        ledger.verify(api.records[0])
        if change:
            if change == 'payload':
                api.records[0]['payload']['state']['label'] = 'changed'
            else:
                api.statuses[api.records[0]['id']][0]['id'] += 1
            with pytest.raises(emissions.EmissionError):
                ledger.verify(api.records[0])
            assert crypto.call_count == 2
        else:
            ledger.verify(api.records[0])
            assert crypto.call_count == 1
    assert api.posts == []


def test_a_new_worker_cannot_reuse_a_prior_workers_signature_verification(tmp_path):
    api = OperationalAPI()
    api.install_state(acked_job_state(api))
    with signed_transport(api):
        incidents.Ledger(api, api.current, tmp_path).verify(api.records[0])
    with mock.patch.object(emissions, 'verified_output', side_effect=RuntimeError('signature unavailable')):
        with pytest.raises(RuntimeError, match='signature unavailable'):
            incidents.Ledger(api, api.current, tmp_path).verify(api.records[0])
    assert api.posts == []
