from copy import deepcopy
import unittest
from unittest import mock

from ingestion.common import publication as p, reset_controls as controls


class ResetControlTests(unittest.TestCase):
    def session(self, *, state="PAUSED", pages=None):
        values = [{"state": state}, *(pages or [{"executions": []}])]
        session = mock.Mock()
        session.get.side_effect = [mock.Mock(status_code=200, content=p.canonical(value)) for value in values]
        return session

    def test_only_affected_project_job_is_read_and_all_execution_pages_are_checked(self):
        for slug, job in controls.ASSET_JOBS.items():
            with self.subTest(slug=slug):
                session = self.session(pages=[{"nextPageToken": "next"}, {"executions": [{"completionTime": "done", "reconciling": False}]}])
                controls.GoogleControlReader(session).check_quiescent(slug)
                calls = session.get.call_args_list
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[0].args[0], f"https://cloudscheduler.googleapis.com/v1/projects/{controls.PROJECT}/locations/{controls.REGION}/jobs/{job}")
                self.assertEqual(calls[1].args[0], f"https://run.googleapis.com/v2/projects/{controls.PROJECT}/locations/{controls.REGION}/jobs/{job}/executions")
                self.assertEqual(calls[2].kwargs["params"], {"pageToken": "next"})
                self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in calls))

    def test_active_schedule_or_running_pending_reconciling_execution_refuses(self):
        for state in ("ENABLED", "DISABLED", None):
            with self.subTest(state=state), self.assertRaisesRegex(p.PublicationError, "pause"):
                controls.GoogleControlReader(self.session(state=state)).check_quiescent("wdpa-marine")
        for execution in ({}, {"completionTime": None}, {"completionTime": "done", "reconciling": True}):
            with self.subTest(execution=execution), self.assertRaisesRegex(p.PublicationError, "running or pending"):
                session = self.session(pages=[{"nextPageToken": "more"}, {"executions": [deepcopy(execution)]}])
                controls.GoogleControlReader(session).check_quiescent("wdpa-marine")

    def test_permission_errors_and_bad_pagination_fail_closed(self):
        for status in (403, 404, 500):
            session = mock.Mock()
            session.get.return_value = mock.Mock(status_code=status)
            with self.subTest(status=status), self.assertRaises(p.PublicationError):
                controls.GoogleControlReader(session).check_quiescent("ims-sea-ice-extent")
        for pages in ([{"nextPageToken": "x"}, {"nextPageToken": "x"}], [{"nextPageToken": True}], [{"executions": [None]}]):
            with self.subTest(pages=pages), self.assertRaises(p.PublicationError):
                controls.GoogleControlReader(self.session(pages=pages)).check_quiescent("ims-sea-ice-extent")
