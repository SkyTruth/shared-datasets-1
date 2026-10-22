"""Offline transport/persistence proofs for independent native Monitoring alerts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlsplit

from google.api_core import exceptions as api_errors
import pytest
import requests

from scripts import operational_retry
from scripts.slack_incident_api import Slack, SlackError
from services.monitoring_slack import model, run
from services.monitoring_slack.store import CAPABILITY_OBJECT, STATE_OBJECT, Conflict, Snapshot, Store

FIXTURES = Path(__file__).parent / "fixtures" / "monitoring-slack"
NOW = "2026-10-22T13:05:00Z"
CONTEXT = {"CLOUD_RUN_EXECUTION": "monitoring-slack-relay-abc12",
           "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL": "https://github.com/SkyTruth/shared-datasets-1/actions/runs/12345"}


def config(**changes):
    value = {"schema": model.CONFIG_SCHEMA, "project": "shared-datasets-1",
             "policies": [{"name": "projects/shared-datasets-1/alertPolicies/1980534029880229379", "kind": "health"},
                          {"name": "projects/shared-datasets-1/alertPolicies/9000000000000000001", "kind": "security"},
                          {"name": "projects/shared-datasets-1/alertPolicies/9000000000000000002", "kind": "security"}],
             "health_metric": "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2",
             "health_policy_created_at": "2026-10-22T11:00:00Z", "state_bucket": "relay-private-test-state",
             "slack_secret_version": "projects/shared-datasets-1/secrets/synthetic-relay-test/versions/1",
             "slack_channel": "C12345678", "slack_team": "T12345678", "slack_user": "U12345678",
             "activated_at": "2026-10-22T12:00:00Z", "mode": "live"}
    return model.Config.parse({**value, **changes})


def fixture(filename="health-open", **changes):
    return {**json.loads((FIXTURES / (filename + ".json")).read_text()), **changes}


def point(at="2026-10-22T13:04:50Z", **changes):
    return {"metric": {"type": config()["health_metric"], "labels": {}},
            "resource": {"type": "global", "labels": {"project_id": "shared-datasets-1"}},
            "metricKind": "GAUGE", "valueType": "INT64",
            "points": [{"interval": {"endTime": at}, "value": {"int64Value": "1"}}], **changes}


class MemoryStore:
    """The fake replaces only GCS; every boundary still serializes real bytes."""
    def __init__(self):
        self.rows, self.lock, self.writes = {}, Lock(), []
        self.before_save = None

    def load(self, name=STATE_OBJECT):
        with self.lock:
            body, generation = self.rows.get(name, (None, 0))
            return Snapshot(None if body is None else model.parse_json(body), generation)

    def save(self, value, generation, name=STATE_OBJECT):
        if self.before_save:
            self.before_save(value, generation, name)
        body = model.canonical(value)
        with self.lock:
            _, actual = self.rows.get(name, (None, 0))
            if actual != generation:
                raise Conflict("fake generation mismatch")
            self.rows[name] = (body, actual + 1)
            self.writes.append((name, actual + 1))
            return Snapshot(model.parse_json(body), actual + 1)


class Reader:
    def __init__(self, *alerts, points=()):
        self.current = {value["name"]: deepcopy(value) for value in alerts}
        self.series, self.exact, self.queries, self.tokens = list(points), [], [], 0
        self.fail = None

    def alerts(self, policy, cutoff, *, open_only=False):
        self.queries.append((policy, cutoff, open_only))
        if self.fail:
            raise self.fail
        return [deepcopy(value) for value in self.current.values() if value["policy"]["name"] == policy
                and (not open_only or value["state"] == "OPEN")]

    def alert(self, name):
        self.exact.append(name)
        value = self.current.get(name)
        if value is None:
            raise api_errors.NotFound("known alert absent")
        return deepcopy(value)

    def points(self, now):
        return deepcopy(self.series)

    def token(self):
        self.tokens += 1
        return "xoxb-synthetic-test"


class FakeSlack:
    def __init__(self, cfg=None):
        self.cfg = cfg or config()
        self.posts, self.updates = [], []
        self.failure = None

    def identity(self):
        return self.cfg.identity

    def post(self, payload):
        self.posts.append(deepcopy(payload))
        if self.failure:
            raise self.failure
        return "1000000000.000001"

    def update(self, ts, payload):
        self.updates.append((ts, deepcopy(payload)))
        if self.failure:
            raise self.failure


def execute(reader, store=None, cfg=None, slack=None, now=NOW):
    store, cfg, slack = store or MemoryStore(), cfg or config(), slack or FakeSlack()
    return run.cycle(reader, store, cfg, clock=lambda: now, slack_factory=lambda token, channel: slack)


def test_real_official_fixtures_one_parent_quiet_health_recovery_and_no_repeat():
    cfg, store, slack = config(), MemoryStore(), FakeSlack()
    reader = Reader(fixture())
    assert execute(reader, store, cfg, slack)["public_operations"] == 1
    reader.current[fixture()["name"]] = fixture("health-closed")
    reader.series = [point()]
    assert execute(reader, store, cfg, slack)["public_operations"] == 1
    assert execute(reader, store, cfg, slack)["public_operations"] == 0
    assert len(slack.posts) == len(slack.updates) == 1
    assert slack.updates[0][0] == "1000000000.000001"
    assert "RECOVERED" in slack.updates[0][1]["text"]
    episode = store.load().value["episodes"][fixture()["name"]]
    assert episode["operation"] is None
    assert episode["desired_sha256"] == episode["applied_sha256"]
    assert len(reader.queries) == 18  # all-open AND recent, every policy/every pass.


def test_closed_before_first_post_is_quiet_only_for_health():
    store, slack = MemoryStore(), FakeSlack()
    reader = Reader(fixture("health-closed"), fixture("security-closed"), points=[point()])
    assert execute(reader, store, slack=slack)["public_operations"] == 1
    assert len(slack.posts) == 1
    assert "Repair remains unverified" in slack.posts[0]["text"]
    assert "RECOVERED" not in slack.posts[0]["text"]
    assert store.load().value["episodes"][fixture("health-closed")["name"]]["ts"] is None


def test_preactivation_security_and_health_are_historical_not_adopted():
    store, slack = MemoryStore(), FakeSlack()
    reader = Reader(fixture("security-closed", openTime="2026-10-22T11:59:59Z"),
                    fixture(openTime="2026-10-22T11:50:00Z"))
    assert execute(reader, store, slack=slack)["observed"] == 0
    assert store.load().value["episodes"] == {}
    assert not slack.posts


def test_quiet_closed_health_tombstone_does_not_poll_provider_forever_without_a_parent():
    store, reader, slack = MemoryStore(), Reader(fixture("health-closed")), FakeSlack()
    execute(reader, store, slack=slack)
    exact = list(reader.exact)
    reader.current.clear()
    execute(reader, store, slack=slack, now="2026-10-22T14:00:00Z")
    assert reader.exact == exact and not slack.posts and not slack.updates
    assert store.load().value["episodes"][fixture("health-closed")["name"]]["alert"]["state"] == "CLOSED"


def test_shadow_does_not_resolve_secret_or_construct_slack():
    reader, store = Reader(fixture()), MemoryStore()
    def forbidden(*args):
        raise AssertionError("shadow acquired public transport")
    result = run.cycle(reader, store, config(mode="shadow"), clock=lambda: NOW, slack_factory=forbidden)
    assert result["public_operations"] == reader.tokens == 0
    assert model.Config.parse({key: value for key, value in config().value.items() if key != "mode"})["mode"] == "shadow"
    # The same signed/operational identity survives reviewed activation.
    assert config(mode="shadow").binding == config(mode="live").binding


@pytest.mark.parametrize("series", [[], [point("2026-10-22T12:44:25Z")], [point("2026-10-22T12:54:59.999999999Z")]])
def test_closed_health_requires_postopening_fresh_point(series):
    store, slack, reader = MemoryStore(), FakeSlack(), Reader(fixture())
    execute(reader, store, slack=slack)
    reader.current[fixture()["name"]] = fixture("health-closed")
    reader.series = series
    execute(reader, store, slack=slack)
    assert "RECOVERED" not in slack.updates[-1][1]["text"]
    assert store.load().value["episodes"][fixture()["name"]]["recovery"] is None


def test_health_anchor_and_nanosecond_future_boundary():
    closed = model.normalize(fixture("health-closed"), config(), NOW)
    assert model.heartbeat([point()], closed, config(health_policy_created_at="2026-10-22T13:04:51Z"), NOW) is None
    with pytest.raises(model.Invalid, match="future heartbeat"):
        model.heartbeat([point("2026-10-22T13:05:00.000000001Z")], closed, config(), NOW)
    assert model.heartbeat([point(NOW)], closed, config(), NOW)["point_at"] == NOW


@pytest.mark.parametrize("mutation", [
    lambda value: value["points"][0].update(value={"int64Value": True}),
    lambda value: value["points"][0].update(value={"doubleValue": 1}),
    lambda value: value["metric"].update(type="custom.googleapis.com/wrong"),
    lambda value: value["resource"]["labels"].update(project_id="foreign-project"),
    lambda value: value.update(valueType="DOUBLE"),
])
def test_bad_metric_proof_is_failure_not_a_false_green(mutation):
    series = point()
    mutation(series)
    with pytest.raises(model.Invalid):
        execute(Reader(fixture("health-closed"), points=[series]))


def test_stale_open_and_duplicates_cannot_regress_closed_or_create_second_parent():
    store, reader, slack = MemoryStore(), Reader(fixture("security-closed")), FakeSlack()
    execute(reader, store, slack=slack)
    name = fixture("security-closed")["name"]
    old = deepcopy(store.load().value["episodes"][name])
    raw = fixture("security-closed", state="OPEN")
    raw.pop("closeTime")
    reader.current[name] = raw
    execute(reader, store, slack=slack)
    assert store.load().value["episodes"][name] == old
    assert len(slack.posts) == 1 and not slack.updates


def test_new_monitoring_resource_is_new_episode_even_for_same_policy():
    store, slack = MemoryStore(), FakeSlack()
    reader = Reader(fixture("security-closed"))
    execute(reader, store, slack=slack)
    new = fixture("security-closed", name="projects/shared-datasets-1/alerts/distinct-episode")
    reader.current[new["name"]] = new
    execute(reader, store, slack=slack)
    assert len(slack.posts) == len(store.load().value["episodes"]) == 2


def test_missing_known_exact_resource_cannot_resolve_or_advance():
    store, reader, slack = MemoryStore(), Reader(fixture()), FakeSlack()
    execute(reader, store, slack=slack)
    before = store.load()
    reader.current.clear()
    with pytest.raises(api_errors.NotFound):
        execute(reader, store, slack=slack, now="2026-10-22T13:06:00Z")
    assert store.load() == before
    assert not slack.updates


@pytest.mark.parametrize("mutation", [
    lambda raw: raw.update(name="projects/foreign-project/alerts/example"),
    lambda raw: raw["policy"].update(name="projects/shared-datasets-1/alertPolicies/999"),
    lambda raw: raw.update(state="STATE_UNSPECIFIED"),
    lambda raw: raw.update(closeTime="2026-10-22T13:00:00Z"),
    lambda raw: raw.update(openTime="2026-10-22T13:05:00.000000001Z"),
    lambda raw: raw.update(error={"code": 500}),
])
def test_provider_identity_and_malformed_alerts_fail_before_any_write(mutation):
    raw, store, slack = fixture(), MemoryStore(), FakeSlack()
    mutation(raw)
    reader = Reader()
    reader.alerts = lambda *args, **kwargs: [raw]  # Malformed HTTP200 escapes the requested filter.
    with pytest.raises(model.Invalid):
        execute(reader, store, slack=slack)
    assert store.load().generation == 0 and not slack.posts


def test_claim_persisted_before_real_slack_http_and_pin_checked():
    store, observed = MemoryStore(), []
    def opener(request, timeout):
        payload = json.loads(request.data)
        assert request.full_url.startswith("https://slack.com/api/")
        if request.full_url.endswith("auth.test"):
            body = {"ok": True, "bot_id": "BTEST", "user_id": config()["slack_user"], "team_id": config()["slack_team"]}
        else:
            episode = store.load().value["episodes"][fixture()["name"]]
            assert episode["operation"]["kind"] == "post" and episode["ts"] is None
            assert payload["channel"] == config()["slack_channel"]
            assert "thread_ts" not in payload and "reply_broadcast" not in payload
            observed.append(payload)
            body = {"ok": True, "channel": config()["slack_channel"], "ts": "1000000000.000001"}
        return io.BytesIO(json.dumps(body).encode())
    slack = Slack("synthetic-token", config()["slack_channel"], opener=opener)
    execute(Reader(fixture()), store, slack=slack)
    assert len(observed) == 1
    assert store.load().value["episodes"][fixture()["name"]]["operation"] is None


def test_wrong_bot_identity_never_claims_or_posts():
    store = MemoryStore()
    slack = FakeSlack(config(slack_user="UOTHER"))
    with pytest.raises(model.Invalid, match="pinned"):
        execute(Reader(fixture()), store, slack=slack)
    assert store.load().value["episodes"][fixture()["name"]]["operation"] is None
    assert not slack.posts


def test_network_uncertainty_after_post_is_never_blindly_resent_even_after_years():
    store, slack, reader = MemoryStore(), FakeSlack(), Reader(fixture())
    slack.failure = SlackError("unconfirmed")
    with pytest.raises(SlackError):
        execute(reader, store, slack=slack)
    operation = store.load().value["episodes"][fixture()["name"]]["operation"]
    assert operation["uncertain"] is True
    slack.failure = None
    with pytest.raises(model.Invalid, match="inspection"):
        execute(reader, store, slack=slack, now="2027-10-22T13:05:00Z")
    assert len(slack.posts) == 1
    assert store.load().value["episodes"][fixture()["name"]]["operation"] == operation


def test_crash_after_slack_ok_before_ack_retains_claim_and_cannot_repost():
    store, slack, reader = MemoryStore(), FakeSlack(), Reader(fixture())
    def fail_ack(value, generation, name):
        episode = value["episodes"][fixture()["name"]]
        if episode["ts"] is not None:
            raise OSError("simulated ack persistence failure")
    store.before_save = fail_ack
    with pytest.raises(OSError):
        execute(reader, store, slack=slack)
    assert store.load().value["episodes"][fixture()["name"]]["operation"]["uncertain"] is False
    store.before_save = None
    with pytest.raises(model.Invalid, match="inspection"):
        execute(reader, store, slack=slack)
    assert len(slack.posts) == 1


def test_unknown_update_retains_exact_known_timestamp_and_no_later_stale_writer_takeover():
    store, slack, reader = MemoryStore(), FakeSlack(), Reader(fixture())
    execute(reader, store, slack=slack)
    reader.current[fixture()["name"]] = fixture("health-closed")
    reader.series = [point()]
    slack.failure = SlackError("update ack lost")
    with pytest.raises(SlackError):
        execute(reader, store, slack=slack)
    episode = store.load().value["episodes"][fixture()["name"]]
    assert episode["ts"] == "1000000000.000001" and episode["operation"]["kind"] == "update"
    assert episode["operation"]["sha256"] == episode["desired_sha256"]
    with pytest.raises(model.Invalid, match="inspection"):
        execute(reader, store, slack=slack)
    assert len(slack.updates) == 1


def test_cas_contention_cannot_allocate_a_second_post_and_ack_preserves_newer_closure():
    store, slack, reader = MemoryStore(), FakeSlack(), Reader(fixture())
    entered, finish = Event(), Event()
    def blocking_post(payload):
        slack.posts.append(deepcopy(payload))
        entered.set()
        assert finish.wait(5)
        return "1000000000.000001"
    slack.post = blocking_post
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute, reader, store, None, slack)
        assert entered.wait(5)
        reader.current[fixture()["name"]] = fixture("health-closed")
        reader.series = [point()]
        with pytest.raises(model.Invalid, match="inspection"):
            execute(reader, store, slack=slack)
        finish.set()
        first.result()
    # First sender acknowledges only its original payload, preserving newer desired recovery.
    episode = store.load().value["episodes"][fixture()["name"]]
    assert episode["alert"]["state"] == "CLOSED" and episode["recovery"]
    assert episode["desired_sha256"] != episode["applied_sha256"]
    execute(reader, store, slack=slack)
    assert len(slack.posts) == len(slack.updates) == 1


def test_cas_failure_before_claim_cannot_send_or_advance_coverage():
    store, slack = MemoryStore(), FakeSlack()
    store.before_save = lambda *_: (_ for _ in ()).throw(Conflict("write conflict"))
    with pytest.raises(Conflict):
        execute(Reader(fixture()), store, slack=slack)
    assert store.load().generation == 0 and not slack.posts


def test_private_state_pin_and_json_corruption_fail_closed():
    store, reader = MemoryStore(), Reader(fixture())
    execute(reader, store)
    with pytest.raises(model.Invalid, match="mismatch"):
        execute(reader, store, cfg=config(slack_team="TOTHER"))
    with pytest.raises(model.Invalid):
        model.parse_json(b'{"schema":1,"schema":2}')
    with pytest.raises(model.Invalid):
        model.parse_json(b'{"value":NaN}')


def test_health_closure_without_sample_can_gain_later_sample_without_another_post():
    store, reader, slack = MemoryStore(), Reader(fixture()), FakeSlack()
    execute(reader, store, slack=slack)
    reader.current[fixture()["name"]] = fixture("health-closed")
    execute(reader, store, slack=slack)
    assert "RECOVERED" not in slack.updates[-1][1]["text"]
    reader.series = [point()]
    execute(reader, store, slack=slack)
    assert len(slack.posts) == 1 and len(slack.updates) == 2
    assert "RECOVERED" in slack.updates[-1][1]["text"]
    # A later sampling outage cannot erase a previously established closure proof.
    reader.series = []
    execute(reader, store, slack=slack, now="2026-10-22T14:00:00Z")
    assert len(slack.updates) == 2


def test_saved_pending_green_cannot_use_stale_proof_at_later_delivery():
    cfg, reader, store, slack = config(), Reader(fixture()), MemoryStore(), FakeSlack()
    execute(reader, store, slack=slack)
    reader.current[fixture()["name"]] = fixture("health-closed")
    reader.series = [point()]
    # Complete discovery persistence, then crash before transport can claim green.
    _, state = run.loaded(store, cfg, NOW)
    observations, proofs = run.discover(reader, state, cfg, NOW)
    run.persist_scan(store, cfg, observations, proofs, NOW, NOW)
    assert store.load().value["episodes"][fixture()["name"]]["recovery"]
    reader.series = []
    # Actual delivery entrypoint must re-read, even without another discovery pass.
    run.deliver(reader, store, cfg, slack, clock=lambda: "2026-10-22T13:20:00Z")
    assert "RECOVERED" not in slack.updates[-1][1]["text"]
    assert store.load().value["episodes"][fixture()["name"]]["recovery"] is None


def test_delivery_time_exact_get_or_heartbeat_failure_cannot_claim_green():
    cfg, reader, store, slack = config(), Reader(fixture()), MemoryStore(), FakeSlack()
    execute(reader, store, slack=slack)
    reader.current[fixture()["name"]] = fixture("health-closed")
    reader.series = [point()]
    _, state = run.loaded(store, cfg, NOW)
    observations, proofs = run.discover(reader, state, cfg, NOW)
    run.persist_scan(store, cfg, observations, proofs, NOW, NOW)
    before = store.load()
    reader.current.clear()
    with pytest.raises(api_errors.NotFound):
        run.deliver(reader, store, cfg, slack, clock=lambda: NOW)
    assert store.load() == before and not slack.updates


def test_pending_green_freshness_is_measured_after_slow_proof_reads():
    cfg, reader, store, slack = config(), Reader(fixture()), MemoryStore(), FakeSlack()
    execute(reader, store, slack=slack)
    reader.current[fixture()["name"]] = fixture("health-closed")
    reader.series = [point()]
    _, state = run.loaded(store, cfg, NOW)
    observations, proofs = run.discover(reader, state, cfg, NOW)
    run.persist_scan(store, cfg, observations, proofs, NOW, NOW)
    current = [NOW]
    def slow_points(now):
        current[0] = "2026-10-22T13:20:00Z"
        return [point()]
    reader.points = slow_points
    run.deliver(reader, store, cfg, slack, clock=lambda: current[0])
    assert "RECOVERED" not in slack.updates[-1][1]["text"]


def test_delivered_security_closes_same_parent_without_false_repair():
    raw = fixture("security-closed", state="OPEN")
    raw.pop("closeTime")
    reader, store, slack = Reader(raw), MemoryStore(), FakeSlack()
    execute(reader, store, slack=slack)
    reader.current[raw["name"]] = fixture("security-closed")
    execute(reader, store, slack=slack)
    assert len(slack.posts) == len(slack.updates) == 1
    assert "CLOSED (native observation only)" in slack.updates[0][1]["text"]
    assert "Repair remains unverified" in slack.updates[0][1]["text"]


def test_claim_corruption_and_changed_monitoring_open_identity_are_rejected():
    store, reader, slack = MemoryStore(), Reader(fixture()), FakeSlack()
    execute(reader, store, slack=slack)
    before = store.load()
    reader.current[fixture()["name"]]["openTime"] = "2026-10-22T12:45:00Z"
    with pytest.raises(model.Invalid, match="identity changed"):
        execute(reader, store, slack=slack)
    assert store.load() == before
    damaged = deepcopy(before.value)
    damaged["episodes"][fixture()["name"]]["desired_sha256"] = "0" * 64
    with pytest.raises(model.Invalid, match="rendering"):
        model.validate_state(damaged, config(), NOW)


def test_duplicate_conflicting_closure_is_not_accepted_as_quiet_success():
    first = fixture("health-closed")
    second = fixture("health-closed", closeTime="2026-10-22T13:04:44Z")
    reader, store = Reader(first), MemoryStore()
    reader.alerts = lambda *args, **kwargs: [first, second]
    with pytest.raises(model.Invalid, match="closure"):
        execute(reader, store)
    assert store.load().generation == 0


def test_exact_get_wrong_name_cannot_substitute_for_retained_parent():
    reader, store, slack = Reader(fixture()), MemoryStore(), FakeSlack()
    execute(reader, store, slack=slack)
    before = store.load()
    reader.alert = lambda _: fixture(name="projects/shared-datasets-1/alerts/other")
    with pytest.raises(model.Invalid, match="another"):
        execute(reader, store, slack=slack)
    assert store.load() == before


def test_generation_cas_claim_race_reads_winning_state_before_network():
    reader, store, slack = Reader(fixture()), MemoryStore(), FakeSlack()
    execute(reader, store, cfg=config(mode="shadow"), slack=slack)
    once = []
    def claim_race(value, generation, name):
        if not once and value["episodes"][fixture()["name"]]["operation"]:
            once.append(True)
            store.before_save = None
            # Simulate a different worker winning this exact durable post claim.
            store.save(value, generation)
            raise Conflict("other worker already owns the send")
    store.before_save = claim_race
    with pytest.raises(model.Invalid, match="inspection"):
        execute(reader, store, slack=slack)
    assert once and not slack.posts


@pytest.mark.parametrize("secret", ["projects/shared-datasets-1/secrets/synthetic-relay-test/versions/latest",
                                   "projects/foreign-project/secrets/synthetic-relay-test/versions/1"])
def test_secret_config_cannot_use_alias_or_foreign_project_name(secret):
    with pytest.raises(model.Invalid):
        config(slack_secret_version=secret)


def test_explicit_canonical_numeric_secret_resource_is_not_rewritten_or_guessed():
    exact = "projects/123456789/secrets/synthetic-relay-test/versions/7"
    cfg = config(slack_secret_version=exact)
    assert cfg["slack_secret_version"] == exact
    class Session:
        def __init__(self, returned):
            self.returned = returned
        def get(self, url, **kwargs):
            assert url == "https://secretmanager.googleapis.com/v1/" + exact + ":access"
            class Response:
                status_code = 200
                def iter_content(self, size):
                    yield model.canonical({"name": returned, "payload": {"data": "eG94Yi1zeW50aGV0aWM="}})
                def close(self):
                    pass
            returned = self.returned
            return Response()
    assert run.Monitoring(Session(exact), cfg).token() == "xoxb-synthetic"
    with pytest.raises(model.Invalid, match="version"):
        run.Monitoring(Session(exact.replace("/7", "/8")), cfg).token()


class FakeBlob:
    def __init__(self, objects, name):
        self.objects, self.name = objects, name
        self.generation = self.size = None

    def reload(self, **kwargs):
        assert kwargs == {"timeout": 15, "retry": None}
        if self.name not in self.objects:
            raise api_errors.NotFound("absent")
        body, self.generation = self.objects[self.name]
        self.size = len(body)

    def download_as_bytes(self, **kwargs):
        body, generation = self.objects[self.name]
        assert kwargs == {"if_generation_match": self.generation, "timeout": 15, "retry": None}
        if generation != self.generation:
            raise api_errors.PreconditionFailed("changed")
        return body

    def upload_from_string(self, body, *, content_type, if_generation_match, timeout, retry):
        assert content_type == "application/json" and timeout == 15 and retry is None
        actual = self.objects.get(self.name, (None, 0))[1]
        if if_generation_match != actual:
            raise api_errors.PreconditionFailed("changed")
        self.generation = actual + 1
        self.objects[self.name] = (body, self.generation)


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def bucket(self, name):
        assert name == config()["state_bucket"]
        return self

    def blob(self, name):
        assert name in {STATE_OBJECT, CAPABILITY_OBJECT}
        return FakeBlob(self.objects, name)


def test_actual_gcs_adapter_serialization_generation_cas_and_no_listing():
    storage = FakeStorage()
    store = Store(storage, config()["state_bucket"])
    assert store.load() == Snapshot(None, 0)
    first = store.save({"value": "first"}, 0)
    assert first == store.load() == Snapshot({"value": "first"}, 1)
    with pytest.raises(Conflict):
        store.save({"value": "overwrite"}, 0)
    assert store.load() == first
    with pytest.raises(model.Invalid):
        store.load("dataset/latest/rows.csv")


def test_capabilities_real_readback_marker_has_zero_public_delivery():
    store, slack, reader = Store(FakeStorage(), config()["state_bucket"]), FakeSlack(), Reader(fixture())
    result = run.verify_capabilities(reader, store, config(mode="shadow"), clock=lambda: NOW,
                                     slack_factory=lambda *args: slack, environ=CONTEXT)
    assert result["verified"] and result["public_operations"] == 0
    assert store.load().generation == 0
    assert store.load(CAPABILITY_OBJECT).generation == 1
    assert store.load(CAPABILITY_OBJECT).value["execution_name"] == CONTEXT["CLOUD_RUN_EXECUTION"]
    assert result["github_run_url"] == CONTEXT["SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"]
    assert not slack.posts and not slack.updates


@pytest.mark.parametrize("context", [{}, {**CONTEXT, "CLOUD_RUN_EXECUTION": "other-job-abc12"},
                                     {**CONTEXT, "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL": "https://github.com/foreign/repo/actions/runs/12345"}])
def test_capability_guard_precedes_any_live_read_or_private_write(context):
    reader, store = Reader(fixture()), MemoryStore()
    with pytest.raises(model.Invalid, match="requires"):
        run.verify_capabilities(reader, store, config(), environ=context)
    assert not reader.queries and not reader.exact and reader.tokens == 0 and not store.writes


@pytest.fixture
def http_boundary():
    calls, replies = [], []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            status, body = replies.pop(0)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = requests.Session()
    class Session:
        def get(self, url, **kwargs):
            assert url.startswith("https://monitoring.googleapis.com/v3/")
            assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
            local = f"http://127.0.0.1:{server.server_port}" + urlsplit(url).path
            return session.get(local, **kwargs)
    try:
        yield run.Monitoring(Session(), config()), replies, calls
    finally:
        server.shutdown()
        thread.join(5)
        session.close()
        server.server_close()


def test_actual_http_filters_full_pagination_and_estimated_size(http_boundary):
    reader, replies, calls = http_boundary
    replies.extend([(200, {"alerts": [fixture()], "nextPageToken": "second", "totalSize": 999}),
                    (200, {"alerts": [fixture("health-closed")]})])
    result = reader.alerts(config()["policies"][0]["name"], "2026-10-22T12:50:00Z")
    assert len(result) == 2
    query = parse_qs(urlsplit(calls[0][0]).query)
    assert query["filter"] == ['policy.name="projects/shared-datasets-1/alertPolicies/1980534029880229379" AND (state=OPEN OR open_time>="2026-10-22T12:50:00Z" OR close_time>="2026-10-22T12:50:00Z")']
    assert parse_qs(urlsplit(calls[1][0]).query)["pageToken"] == ["second"]


def test_partial_pagination_is_discarded_before_recognized_retry(http_boundary, monkeypatch):
    reader, replies, calls = http_boundary
    monkeypatch.setattr(operational_retry, "sleep", lambda _: None)
    replies.extend([(200, {"alerts": [fixture()], "nextPageToken": "second"}), (503, {"error": "outage"}),
                    (200, {"alerts": [fixture("health-closed")]})])
    result = reader.alerts(config()["policies"][0]["name"], NOW)
    assert result == [fixture("health-closed")]
    assert len(calls) == 3 and "pageToken" not in parse_qs(urlsplit(calls[2][0]).query)


def test_exhausted_inventory_outage_uses_one_bounded_shared_budget(http_boundary, monkeypatch):
    reader, replies, calls = http_boundary
    ticks = [0.0]
    monkeypatch.setattr(operational_retry, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(operational_retry, "sleep", lambda value: ticks.__setitem__(0, ticks[0] + value))
    replies.extend([(503, {"error": "unavailable"})] * 3)
    store = MemoryStore()
    with pytest.raises(api_errors.ServiceUnavailable):
        execute(reader, store)
    assert len(calls) == 3 and ticks[0] == 20
    assert store.load().generation == 0


def test_repeated_page_token_is_incomplete_not_success(http_boundary):
    reader, replies, calls = http_boundary
    replies.extend([(200, {"alerts": [], "nextPageToken": "same"})] * 2)
    store = MemoryStore()
    with pytest.raises(model.Invalid, match="repeated"):
        execute(reader, store)
    assert len(calls) == 2 and store.load().generation == 0


def test_time_series_partial_error_blocks_recovery_and_checkpoint(http_boundary):
    reader, replies, calls = http_boundary
    replies.append((200, {"timeSeries": [point()], "executionErrors": [{"code": 13}]}))
    with pytest.raises(model.Invalid, match="incomplete"):
        reader.points(NOW)
    assert len(calls) == 1


def test_streaming_success_after_absolute_deadline_cannot_advance_state(monkeypatch):
    ticks, reads = [0.0], []
    monkeypatch.setattr(operational_retry, "monotonic", lambda: ticks[0])
    class Session:
        def get(self, url, **kwargs):
            reads.append(url)
            class Response:
                status_code = 200
                def iter_content(self, size):
                    ticks[0] = 61
                    yield b'{}'
                def close(self):
                    pass
            return Response()
    store = MemoryStore()
    with pytest.raises(operational_retry.RetryBudgetExhausted):
        execute(run.Monitoring(Session(), config()), store)
    assert len(reads) == 1 and store.load().generation == 0


@pytest.mark.parametrize("response", [{"error": {"code": 500}}, {"unexpected": []}, {"alerts": {}},
                                     {"alerts": [], "totalSize": True}])
def test_http200_malformed_inventory_never_quietly_becomes_complete(response, http_boundary):
    reader, replies, calls = http_boundary
    replies.append((200, response))
    with pytest.raises(model.Invalid):
        reader.alerts(config()["policies"][0]["name"], NOW)
    assert len(calls) == 1


def test_http403_is_not_retried_and_cannot_advance_state(http_boundary):
    reader, replies, calls = http_boundary
    replies.append((403, {"error": {"message": "denied"}}))
    store = MemoryStore()
    with pytest.raises(api_errors.Forbidden):
        execute(reader, store)
    assert len(calls) == 1 and store.load().generation == 0


def test_last_page_failure_cannot_advance_cursor_or_send(http_boundary):
    reader, replies, calls = http_boundary
    replies.extend([(200, {"alerts": [fixture()], "nextPageToken": "tail"}),
                    (200, {"unexpected": "malformed tail"})])
    store, slack = MemoryStore(), FakeSlack()
    with pytest.raises(model.Invalid):
        execute(reader, store, slack=slack)
    assert len(calls) == 2 and store.load().generation == 0 and not slack.posts


def test_offline_immutable_image_cli_selftest_without_credentials_or_configuration():
    result = subprocess.run([sys.executable, "-m", "services.monitoring_slack.run", "--self-test"],
                            cwd=Path(__file__).parents[1], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"self_test": "passed", "public_operations": 0}
    dockerfile = (Path(__file__).parents[1] / "services/monitoring_slack/Dockerfile").read_text()
    assert "FROM python:3.12.12-slim-bookworm" in dockerfile
    assert "COPY --from=ghcr.io/astral-sh/uv:0.11.8" in dockerfile
    assert "RUN uv sync --locked --no-dev --no-install-project" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "services.monitoring_slack.run"]' in dockerfile
    assert "pip install" not in dockerfile
    for line in dockerfile.splitlines():
        if line.startswith("COPY ") and not line.startswith("COPY --from="):
            for source in line.split()[1:-1]:
                assert (Path(__file__).parents[1] / source).exists()
