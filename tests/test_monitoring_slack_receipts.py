"""Receipt-only controls through canonical CLI, exact HTTP and cold CAS state."""
from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from threading import Thread
from urllib.parse import parse_qs, urlsplit
import urllib.request

import pytest
import requests

from scripts.slack_incident_api import ReadOnlySlack, SlackError
from services.monitoring_slack import model, receipts, run
from services.monitoring_slack.store import Conflict, Store
from test_monitoring_slack_relay import (CONTEXT, FakeSlack, FakeStorage,
                                        MemoryStore, Reader, config, execute,
                                        fixture, point)

CONTROL_NOW = "2026-10-22T13:10:00Z"
CONTROL_ENV = {**CONTEXT, "CLOUD_RUN_EXECUTION": "monitoring-slack-relay-ctl12"}
TS = "1000000000.000001"


@pytest.fixture(autouse=True)
def managed(monkeypatch):
    for key, value in CONTEXT.items():
        monkeypatch.setenv(key, value)


def held(*, phase="sending", kind="post"):
    cfg, store, reader, slack = config(), MemoryStore(), Reader(fixture()), FakeSlack()
    if kind == "update":
        execute(reader, store, slack=slack)
        reader.current[fixture()["name"]] = fixture("health-closed")
        reader.series = [point()]
    if phase == "claimed":
        def before_send(value, generation, name):
            operation = value["episodes"][fixture()["name"]]["operation"]
            if operation and operation["phase"] == "sending":
                raise OSError("crash before pre-send CAS")
        store.before_save = before_send
    else:
        slack.failure = SlackError("unconfirmed call")
    with pytest.raises((OSError, SlackError)):
        execute(reader, store, slack=slack)
    store.before_save = None
    operation = store.load().value["episodes"][fixture()["name"]]["operation"]
    assert operation["phase"] == phase
    request = {"schema": "monitoring-slack-receipt-request-v1", "request_id": "11111111-2222-4333-8444-555555555555",
               "config_binding": cfg.binding, "alert_name": fixture()["name"], "operation_id": operation["id"],
               "operation_sha256": operation["sha256"], "expected_generation": store.load().generation,
               "outcome": "confirm_not_delivered" if phase == "claimed" else "confirm_message",
               "message_ts": None if phase == "claimed" else TS}
    return cfg, store, operation, request


def metadata(cfg, name, *, terminal=False, request=None):
    raw = Reader().execution(name)
    if terminal:
        raw.update(completionTime="2026-10-22T13:06:00Z", runningCount=0, failedCount=1, retriedCount=0,
                   generation="1", observedGeneration="1", reconciling=False,
                   conditions=[{"type": "Completed", "state": "CONDITION_FAILED"}])
    if request is not None:
        raw.update(uid="22222222-2222-4222-8222-222222222222", createTime="2026-10-22T13:07:00Z",
                   startTime="2026-10-22T13:08:00Z", creator="shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com")
        container = raw["template"]["containers"][0]
        container["args"] = ["--reconcile-receipt-json", model.canonical(request).decode()]
        container["env"] = [{"name": "MONITORING_SLACK_CONFIG", "value": model.canonical(cfg.value).decode()},
                            {"name": "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL", "value": CONTROL_ENV["SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"]}]
    return raw


class ReceiptReader:
    def __init__(self, cfg, operation, request):
        self.calls, self.tokens = [], 0
        controller = model.execution_name(cfg, CONTROL_ENV)
        self.rows = {controller: metadata(cfg, controller, request=request),
                     operation["writer"]["execution"]: metadata(cfg, operation["writer"]["execution"], terminal=True)}
    def execution(self, name):
        self.calls.append(name)
        return deepcopy(self.rows[name])
    def token(self):
        self.tokens += 1
        return "xoxb-synthetic"
    def alert(self, name):
        raise AssertionError("receipt must not establish Monitoring lifecycle")
    def points(self, now):
        raise AssertionError("receipt must not establish heartbeat recovery")


def posted(cfg, operation):
    return {"type": "message", "subtype": "bot_message", "ts": TS, "user": cfg["slack_user"],
            "team": cfg["slack_team"], "bot_id": "BTEST", **deepcopy(operation["payload"])}


def slack_transport(cfg, message, calls):
    def opener(request, timeout):
        parsed = urlsplit(request.full_url)
        method = parsed.path.rsplit("/", 1)[1]
        assert timeout == 30
        assert method in {"auth.test", "conversations.history"}, "receipt attempted a public write"
        calls.append(method)
        if method == "auth.test":
            result = {"ok": True, "bot_id": "BTEST", "team_id": cfg["slack_team"], "user_id": cfg["slack_user"]}
        else:
            assert request.get_method() == "GET" and request.data is None
            assert parse_qs(parsed.query) == {"channel": [cfg["slack_channel"]], "oldest": [TS], "latest": [TS],
                                              "inclusive": ["true"], "limit": ["1"]}
            result = {"ok": True, "messages": [message], "has_more": False, "response_metadata": {"next_cursor": ""}}
        return io.BytesIO(model.canonical(result))
    return lambda token, channel: ReadOnlySlack(token, channel, opener=opener)


def apply(cfg, store, operation, request, *, reader=None, message=None, calls=None, environment=CONTROL_ENV):
    calls = [] if calls is None else calls
    return receipts.reconcile(reader or ReceiptReader(cfg, operation, request), store, cfg, model.canonical(request).decode(),
                              environment=environment, clock=lambda: CONTROL_NOW,
                              slack_factory=slack_transport(cfg, posted(cfg, operation) if message is None else message, calls))


@pytest.mark.parametrize("kind", ["post", "update"])
def test_exact_message_receipt_cold_state_preserves_desired_clocks_and_sends_nothing(kind, capsys):
    cfg, store, operation, request = held(kind=kind)
    before = deepcopy(store.load().value)
    calls = []
    capsys.readouterr()
    result = apply(cfg, store, operation, request, calls=calls)
    assert result["verified"] and result["public_operations"] == 0 and result["replayed"] is False
    after = model.validate_state(store.load().value, cfg, CONTROL_NOW)
    expected = deepcopy(before)
    episode = expected["episodes"][request["alert_name"]]
    episode.update(ts=TS, applied_sha256=operation["sha256"], operation=None)
    expected["receipts"][request["request_id"]] = result["receipt"]
    assert after == expected
    assert calls == ["auth.test", "conversations.history"]
    assert '"kind":"job_outcome"' not in capsys.readouterr().err


def test_receipt_for_old_green_never_rewrites_new_desired_recovery_or_coverage():
    cfg, store, operation, request = held(kind="update")
    before = store.load()
    proof = {"point_at": "2026-10-22T13:09:50Z", "verified_at": CONTROL_NOW,
             "policy_created_at": cfg["health_policy_created_at"]}
    changed = model.merge(before.value, {}, {request["alert_name"]: proof}, cfg, CONTROL_NOW, now=CONTROL_NOW)
    store.save(changed, before.generation)
    request["expected_generation"] = store.load().generation
    result = apply(cfg, store, operation, request)
    after = store.load().value
    episode = after["episodes"][request["alert_name"]]
    assert result["public_operations"] == 0
    assert after["covered_through"] == CONTROL_NOW and episode["recovery"] == proof
    assert episode["desired_sha256"] == changed["episodes"][request["alert_name"]]["desired_sha256"]
    assert episode["applied_sha256"] == operation["sha256"] != episode["desired_sha256"]


def test_receipt_cannot_adopt_a_parent_already_owned_by_another_alert():
    cfg, store, operation, request = held()
    before = store.load()
    other = model.normalize(fixture("security-closed"), cfg, CONTROL_NOW)
    changed = model.merge(before.value, {other["name"]: other}, {}, cfg, CONTROL_NOW, now=CONTROL_NOW)
    changed["episodes"][other["name"]].update(ts=TS, applied_sha256=changed["episodes"][other["name"]]["desired_sha256"])
    store.save(changed, before.generation)
    request["expected_generation"] = store.load().generation
    reader = ReceiptReader(cfg, operation, request)
    with pytest.raises(model.Invalid, match="another incident"):
        apply(cfg, store, operation, request, reader=reader)
    assert store.load().value == changed and reader.tokens == 0


@pytest.mark.parametrize("environment", [{}, {**CONTROL_ENV, "CLOUD_RUN_TASK_ATTEMPT": "1"},
                                       {**CONTROL_ENV, "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL": "https://github.com/foreign/repo/actions/runs/1"}])
def test_receipt_managed_protected_context_is_required_before_any_api(environment):
    cfg, store, operation, request = held()
    reader, before = ReceiptReader(cfg, operation, request), store.load()
    with pytest.raises(model.Invalid):
        apply(cfg, store, operation, request, reader=reader, environment=environment)
    assert store.load() == before and not reader.calls and reader.tokens == 0


@pytest.mark.parametrize("kind", ["post", "update"])
def test_proven_pre_send_terminal_claim_can_clear_without_message_or_secret_access(kind):
    cfg, store, operation, request = held(phase="claimed", kind=kind)
    before = deepcopy(store.load().value)
    reader, calls = ReceiptReader(cfg, operation, request), []
    result = apply(cfg, store, operation, request, reader=reader, calls=calls)
    assert result["verified"] and not calls and reader.tokens == 0
    after = store.load().value
    expected = deepcopy(before)
    expected["episodes"][request["alert_name"]]["operation"] = None
    expected["receipts"][request["request_id"]] = result["receipt"]
    assert after == expected


def test_sending_unknown_cannot_be_reset_by_elapsed_time_or_operator_assertion():
    cfg, store, operation, request = held()
    request.update(outcome="confirm_not_delivered", message_ts=None)
    before = store.load()
    with pytest.raises(model.Invalid, match="non-delivery"):
        apply(cfg, store, operation, request)
    assert store.load() == before


@pytest.mark.parametrize("mutation", [
    lambda raw: raw.pop("completionTime"),
    lambda raw: raw.update(runningCount=1),
    lambda raw: raw.update(retriedCount=1),
    lambda raw: raw.update(failedCount=0),
    lambda raw: raw.update(reconciling=True),
    lambda raw: raw.update(observedGeneration="0"),
    lambda raw: raw.update(conditions=[{"type": "Completed", "state": "CONDITION_PENDING"}]),
    lambda raw: raw.update(uid="33333333-3333-4333-8333-333333333333"),
    lambda raw: raw["template"]["containers"][0].update(image="tag:unverified"),
    lambda raw: raw["template"].update(maxRetries=1),
    lambda raw: raw["template"].pop("maxRetries"),
    lambda raw: raw.update(job=config()["relay_job"]),
    lambda raw: raw.pop("observedGeneration"),
    lambda raw: raw.update(completionTime="2026-10-22T13:11:00Z"),
])
def test_exact_old_writer_must_be_terminal_unchanged_and_without_retry(mutation):
    cfg, store, operation, request = held(phase="claimed")
    reader = ReceiptReader(cfg, operation, request)
    mutation(reader.rows[operation["writer"]["execution"]])
    before = store.load()
    with pytest.raises(model.Invalid):
        apply(cfg, store, operation, request, reader=reader)
    assert store.load() == before


def test_actual_provider_short_job_and_omitted_zero_terminal_counters_are_valid():
    cfg, store, operation, request = held(phase="claimed")
    reader = ReceiptReader(cfg, operation, request)
    raw = reader.rows[operation["writer"]["execution"]]
    # Exact v2 provider GET: parent job is short, int64 generations are strings,
    # and default false/zero output-only fields are omitted, not null.
    assert raw["job"] == "monitoring-slack-relay"
    for field in ("runningCount", "retriedCount", "reconciling"):
        raw.pop(field)
    assert apply(cfg, store, operation, request, reader=reader)["verified"]


@pytest.mark.parametrize("mutation", [
    lambda raw: raw.update(creator="someone@example.test"),
    lambda raw: raw["template"]["containers"][0].update(args=[]),
    lambda raw: raw["template"].update(serviceAccount="different@example.test"),
    lambda raw: raw["template"]["containers"][0].update(image="tag:unverified"),
])
def test_current_control_requires_exact_protected_image_args_identity_and_context(mutation):
    cfg, store, operation, request = held()
    reader = ReceiptReader(cfg, operation, request)
    mutation(reader.rows[model.execution_name(cfg, CONTROL_ENV)])
    before = store.load()
    with pytest.raises(model.Invalid):
        apply(cfg, store, operation, request, reader=reader)
    assert store.load() == before


@pytest.mark.parametrize("mutation", [
    lambda msg: msg.update(user="UOTHER"),
    lambda msg: msg.update(team="TOTHER"),
    lambda msg: msg.update(ts="1000000000.000002"),
    lambda msg: msg.update(thread_ts="1000000000.000002"),
    lambda msg: msg.update(subtype="message_changed"),
    lambda msg: msg.update(text="similar content is not a receipt"),
    lambda msg: msg["blocks"][0]["text"].update(text="modified"),
    lambda msg: msg.update(attachments=[{"text": "unapproved content"}]),
])
def test_wrong_owner_message_or_content_never_confirms(mutation):
    cfg, store, operation, request = held()
    message = posted(cfg, operation)
    mutation(message)
    before = store.load()
    with pytest.raises((model.Invalid, SlackError)):
        apply(cfg, store, operation, request, message=message)
    assert store.load() == before


def test_only_documented_inert_block_fields_can_normalize():
    cfg, store, operation, request = held()
    message = posted(cfg, operation)
    for index, block in enumerate(message["blocks"]):
        block["block_id"] = f"generated-{index}"
        if block.get("text", {}).get("type") == "mrkdwn":
            block["text"]["verbatim"] = False
    assert apply(cfg, store, operation, request, message=message)["verified"]


def test_receipt_is_idempotent_and_uuid_reuse_cannot_retarget_changed_request():
    cfg, store, operation, request = held()
    first = apply(cfg, store, operation, request)
    generation = store.load().generation
    calls = []
    second = apply(cfg, store, operation, request, calls=calls)
    assert second["replayed"] and second["receipt"] == first["receipt"]
    assert store.load().generation == generation and not calls
    changed = {**request, "message_ts": "1000000000.000002"}
    with pytest.raises(model.Invalid, match="UUID"):
        apply(cfg, store, operation, changed)
    assert store.load().generation == generation


def test_unknown_cas_ack_can_be_replayed_as_receipt_without_public_transport():
    cfg, store, operation, request = held()
    actual = store.save
    def commit_then_disconnect(value, generation, name="monitoring-slack/state-v1.json"):
        actual(value, generation, name)
        raise OSError("control acknowledgement lost")
    store.save = commit_then_disconnect
    with pytest.raises(OSError):
        apply(cfg, store, operation, request)
    store.save = actual
    calls = []
    assert apply(cfg, store, operation, request, calls=calls)["replayed"]
    assert not calls


def test_cas_conflict_refuses_retarget_and_preserves_exact_pending_claim():
    cfg, store, operation, request = held()
    before = store.load()
    actual = store.save
    def conflict(value, generation, name="monitoring-slack/state-v1.json"):
        actual(before.value, generation, name)  # another complete writer wins
        return actual(value, generation, name)
    store.save = conflict
    with pytest.raises(Conflict):
        apply(cfg, store, operation, request)
    assert store.load().value == before.value


@pytest.mark.parametrize("change", [{"expected_generation": 999}, {"operation_id": "0" * 32},
                                    {"operation_sha256": "0" * 64}, {"config_binding": "0" * 64},
                                    {"request_id": "not-a-uuid"}, {"actor": "self-asserted"}])
def test_request_exact_fields_and_cold_claim_binding_are_required(change):
    cfg, store, operation, request = held()
    before = store.load()
    with pytest.raises(model.Invalid):
        apply(cfg, store, operation, {**request, **change})
    assert store.load() == before


def test_image_upgrade_preserves_history_but_cannot_reconcile_old_unresolved_image():
    cfg, store, operation, request = held()
    upgraded = config(relay_image=cfg["relay_image"].replace("a" * 64, "b" * 64))
    assert cfg.binding == upgraded.binding
    model.validate_state(store.load().value, upgraded, CONTROL_NOW)
    reader = ReceiptReader(upgraded, operation, request)
    controller = reader.rows[model.execution_name(upgraded, CONTROL_ENV)]
    controller["template"]["containers"][0]["image"] = upgraded["relay_image"]
    with pytest.raises(model.Invalid, match="historical writer image"):
        apply(upgraded, store, operation, request, reader=reader)
    assert store.load().value["episodes"][request["alert_name"]]["operation"] == operation


def test_image_upgrade_preserves_completed_parent_and_receipt_audit_without_takeover():
    cfg, store, operation, request = held()
    apply(cfg, store, operation, request)
    before = store.load()
    upgraded = config(relay_image=cfg["relay_image"].replace("a" * 64, "b" * 64))
    assert upgraded.binding == cfg.binding
    assert model.validate_state(before.value, upgraded, CONTROL_NOW) == before.value
    episode = before.value["episodes"][request["alert_name"]]
    assert episode["ts"] == TS and episode["operation"] is None
    assert before.value["receipts"][request["request_id"]]["writer"]["image"] == cfg["relay_image"]


def test_pre_send_cas_conflict_keeps_claimed_and_never_reaches_slack():
    store, reader, slack = MemoryStore(), Reader(fixture()), FakeSlack()
    def conflict_sending(value, generation, name):
        operation = value["episodes"][fixture()["name"]]["operation"]
        if operation and operation["phase"] == "sending":
            raise Conflict("another writer changed the generation")
    store.before_save = conflict_sending
    with pytest.raises(Conflict):
        execute(reader, store, slack=slack)
    operation = store.load().value["episodes"][fixture()["name"]]["operation"]
    assert operation["phase"] == "claimed" and not slack.posts


def test_read_only_slack_capability_refuses_all_chat_writes_before_http():
    client = ReadOnlySlack("synthetic", config()["slack_channel"], opener=lambda *args: pytest.fail("write reached HTTP"))
    with pytest.raises(SlackError, match="forbids"):
        client.post({"text": "forbidden"})
    with pytest.raises(SlackError, match="forbids"):
        client.update(TS, {"text": "forbidden"})


@pytest.mark.parametrize("response", [
    {"ok": True, "messages": [], "has_more": False, "response_metadata": {"next_cursor": ""}},
    {"ok": False, "error": "missing_scope"},
    {"ok": True, "messages": [{"text": "unexpected"}], "has_more": False},
    {"ok": True, "messages": [], "has_more": True},
    {"ok": True, "messages": [], "response_metadata": {"next_cursor": "more"}},
    {"ok": True, "messages": [], "error": "contradictory"},
])
def test_history_capability_exact_empty_exclusive_get_and_no_scope_fallback(response):
    calls = []
    def opener(request, timeout):
        calls.append(request)
        parsed = urlsplit(request.full_url)
        assert request.get_method() == "GET" and request.data is None and timeout == 30
        assert parsed.path == "/api/conversations.history"
        assert parse_qs(parsed.query) == {"channel": [config()["slack_channel"]], "oldest": ["946684800.000000"],
                                         "latest": ["946684800.000000"], "inclusive": ["false"], "limit": ["1"]}
        return io.BytesIO(model.canonical(response))
    slack = ReadOnlySlack("xoxb-synthetic", config()["slack_channel"], opener=opener)
    if response == {"ok": True, "messages": [], "has_more": False, "response_metadata": {"next_cursor": ""}}:
        assert slack.history_capability() is True
    else:
        with pytest.raises(SlackError):
            slack.history_capability()
    assert len(calls) == 1


@pytest.mark.parametrize("response", [{"ok": True, "messages": []}, {"ok": True, "messages": [], "has_more": True},
                                     {"ok": True, "messages": [], "response_metadata": {"next_cursor": "tail"}}])
def test_empty_or_incomplete_history_does_not_prove_non_delivery(response):
    client = ReadOnlySlack("synthetic", config()["slack_channel"], opener=lambda *args, **kwargs: io.BytesIO(model.canonical(response)))
    with pytest.raises(SlackError):
        client.message(TS)


def test_actual_cli_exact_http_cold_gcs_roundtrip_has_zero_public_writes(monkeypatch, capsys):
    cfg, memory, operation, request = held()
    storage = FakeStorage()
    cold_store = Store(storage, cfg["state_bucket"])
    cold_store.save(memory.load().value, 0)
    request["expected_generation"] = cold_store.load().generation
    request_json = model.canonical(request).decode()
    source = ReceiptReader(cfg, operation, request)
    message, calls = posted(cfg, operation), []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            calls.append(("GET", parsed.path))
            if parsed.path.startswith("/v2/"):
                value = source.execution(parsed.path.removeprefix("/v2/"))
            elif parsed.path.startswith("/v1/"):
                assert parsed.path == "/v1/" + cfg["slack_secret_version"] + ":access"
                value = {"name": cfg["slack_secret_version"], "payload": {"data": "eG94Yi1zeW50aGV0aWM="}}
            else:
                assert parsed.path == "/api/conversations.history"
                assert parse_qs(parsed.query) == {"channel": [cfg["slack_channel"]], "oldest": [TS], "latest": [TS], "inclusive": ["true"], "limit": ["1"]}
                value = {"ok": True, "messages": [message], "has_more": False}
            self.reply(value)
        def do_POST(self):
            calls.append(("POST", urlsplit(self.path).path))
            assert self.path == "/api/auth.test", "receipt attempted a chat write"
            assert self.rfile.read(int(self.headers["Content-Length"])) == b"{}"
            self.reply({"ok": True, "bot_id": "BTEST", "user_id": cfg["slack_user"], "team_id": cfg["slack_team"]})
        def reply(self, value):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(model.canonical(value))
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = requests.Session()
    base = f"http://127.0.0.1:{server.server_port}"
    class Session:
        def get(self, url, **kwargs):
            parsed = urlsplit(url)
            assert parsed.netloc in {"run.googleapis.com", "secretmanager.googleapis.com"}
            return session.get(base + parsed.path, **kwargs)
    def opener(request, timeout):
        parsed = urlsplit(request.full_url)
        assert parsed.netloc == "slack.com"
        return urllib.request.urlopen(urllib.request.Request(base + parsed.path + ("?" + parsed.query if parsed.query else ""),
                                      data=request.data, headers=dict(request.headers), method=request.get_method()), timeout=timeout)
    try:
        monkeypatch.setenv("MONITORING_SLACK_CONFIG", model.canonical(cfg.value).decode())
        for key, value in CONTROL_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(run.google.auth, "default", lambda **kwargs: (object(), cfg["project"]))
        monkeypatch.setattr(run, "AuthorizedSession", lambda credentials: Session())
        monkeypatch.setattr(run.storage, "Client", lambda **kwargs: storage)
        monkeypatch.setattr(run, "ReadOnlySlack", lambda token, channel: ReadOnlySlack(token, channel, opener=opener))
        monkeypatch.setitem(receipts.reconcile.__kwdefaults__, "clock", lambda: CONTROL_NOW)
        capsys.readouterr()
        assert run.main(["--reconcile-receipt-json", request_json]) == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        receipt = model.validate_receipt(result["receipt"], cfg, CONTROL_NOW)
        cold = Store(storage, cfg["state_bucket"]).load()
        assert cold.value["receipts"][request["request_id"]] == receipt
        assert cold.generation == result["state_generation"] and result["public_operations"] == 0
        assert '"kind":"job_outcome"' not in captured.err
        assert calls == [("GET", "/v2/" + model.execution_name(cfg, CONTROL_ENV)),
                         ("GET", "/v2/" + operation["writer"]["execution"]),
                         ("GET", "/v1/" + cfg["slack_secret_version"] + ":access"),
                         ("POST", "/api/auth.test"), ("GET", "/api/conversations.history")]
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
        session.close()
