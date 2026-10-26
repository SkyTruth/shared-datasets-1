"""Real receiver HTTP -> serialized GCS receipts -> existing Slack Web API boundary."""
import base64
from contextlib import ExitStack
import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from google.api_core.exceptions import NotFound, PreconditionFailed
from services.monitoring_slack import run as relay
from scripts.slack_incident_api import Slack


class Bucket:
    def __init__(self):
        self.objects = {}
        self.fail_receipt = False

    def test_iam_permissions(self, permissions):
        return permissions

    def blob(self, name):
        bucket = self
        class Blob:
            generation = None
            def reload(self):
                if name not in bucket.objects:
                    raise NotFound("absent")
                self.generation = bucket.objects[name][0]
            def download_as_bytes(self, *, if_generation_match):
                generation, raw = bucket.objects[name]
                if generation != if_generation_match:
                    raise PreconditionFailed("concurrent read")
                return raw
            def upload_from_string(self, raw, *, content_type, if_generation_match, retry):
                assert content_type == "application/json" and retry is None
                previous = bucket.objects.get(name, (0, None))[0]
                if previous != if_generation_match:
                    raise PreconditionFailed("concurrent write")
                if bucket.fail_receipt and json.loads(raw)["ts"] is not None:
                    raise OSError("receipt write unavailable")
                self.generation = previous + 1
                bucket.objects[name] = self.generation, raw.encode()
        return Blob()


def envelope(state="open", incident_id="0.oe73n791vhsd"):
    payload = {"version": "1.2", "incident": {
        "incident_id": incident_id, "scoping_project_id": relay.PROJECT,
        "scoping_project_number": 12345, "state": state,
        "policy_name": "WDPA execution observations are stale",
        "summary": "Observer <example> & missing data", "started_at": 1792830681,
        "ended_at": 1792830710 if state == "closed" else None,
    }}
    return {"subscription": relay.SUBSCRIPTION, "message": {"data": base64.b64encode(json.dumps(payload).encode()).decode(), "messageId": "123"}}


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.bucket = Bucket()
        self.calls, self.messages = [], {}
        self.drop_post, self.fail_update = False, False
        owner = self
        class API(BaseHTTPRequestHandler):
            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append((method, body))
                result = {"ok": True}
                if method == "auth.test":
                    result.update(bot_id="B12345678", user_id="U12345678", team_id="T12345678")
                elif method == "chat.postMessage":
                    ts = f"1792830681.{len(owner.messages) + 1:06d}"
                    owner.messages[ts] = body
                    result.update(channel=body["channel"], ts=ts)
                    if owner.drop_post:
                        self.close_connection = True
                        return
                elif method == "chat.update":
                    if owner.fail_update:
                        result = {"ok": False, "error": "ratelimited"}
                    else:
                        owner.messages[body["ts"]] = body
                        result.update(channel=body["channel"], ts=body["ts"])
                else:
                    raise AssertionError(method)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            def log_message(self, *args):
                pass
        self.api = HTTPServer(("127.0.0.1", 0), API)
        threading.Thread(target=self.api.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        def opener(request, **kwargs):
            request.full_url = f"http://127.0.0.1:{self.api.server_port}/" + request.full_url.rsplit("/", 1)[-1]
            return urllib.request.urlopen(request, **kwargs)
        ready = threading.Event()
        def server(address, handler):
            self.receiver = HTTPServer(("127.0.0.1", 0), handler)
            ready.set()
            return self.receiver
        class Client:
            def bucket(inner, name):
                assert name == "fixture-private-state"
                return self.bucket
        self.stack = ExitStack()
        self.stack.enter_context(patch.dict(os.environ, {"SHARED_DATASETS_SLACK_BOT_TOKEN": "fixture-token", "SHARED_DATASETS_SLACK_CHANNEL_ID": "C12345678", "MONITORING_SLACK_STATE_BUCKET": "fixture-private-state"}))
        self.stack.enter_context(patch.object(relay, "Slack", side_effect=lambda token, channel: Slack(token, channel, opener=opener, sleep=lambda _: None)))
        self.stack.enter_context(patch.object(relay.storage, "Client", return_value=Client()))
        self.stack.enter_context(patch.object(relay, "HTTPServer", side_effect=server))
        self.worker = threading.Thread(target=relay.main, daemon=True)
        self.worker.start()
        self.assertTrue(ready.wait(5), "real entrypoint did not start")

    def tearDown(self):
        self.receiver.shutdown()
        self.worker.join(5)
        self.receiver.server_close()
        self.api.shutdown()
        self.api.server_close()
        self.stack.close()

    def send(self, value):
        request = urllib.request.Request(f"http://127.0.0.1:{self.receiver.server_port}/", json.dumps(value).encode(), method="POST")
        try:
            return urllib.request.urlopen(request, timeout=5).status
        except urllib.error.HTTPError as error:
            return error.code

    def test_open_closed_duplicates_and_late_open_edit_one_original_parent(self):
        for state in ("open", "open", "closed", "closed", "open"):
            self.assertEqual(self.send(envelope(state)), 204)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(sum(method == "chat.postMessage" for method, _ in self.calls), 1)
        message = next(iter(self.messages.values()))
        self.assertIn("Closed by Monitoring", message["text"])
        self.assertNotIn("thread_ts", message)
        self.assertEqual(message["channel"], "C12345678")
        self.assertIn("requires separate verification", message["text"])
        record, _ = relay.Store(self.bucket).load("0.oe73n791vhsd")
        self.assertTrue(record["closed"])
        self.assertEqual(record["ts"], next(iter(self.messages)))

    def test_closed_arriving_first_stays_closed_and_distinct_incidents_stay_separate(self):
        for state in ("closed", "open"):
            self.assertEqual(self.send(envelope(state)), 204)
        self.assertEqual(self.send(envelope(incident_id="0.other")), 204)
        self.assertEqual(len(self.messages), 2)
        self.assertIn("Closed by Monitoring", next(iter(self.messages.values()))["text"])

    def test_failed_update_retries_the_exact_parent_even_with_old_open_event(self):
        self.assertEqual(self.send(envelope()), 204)
        self.fail_update = True
        self.assertEqual(self.send(envelope("closed")), 503)
        self.fail_update = False
        self.assertEqual(self.send(envelope()), 204)
        self.assertEqual(len(self.messages), 1)
        self.assertIn("Closed by Monitoring", next(iter(self.messages.values()))["text"])

    def test_uncertain_post_or_lost_receipt_never_posts_a_second_parent(self):
        for drop in (True, False):
            with self.subTest(drop_response=drop):
                self.drop_post, self.bucket.fail_receipt = drop, not drop
                key = "0.drop" if drop else "0.receipt"
                self.assertEqual(self.send(envelope(incident_id=key)), 503)
                self.drop_post, self.bucket.fail_receipt = False, False
                before = len(self.messages)
                self.assertEqual(self.send(envelope("closed", key)), 503)
                self.assertEqual(len(self.messages), before)

    def test_foreign_and_malformed_events_never_reach_slack(self):
        cases = [envelope(), envelope()]
        cases[0]["subscription"] = "projects/other/subscriptions/monitoring-slack"
        payload = {"version": "1.2", "incident": {"scoping_project_id": "other"}}
        cases[1]["message"]["data"] = base64.b64encode(json.dumps(payload).encode()).decode()
        for value in [*cases, {}, {"subscription": relay.SUBSCRIPTION, "message": {"data": "!!!"}}]:
            self.assertEqual(self.send(value), 503)
        self.assertEqual([method for method, _ in self.calls], ["auth.test"])

    def test_persisted_identity_change_and_corruption_fail_before_update(self):
        self.assertEqual(self.send(envelope()), 204)
        key = "incidents/0.oe73n791vhsd.json"
        generation, raw = self.bucket.objects[key]
        record = json.loads(raw)
        for change in ({"schema": "unknown"}, {"identity": {"channel": "C87654321"}}, {"ts": "not-a-timestamp"}):
            self.bucket.objects[key] = generation, json.dumps({**copy.deepcopy(record), **change}).encode()
            self.assertEqual(self.send(envelope("closed")), 503)
        self.assertFalse(any(method == "chat.update" for method, _ in self.calls))

    def test_warning_decision_keeps_its_severity_and_escapes_mentions(self):
        incident = relay.event(json.dumps(envelope()).encode())
        incident["severity"] = "WARNING"
        payload = relay.message(incident, False)
        self.assertTrue(payload["text"].startswith(":warning:"))
        self.assertIn("&lt;example&gt; &amp;", payload["text"])

    def test_generation_preconditions_reject_a_competing_claim(self):
        store = relay.Store(self.bucket)
        first = {"ts": None}
        store.save("0.concurrent", first, 0)
        with self.assertRaises(PreconditionFailed):
            store.save("0.concurrent", first, 0)


if __name__ == "__main__":
    unittest.main()
