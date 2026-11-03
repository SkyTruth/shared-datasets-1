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
from services.monitoring_slack import run as relay, decisions
from ingestion.common import decision_events as wire
from scripts.slack_incident_api import Slack


class Bucket:
    def __init__(self):
        self.objects = {}
        self.fail_receipt = False

    def test_iam_permissions(self, permissions):
        return permissions

    def list_blobs(self, *, prefix):
        return [type("Listed", (), {"name": key})() for key in sorted(self.objects) if key.startswith(prefix)]

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
                if bucket.fail_receipt and json.loads(raw).get("ts") is not None:
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


def decision_event():
    return wire.event({"schema": wire.SCHEMA, "project": wire.PROJECT, "job": "wdpa-monthly",
        "execution": "wdpa-monthly-abcde", "asset_slug": "wdpa-marine", "release": "2026-11-01", "cases": [{
            "new_identity_key": ["555729583"], "new_geometry_hash": "sha256:" + "a" * 64,
            "new_properties_hash": "sha256:" + "b" * 64, "matching_geometry_feature_ids": ["11347", "15093"],
            "matching_properties_feature_ids": [], "matching_geometry_properties_hashes": ["sha256:" + "c" * 64],
            "matching_properties_geometry_hashes": []}]})


def proof(source, case):
    return {"asset_slug": source["asset_slug"], "decision": {**case, "release": source["release"],
        "action": "keep_previous_key_mapping", "previous_feature_id_for_key": "15378", "reviewer": "jonaraphael",
        "rationale": "Preserve three distinct Bonden designations; reusing another ID would merge records.",
        "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/365"},
        "head_sha": "a" * 40, "merge_sha": "b" * 40,
        "acceptance": {"kind": "self_authored_merge", "reviewed_commit": "a" * 40}}


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.bucket = Bucket()
        self.calls, self.messages = [], {}
        self.alerts = {}
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
        self.stack.enter_context(patch.object(relay, "read_alert", side_effect=lambda key: self.alerts[key]))
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

    def test_review_ready_upgrades_the_original_parent_through_real_http(self):
        source = decision_event()
        ref = {"uri": f"gs://skytruth-shared-datasets-1/_scratch/wdpa-builds/{source['execution']}/{source['asset_slug']}/identity-review.json",
               "generation": 1001, "size": 3, "sha256": "a" * 64}
        ready = wire.event({**source, "schema":wire.REVIEW_SCHEMA, "review_bundle":ref})
        with patch.dict(os.environ, {"CATALOG_VIEWER_URL":"https://catalog-viewer-example.a.run.app"}):
            for value in (source, ready, ready, source):
                packet = {"subscription":wire.SUBSCRIPTION,"message":{"data":base64.b64encode(wire.canonical(value)).decode()}}
                self.assertEqual(self.send(packet), 204)
        self.assertEqual(len(self.messages),1)
        self.assertEqual(sum(method == "chat.postMessage" for method,_ in self.calls),1)
        self.assertEqual(sum(method == "chat.update" for method,_ in self.calls),1)
        text = next(iter(self.messages.values()))["text"]
        self.assertIn("Compare this case",text)
        self.assertIn("review_generation=1001",text)

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
        for method, body in self.calls:
            if method not in {"chat.postMessage", "chat.update"}:
                continue
            for field in ("Impact", "Mutation state", "Owner", "Required action"):
                self.assertIn(f"*{field}:*", body["text"])
            self.assertIn("*Mutation state:* Unknown", body["text"])
            self.assertIn("jonaraphael (triage)", body["text"])
            self.assertIn("operation owner unknown", body["text"])
        self.assertIn("Verify the affected execution", message["text"])
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

    def approve(self, source, cases=None):
        records = {wire.case_key(source["asset_slug"], source["release"], item): proof(source, item)
                   for item in (source["cases"] if cases is None else cases)}
        store = relay.Store(self.bucket)
        _, generation = store.read("identity/approvals.json")
        store.write("identity/approvals.json", {"schema": decisions.APPROVAL_SCHEMA, "revision": "b" * 40,
                                              "known": {}, "records": records}, generation)

    def send_decision(self, source):
        return self.send({"subscription": wire.SUBSCRIPTION, "message": {"data": base64.b64encode(wire.canonical(source)).decode()}})

    def review_alert(self, source, incident_id="0.review", state="open"):
        labels = {"project_id": wire.PROJECT, "location": "us-central1", "job_name": source["job"]}
        self.alerts[incident_id] = {"name": f"projects/{wire.PROJECT}/alerts/{incident_id}",
            "policy": {"displayName": decisions.POLICY, "userLabels": {"kind": "decision-required"}},
            "resource": {"type": "cloud_run_job", "labels": labels}, "log": {"extractedLabels": {
                "decision_execution": source["execution"], "decision_asset": source["asset_slug"], "decision_release": source["release"]}}}
        value = envelope(state, incident_id)
        payload = json.loads(base64.b64decode(value["message"]["data"]))
        payload["incident"]["policy_name"] = decisions.POLICY
        value["message"]["data"] = base64.b64encode(json.dumps(payload).encode()).decode()
        return self.send(value)

    def test_producer_to_receiver_replies_once_and_settles_only_exact_review_alerts(self):
        source = decision_event()
        self.assertEqual(self.review_alert(source), 204)  # Monitoring may arrive first.
        foreign = {**source, "execution": "wdpa-monthly-zzzzz"}
        self.assertEqual(self.review_alert(foreign, "0.other-review"), 204)
        self.assertEqual(self.send(envelope(incident_id="0.unrelated-failure")), 204)
        owner = self
        class Session:
            def post(self, url, *, json, timeout):
                assert url == "https://pubsub.googleapis.com/v1/" + wire.TOPIC + ":publish"
                assert timeout == 30
                assert owner.send({"subscription": wire.SUBSCRIPTION, "message": json["messages"][0]}) == 204
                return type("Response", (), {"raise_for_status": lambda self: None, "json": lambda self: {"messageIds": ["123"]}})()
        wire.publish(asset_slug=source["asset_slug"], release=source["release"], ambiguities=source["cases"],
                     env={"CLOUD_RUN_JOB": source["job"], "CLOUD_RUN_EXECUTION": source["execution"]}, session=Session())
        self.approve(source)
        self.assertEqual(self.send_decision(source), 204)
        for state in ("open", "closed", "open"):
            self.assertEqual(self.review_alert(source, state=state), 204)
            self.assertEqual(self.send_decision(source), 204)
        replies = [body for method, body in self.calls if method == "chat.postMessage" and body.get("thread_ts")]
        self.assertEqual(len(replies), 1)
        self.assertFalse(replies[0]["reply_broadcast"])
        for text in ("15378", "jonaraphael", "Why recommended", "distinct Bonden", "/pull/365"):
            self.assertIn(text, replies[0]["text"])
        store = relay.Store(self.bucket)
        resolved, _ = store.load("0.review")
        self.assertTrue(resolved["closed"])
        self.assertIn("Decision recorded", self.messages[resolved["ts"]]["text"])
        for key in ("0.other-review", "0.unrelated-failure"):
            record, _ = store.load(key)
            self.assertEqual(record["schema"], relay.SCHEMA)
            self.assertIn(" Open", self.messages[record["ts"]]["text"])

    def test_partial_decisions_wait_and_late_monitoring_is_settled_without_duplicate_replies(self):
        source = decision_event()
        second = {**source["cases"][0], "new_identity_key": ["another"]}
        source = wire.event({**source, "cases": [*source["cases"], second]})
        self.approve(source, [source["cases"][0]])
        self.assertEqual(self.send_decision(source), 204)
        self.assertEqual(self.review_alert(source), 204)
        record, _ = relay.Store(self.bucket).load("0.review")
        self.assertIn(" Open", self.messages[record["ts"]]["text"])
        self.approve(source)
        self.assertEqual(self.send_decision(source), 204)
        self.assertEqual(self.review_alert(source, "0.late-review"), 204)
        posts = [body for method, body in self.calls if method == "chat.postMessage" and body.get("thread_ts")]
        self.assertEqual(len(posts), 2)
        self.assertNotEqual(posts[0]["text"], posts[1]["text"])
        for key in ("0.review", "0.late-review"):
            receipt, _ = relay.Store(self.bucket).load(key)
            self.assertFalse(receipt["closed"])  # No fabricated GCP closure.
            self.assertIn("Decision recorded", self.messages[receipt["ts"]]["text"])

    def test_lost_reply_response_never_reposts_or_resolves_review(self):
        source = decision_event()
        self.assertEqual(self.send_decision(source), 204)
        self.assertEqual(self.review_alert(source), 204)
        self.approve(source)
        self.drop_post = True
        self.assertEqual(self.send_decision(source), 503)
        self.drop_post = False
        count = len(self.messages)
        self.assertEqual(self.send_decision(source), 503)
        self.assertEqual(len(self.messages), count)
        receipt, _ = relay.Store(self.bucket).load("0.review")
        self.assertEqual(receipt["schema"], relay.SCHEMA)

    def test_update_failure_and_late_open_finish_decision_view_without_new_reply(self):
        source = decision_event()
        self.approve(source)
        self.assertEqual(self.send_decision(source), 204)
        self.fail_update = True
        self.assertEqual(self.review_alert(source), 503)
        self.fail_update = False
        self.assertEqual(self.review_alert(source), 204)
        self.assertEqual(sum(method == "chat.postMessage" and bool(body.get("thread_ts")) for method, body in self.calls), 1)
        record, _ = relay.Store(self.bucket).load("0.review")
        self.assertIn("Decision recorded", self.messages[record["ts"]]["text"])


if __name__ == "__main__":
    unittest.main()
