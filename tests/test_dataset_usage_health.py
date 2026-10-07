from types import SimpleNamespace

from ingestion.dataset_usage.health import Health


def health(*, pages, logging=None, exemptions=None):
    checker = Health.__new__(Health)
    checker.raw_bucket = 'raw'
    checker.project = 'project'
    checker.classifier = SimpleNamespace(bucket='shared', config={'sink_filter': 'reviewed filter'})
    checker.client = SimpleNamespace(get_bucket=lambda *args, **kwargs: SimpleNamespace(get_logging=lambda: logging or {'logBucket': 'raw', 'logObjectPrefix': 'storage-usage'}))
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
