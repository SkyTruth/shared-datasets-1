from types import SimpleNamespace
from unittest.mock import Mock

from google.api_core.exceptions import Forbidden
from google.cloud.storage import Bucket
import pytest

from ingestion.dataset_usage.health import Health


DEFAULT_LOGGING = object()


def health(*, pages, logging=DEFAULT_LOGGING, exemptions=None):
    checker = Health.__new__(Health)
    checker.raw_bucket = 'raw'
    checker.project = 'project'
    checker.classifier = SimpleNamespace(bucket='shared', config={'sink_filter': 'reviewed filter'})
    if logging is DEFAULT_LOGGING:
        logging = {'logBucket': 'raw', 'logObjectPrefix': 'storage-usage'}
    checker.client = SimpleNamespace(get_bucket=lambda *args, **kwargs: SimpleNamespace(get_logging=lambda: logging))
    class Response:
        def __init__(self, data):
            self.data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self.data
    class Session:
        def get(self, url, **kwargs):
            if '/sinks/' in url:
                return Response({'destination': 'storage.googleapis.com/raw', 'filter': 'reviewed filter'})
            return Response(pages.pop(0))
        def post(self, url, **kwargs):
            return Response({'auditConfigs': [{'service': 'storage.googleapis.com', 'auditLogConfigs': [{'logType': 'DATA_READ', 'exemptedMembers': exemptions or []}]}]})
    checker.session = Session()
    return checker


def test_sink_errors_on_later_metric_page_suspend_coverage():
    checker = health(pages=[{'nextPageToken': 'two'}, {'timeSeries': [{'points': [{'value': {'int64Value': '1'}}]}]}])
    assert checker.configuration_healthy() is False


def test_read_audit_exemption_and_wrong_usage_prefix_suspend_coverage():
    assert health(pages=[], exemptions=['serviceAccount:exempted']).configuration_healthy() is False
    assert health(pages=[], logging={'logBucket': 'raw', 'logObjectPrefix': 'wrong'}).configuration_healthy() is False
    assert health(pages=[{}]).configuration_healthy() is True


def test_actual_storage_bucket_without_logging_suspends_coverage_without_other_api_calls():
    bucket = Bucket(client=None, name='shared')
    assert bucket.get_logging() is None
    checker = health(pages=[], logging=None)
    checker.client.get_bucket = Mock(return_value=bucket)
    checker.session = Mock()
    assert checker.configuration_healthy() is False
    checker.client.get_bucket.assert_called_once_with('shared', timeout=30)
    assert checker.session.mock_calls == []
    assert bucket.get_logging() is None


def test_empty_logging_configuration_is_not_replaced_with_a_healthy_fixture():
    assert health(pages=[], logging={}).configuration_healthy() is False


@pytest.mark.parametrize('logging', [[], 'disabled', 0, False])
def test_malformed_logging_still_fails(logging):
    with pytest.raises(TypeError, match='logging'):
        health(pages=[], logging=logging).configuration_healthy()


def test_failed_bucket_read_is_not_reported_as_disabled_logging():
    checker = health(pages=[])
    denied = Forbidden('bucket read denied')
    checker.client.get_bucket = Mock(side_effect=denied)
    with pytest.raises(Forbidden) as raised:
        checker.configuration_healthy()
    assert raised.value is denied
