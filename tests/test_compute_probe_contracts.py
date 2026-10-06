"""API caller prerequisites are distinct from actual-resource operation authority."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from scripts import cdn_plan_readiness as cdn
from scripts import deployment_permissions as live
from scripts.terraform_plan_permissions import PROJECT_URL, plan_checks, probe_call_checks

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/ci_failures'
FAILURE = json.loads((FIXTURES / 'pmtiles_compute_probe_403.json').read_text())
API = json.loads((FIXTURES / 'compute_probe_call_contract.json').read_text())


def adoption_plan(*, complete=False, importing=True):
    existing = FAILURE['existing_role']
    before = {
        'project': 'shared-datasets-1', 'role_id': existing['name'].rsplit('/', 1)[1],
        'id': existing['name'], 'name': existing['name'], 'title': existing['title'],
        'description': existing['description'], 'stage': existing['stage'], 'deleted': False,
        'permissions': sorted(existing['includedPermissions'] + (list(cdn.PROBE_PERMISSIONS) if complete else [])),
    }
    after = {**before, 'permissions': sorted(cdn.ROLE_PERMISSIONS)}
    change = {'actions': ['no-op'] if complete else ['update'], 'before': before, 'after': after, 'after_unknown': {}}
    if importing:
        change['importing'] = {'id': existing['name']}
    return {'format_version': '1.2', 'terraform_version': '1.8.5', 'resource_changes': [{
        'address': cdn.ROLE_ADDRESS, 'mode': 'managed', 'type': 'google_project_iam_custom_role', 'change': change,
    }]}


class ProbeCallContractTests(unittest.TestCase):
    def test_complete_primary_api_matrix_has_exact_global_and_regional_methods(self):
        self.assertEqual(len(API['families']), 15)
        for family in API['families']:
            with self.subTest(family=family['family']):
                actual = probe_call_checks([(family['endpoint'], ('operation.fixture',))])
                expected = [(PROJECT_URL, tuple(family['probe_call_permissions']))] if family['probe_call_permissions'] else []
                self.assertEqual(actual, expected)
        methods = {method['id'] for method in API['compute_discovery']['methods']}
        self.assertEqual(methods, {family['discovery_method'] for family in API['families'] if family['family'].startswith('compute.')})
        self.assertTrue(all(method['httpMethod'] == 'POST' and method['path'].endswith('/testIamPermissions') for method in API['compute_discovery']['methods']))
        support = API['iam_custom_role_support']
        self.assertTrue(support['complete'])
        self.assertFalse(support['not_found'])
        self.assertEqual(support['found']['compute.urlMaps.list']['customRolesSupportLevel'], 'TESTING')
        self.assertEqual(API['iam_enum_default']['omitted_customRolesSupportLevel'], 'SUPPORTED (enum value 0)')

    def test_verified_historical_nochange_failure_is_prevented_before_compute_call(self):
        value = {'format_version': '1.2', 'resource_changes': [{
            'address': 'google_compute_url_map.pmtiles_cdn', 'type': 'google_compute_url_map',
            'change': {'actions': ['no-op'], 'after': {'project': 'shared-datasets-1', 'name': 'shared-datasets-pmtiles-cdn'}},
        }]}
        required = plan_checks(value, project_number='123456789', target='pmtiles-cdn')
        self.assertEqual(required, [(FAILURE['endpoint'], tuple(FAILURE['operation_permissions']))])
        self.assertNotIn(cdn.PROBE_PERMISSION, FAILURE['existing_role']['includedPermissions'])
        self.assertIsNone(FAILURE['historical_error_body'])
        calls = []
        def probe(url, permissions):
            calls.append((url, permissions))
            if url != PROJECT_URL:
                raise AssertionError('403-producing Compute request must not be attempted')
            return [cdn.PROBE_PERMISSION]
        with self.assertRaisesRegex(RuntimeError, 'permission-probe call.*compute.urlMaps.list'):
            live.verify_checks(required, probe, attempts=1)
        self.assertEqual(calls, [(PROJECT_URL, (cdn.PROBE_PERMISSION,))])

    def test_probe_call_success_never_satisfies_missing_actual_resource_permission(self):
        calls = []
        def probe(url, permissions):
            calls.append((url, permissions))
            return [] if url == PROJECT_URL else list(permissions)
        with self.assertRaisesRegex(RuntimeError, 'resource operation.*compute.urlMaps.invalidateCache'):
            live.verify_checks([(FAILURE['endpoint'], ('compute.urlMaps.invalidateCache',))], probe, attempts=1)
        self.assertEqual(calls, [(PROJECT_URL, ('compute.urlMaps.list',)), (FAILURE['endpoint'], ('compute.urlMaps.invalidateCache',))])

    def test_existing_catalog_routing_update_has_complete_probe_and_operation_authority(self):
        # Use the configured global backend bucket and a representative catalog
        # route addition, rather than only today's no-change cache operation.
        source = (ROOT / 'terraform/envs/prod/pmtiles_cdn.tf').read_text()
        mode = FAILURE['production_routing_contract']
        self.assertEqual(mode['effective_mode'], 'cdn')
        self.assertEqual(mode['variable_default'], 'redirect')
        self.assertFalse(mode['workflow_serving_mode_override'])
        self.assertEqual(cdn.routing_contract_errors(ROOT), [])
        self.assertIn('service = google_compute_backend_bucket.pmtiles_cdn.self_link', source)
        self.assertIn('name        = "shared-datasets-pmtiles-cdn"', source)
        required = plan_checks(FAILURE['normal_routing_update_plan'], project_number='123456789', target='pmtiles-cdn')
        self.assertEqual(probe_call_checks(required), [(PROJECT_URL, ('compute.backendBuckets.list', 'compute.urlMaps.list'))])
        bucket_url = FAILURE['endpoint'].replace('/urlMaps/', '/backendBuckets/')
        self.assertEqual(dict(required)[bucket_url], ('compute.backendBuckets.use',))
        self.assertEqual(set(dict(required)[FAILURE['endpoint']]), {'compute.urlMaps.get', 'compute.urlMaps.update', 'compute.urlMaps.invalidateCache'})
        calls = []
        def probe(url, permissions):
            calls.append((url, permissions))
            return sorted(set(permissions) - cdn.ROLE_PERMISSIONS)
        live.verify_checks(required, probe, attempts=1)
        self.assertEqual(calls[0], (PROJECT_URL, ('compute.backendBuckets.list', 'compute.urlMaps.list')))
        self.assertEqual(calls[1:], required)
        for withheld in ('compute.backendBuckets.use', 'compute.urlMaps.update'):
            with self.subTest(withheld=withheld), self.assertRaisesRegex(RuntimeError, withheld):
                live.verify_checks(required, lambda url, permissions: sorted(set(permissions) - (cdn.ROLE_PERMISSIONS - {withheld})), attempts=1)

    def test_old_sole_urlmap_list_fix_cannot_pass_the_routing_probe_contract(self):
        required = plan_checks(FAILURE['normal_routing_update_plan'], project_number='123456789', target='pmtiles-cdn')
        incomplete = cdn.ORIGINAL_PERMISSIONS | {'compute.urlMaps.list'}
        probe = Mock(side_effect=lambda url, permissions: sorted(set(permissions) - incomplete))
        with self.assertRaisesRegex(RuntimeError, 'permission-probe call.*compute.backendBuckets.list'):
            live.verify_checks(required, probe, attempts=1)
        self.assertEqual(probe.call_count, 1)

    def test_all_selected_compute_prerequisites_precede_unchanged_resource_checks(self):
        required = [(family['endpoint'], ('operation.fixture',)) for family in API['families'][:5]]
        probe = Mock(return_value=[])
        live.verify_checks(required, probe, attempts=1)
        self.assertEqual(probe.call_args_list[0].args, (PROJECT_URL, tuple(sorted(family['probe_call_permissions'][0] for family in API['families'][:5]))))
        self.assertEqual([call.args for call in probe.call_args_list[1:]], required)

    def test_readonly_propagation_is_bounded_separately_before_actual_probe(self):
        probe = Mock(side_effect=[['compute.urlMaps.list'], [], []])
        pause = Mock()
        live.verify_checks([(FAILURE['endpoint'], ('compute.urlMaps.invalidateCache',))], probe, attempts=2, pause=pause)
        pause.assert_called_once_with(10)
        self.assertEqual(probe.call_count, 3)
        self.assertEqual([call.args[0] for call in probe.call_args_list], [PROJECT_URL, PROJECT_URL, FAILURE['endpoint']])

    def test_unknown_or_foreign_compute_method_scope_and_kind_fail_closed(self):
        endpoint = FAILURE['endpoint']
        for invalid in (
            endpoint.replace('/projects/shared-datasets-1/', '/projects/foreign/'),
            endpoint.replace('/global/', '/regions/us-east1/'),
            endpoint.replace('/urlMaps/', '/disks/'),
            endpoint.replace('/testIamPermissions', '/getIamPolicy'),
            endpoint.replace('/compute/v1/', '/compute/beta/'),
            endpoint.replace('/global/', '/regions/us-central1/'),
        ):
            with self.subTest(endpoint=invalid), self.assertRaisesRegex(ValueError, 'unsupported Compute'):
                probe_call_checks([(invalid, ('compute.urlMaps.update',))])

    def test_noncompute_families_retain_their_original_actual_resource_checks(self):
        for family in API['families'][5:]:
            with self.subTest(family=family['family']):
                required = [(family['endpoint'], ('operation.fixture',))]
                probe = Mock(return_value=[])
                live.verify_checks(required, probe, attempts=1)
                probe.assert_called_once_with(*required[0])

    def test_api_or_network_failure_is_never_converted_into_readiness_or_retried(self):
        probe = Mock(side_effect=urllib.error.URLError('network unavailable'))
        with self.assertRaises(urllib.error.URLError):
            live.verify_checks([(FAILURE['endpoint'], ('compute.urlMaps.invalidateCache',))], probe)
        self.assertEqual(probe.call_count, 1)


class ProbeDiagnosticTests(unittest.TestCase):
    def invoke(self, body, token='secret-token'):
        error = urllib.error.HTTPError(FAILURE['endpoint'], 403, 'Forbidden', {'Authorization': 'Bearer ' + token}, io.BytesIO(body))
        with patch.object(live.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as failure:
                live.request(FAILURE['endpoint'], ('compute.urlMaps.invalidateCache',), token)
        return str(failure.exception)

    def test_structured_denial_records_status_url_permission_and_redacts_token(self):
        token = 'secret-token\nwith-escape'
        body = json.dumps({'error': {'code': 403, 'status': 'PERMISSION_DENIED', 'message': 'Missing compute.urlMaps.list ' + token, 'details': [{'metadata': {'permission': 'compute.urlMaps.list', 'echo': token}}]}, 'ignored': {'headers': 'Authorization'}}).encode()
        actual = self.invoke(body, token)
        for expected in ('HTTP 403', FAILURE['endpoint'], 'PERMISSION_DENIED', 'compute.urlMaps.list', '[REDACTED]'):
            self.assertIn(expected, actual)
        self.assertNotIn('secret-token', actual)
        self.assertNotIn('Authorization', actual)

    def test_unstructured_or_oversized_body_is_omitted_without_partial_credentials(self):
        for body in (b'<html>secret-token</html>', b'{"error":{"message":"secret-token', json.dumps({'error': {'message': 'secret-token' + 'x' * 20000}}).encode()):
            with self.subTest(length=len(body)):
                actual = self.invoke(body)
                self.assertIn('unavailable, unstructured or over 16 KiB', actual)
                self.assertNotIn('secret-token', actual)

    def test_structured_diagnostic_is_bounded(self):
        actual = self.invoke(json.dumps({'error': {'message': 'x' * 8000}}).encode())
        self.assertLess(len(actual), 4500)

    def test_malformed_successful_api_schema_is_not_ready(self):
        response = io.BytesIO(b'{"permissions":"wrong"}')
        with patch.object(live.urllib.request, 'urlopen', return_value=response), self.assertRaisesRegex(RuntimeError, 'invalid testIamPermissions response'):
            live.request(FAILURE['endpoint'], ('compute.urlMaps.invalidateCache',), 'token')


class ExactCdnRoleAdoptionTests(unittest.TestCase):
    def test_initial_import_update_preserves_live_role_and_adds_only_the_two_probe_reads(self):
        value = adoption_plan()
        cdn.check_bootstrap(value)
        change = value['resource_changes'][0]['change']
        self.assertEqual(set(change['after']['permissions']) - set(change['before']['permissions']), {'compute.urlMaps.list', 'compute.backendBuckets.list'})
        for field in cdn.ROLE_FIELDS:
            self.assertEqual(change['before'][field], change['after'][field])
        self.assertEqual(FAILURE['state_ownership']['matching_addresses'], [])

    def test_already_adopted_noop_is_valid_with_or_without_import_metadata(self):
        for importing in (True, False):
            with self.subTest(importing=importing):
                cdn.check_bootstrap(adoption_plan(complete=True, importing=importing))

    def test_create_delete_replacement_and_nonreviewed_update_are_rejected(self):
        for actions in (['create'], ['delete'], ['delete', 'create'], ['create', 'delete'], ['read'], [], ['forget']):
            with self.subTest(actions=actions):
                value = adoption_plan()
                value['resource_changes'][0]['change']['actions'] = actions
                with self.assertRaises(ValueError):
                    cdn.check_bootstrap(value)
        value = adoption_plan(complete=True)
        value['resource_changes'][0]['change']['actions'] = ['update']
        with self.assertRaisesRegex(ValueError, 'add only'):
            cdn.check_bootstrap(value)

    def test_extra_dropped_and_preexisting_unknown_permissions_are_rejected(self):
        for phase in ('before', 'after'):
            for operation in ('add', 'drop', 'duplicate'):
                with self.subTest(phase=phase, operation=operation):
                    value = adoption_plan()
                    permissions = value['resource_changes'][0]['change'][phase]['permissions']
                    if operation == 'add':
                        permissions.append('compute.urlMaps.delete')
                    elif operation == 'drop':
                        permissions.remove('compute.urlMaps.invalidateCache')
                    else:
                        permissions.append(permissions[0])
                    with self.assertRaises(ValueError):
                        cdn.check_bootstrap(value)

    def test_role_identity_metadata_deleted_or_import_target_drift_is_rejected(self):
        for field, invalid in (('project', 'other'), ('role_id', 'other'), ('id', 'other'), ('name', 'other'), ('title', 'other'), ('description', 'other'), ('stage', 'BETA'), ('deleted', True), ('deleted', 0)):
            for phase in ('before', 'after'):
                with self.subTest(field=field, phase=phase):
                    value = adoption_plan()
                    value['resource_changes'][0]['change'][phase][field] = invalid
                    with self.assertRaises(ValueError):
                        cdn.check_bootstrap(value)
        value = adoption_plan()
        value['resource_changes'][0]['change']['importing']['id'] += 'Other'
        with self.assertRaisesRegex(ValueError, 'exact existing role ID'):
            cdn.check_bootstrap(value)

    def test_unknown_policy_incomplete_or_duplicate_rows_cannot_authorize_adoption(self):
        for mutation in ('unknown', 'missing', 'duplicate', 'errored', 'deferred', 'deposed', 'data'):
            with self.subTest(mutation=mutation):
                value = adoption_plan()
                row = value['resource_changes'][0]
                if mutation == 'unknown':
                    row['change']['after_unknown']['permissions'] = True
                elif mutation == 'missing':
                    value['resource_changes'] = []
                elif mutation == 'duplicate':
                    value['resource_changes'].append(copy.deepcopy(row))
                elif mutation in ('errored', 'deferred'):
                    value['errored' if mutation == 'errored' else 'deferred_changes'] = True if mutation == 'errored' else [{}]
                else:
                    row['deposed' if mutation == 'deposed' else 'mode'] = 'previous' if mutation == 'deposed' else 'data'
                with self.assertRaises(ValueError):
                    cdn.check_bootstrap(value)

    def test_unrelated_role_changes_and_imports_remain_refused(self):
        for actions in (['update'], ['create'], ['no-op']):
            value = adoption_plan()
            unrelated = copy.deepcopy(value['resource_changes'][0])
            unrelated['address'] = 'google_project_iam_custom_role.unrelated'
            unrelated['change']['actions'] = actions
            value['resource_changes'].append(unrelated)
            with self.subTest(actions=actions), self.assertRaisesRegex(ValueError, 'unrelated import'):
                cdn.check_bootstrap(value)
            unrelated['change'].pop('importing')
            if actions != ['no-op']:
                with self.assertRaisesRegex(ValueError, 'refusing'):
                    cdn.check_bootstrap(value)

    def test_real_workflow_cli_uses_the_exact_saved_plan_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bootstrap.tfplan.json'
            path.write_text(json.dumps(adoption_plan()))
            result = subprocess.run([sys.executable, 'scripts/cdn_plan_readiness.py', 'check-bootstrap', '--plan-json', str(path)], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            value = adoption_plan()
            value['resource_changes'][0]['change']['after']['permissions'].append('compute.urlMaps.delete')
            path.write_text(json.dumps(value))
            result = subprocess.run([sys.executable, 'scripts/cdn_plan_readiness.py', 'check-bootstrap', '--plan-json', str(path)], cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('add only', result.stderr)
