"""A drained signed-state migration cannot be silently undone by an old worker."""
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from scripts import deployment_emission as emissions, slack_incidents as incidents
from test_slack_incidents import historical_state, signed_record


@pytest.mark.parametrize("schema", [incidents.LEGACY_SCHEMA, incidents.PREVIOUS_SCHEMA])
def test_later_signed_legacy_worker_cannot_replace_verified_v3_history(tmp_path, schema):
    modern = signed_record()
    legacy = deepcopy(modern)
    legacy["id"] += 1
    legacy["payload"]["schema"] = schema
    legacy["payload"]["state"] = historical_state(legacy["payload"]["state"], schema)
    api = Mock()
    api.pages.side_effect = lambda path: [legacy, modern] if "?environment=" in path else [{"description": "applied", "state": "success"}]
    with patch.object(emissions, "verify") as verify:
        with pytest.raises(incidents.IncidentError, match="older notification worker"):
            incidents.Ledger(api, {}, tmp_path).load()
    assert [call.args[2]["id"] for call in verify.call_args_list] == [legacy["id"], modern["id"]]


def test_forged_modern_history_is_not_treated_as_a_verified_migration(tmp_path):
    modern = signed_record()
    legacy = deepcopy(modern)
    legacy["id"] += 1
    legacy["payload"]["schema"] = incidents.PREVIOUS_SCHEMA
    legacy["payload"]["state"] = historical_state(legacy["payload"]["state"], incidents.PREVIOUS_SCHEMA)
    api = Mock()
    api.pages.side_effect = lambda path: [legacy, modern] if "?environment=" in path else [{"description": "applied", "state": "success"}]
    def verify(_api, _repository, record, *_args, **_kwargs):
        if record["id"] == modern["id"]:
            raise emissions.EmissionError("invalid historical signature")
    with patch.object(emissions, "verify", side_effect=verify):
        with pytest.raises(emissions.EmissionError, match="historical signature"):
            incidents.Ledger(api, {}, tmp_path).load()


def test_duplicate_checkpoint_id_cannot_choose_inconsistent_schema_bytes(tmp_path):
    modern = signed_record()
    conflicting = deepcopy(modern)
    conflicting["payload"]["schema"] = incidents.PREVIOUS_SCHEMA
    api = Mock()
    api.pages.return_value = [modern, conflicting]
    with patch.object(emissions, "verify") as verify:
        with pytest.raises(incidents.IncidentError, match="inconsistent historical bytes"):
            incidents.Ledger(api, {}, tmp_path).load()
    verify.assert_not_called()
