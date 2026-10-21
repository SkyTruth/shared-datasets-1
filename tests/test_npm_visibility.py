"""Replay real verifier CLI/HTTP construction with offline transport and time."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import npm_visibility as visibility
from scripts import operational_retry as retry


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def publication(tmp_path, monkeypatch):
    manifest = json.loads((ROOT / "api/typescript/package.json").read_text())
    package = b"exact retained and tested SDK bytes"
    tarball = tmp_path / "tested.tgz"
    tarball.write_bytes(package)
    candidate = {"name": manifest["name"], "version": manifest["version"], "tarball": str(tarball),
                 "sha256": hashlib.sha256(package).hexdigest(),
                 "integrity": "sha512-" + base64.b64encode(hashlib.sha512(package).digest()).decode(),
                 "tested_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"elapsed": 0, "requests": []}))
    preload = tmp_path / "transport.mjs"
    preload.write_text('''
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
if (process.env.NPM_TEST_MODE === 'verifier_failure') throw new Error('verifier failed before a registry read');
if (process.env.NPM_TEST_MODE === 'bad_output') { console.log('invalid verifier protocol'); process.exit(0); }
globalThis.fetch = async (url, options) => {
  const state = JSON.parse(readFileSync(process.env.NPM_TEST_STATE));
  const candidate = JSON.parse(readFileSync(process.env.NPM_TEST_CANDIDATE));
  assert.equal(url, `https://registry.npmjs.org/@skytruth%2Fshared-datasets/${candidate.version}`);
  assert.equal(options.method, 'GET');
  assert.equal(options.redirect, 'error');
  assert.deepEqual(options.headers, { Accept: 'application/json', 'Cache-Control': 'no-cache' });
  assert(options.signal instanceof AbortSignal);
  assert.equal(process.env.GITHUB_OUTPUT, '');
  state.requests.push({ time: state.elapsed, url, method: options.method });
  writeFileSync(process.env.NPM_TEST_STATE, JSON.stringify(state));
  const mode = process.env.NPM_TEST_MODE;
  if (mode === 'transport') throw new TypeError('fetch failed', { cause: { code: 'ECONNRESET' } });
  if (mode === 'unknown_transport') throw new TypeError('fetch failed', { cause: { code: 'UNKNOWN' } });
  if (mode === 'denied') return new Response('{}', { status: 403 });
  if (mode === 'server_outage') return new Response('{}', { status: 503 });
  if (mode === 'malformed') return new Response('not JSON', { status: 200 });
  const visibleAfter = JSON.parse(process.env.NPM_TEST_VISIBLE_AFTER);
  if (visibleAfter === null || state.elapsed < visibleAfter) return new Response('package/version absent', { status: 404 });
  return new Response(JSON.stringify({ name: mode === 'wrong_package' ? 'other' : candidate.name,
    version: candidate.version, dist: { integrity: mode === 'wrong_integrity' ? 'wrong' : candidate.integrity } }), { status: 200 });
};
''')
    monkeypatch.setenv("NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --import=" + str(preload))
    monkeypatch.setenv("NPM_TEST_STATE", str(state))
    monkeypatch.setenv("NPM_TEST_CANDIDATE", str(path))
    monkeypatch.setenv("NPM_TEST_VISIBLE_AFTER", "0")
    monkeypatch.setenv("NPM_TEST_MODE", "normal")

    def clock():
        return json.loads(state.read_text())["elapsed"]

    def sleep(seconds):
        value = json.loads(state.read_text())
        value["elapsed"] += seconds
        state.write_text(json.dumps(value))

    monkeypatch.setattr(retry, "monotonic", clock)
    monkeypatch.setattr(retry, "sleep", sleep)
    return path, candidate, state


@pytest.mark.parametrize("visible_after,expected_times", [(0, [0]), (90, [0, 5, 20, 60, 180]),
                                                       (600, [0, 5, 20, 60, 180, 420, 900]),
                                                       (1740, [0, 5, 20, 60, 180, 420, 900, 1740])])
def test_normal_npm_propagation_uses_shared_absolute_schedule(publication, monkeypatch, visible_after, expected_times):
    candidate, _, state = publication
    monkeypatch.setenv("NPM_TEST_VISIBLE_AFTER", json.dumps(visible_after))
    visibility.confirm(candidate)
    assert [request["time"] for request in json.loads(state.read_text())["requests"]] == expected_times


@pytest.mark.parametrize("mode", ["normal", "transport", "server_outage"])
def test_absent_package_and_recognized_transport_exhaust_once_without_publishing(publication, monkeypatch, mode):
    candidate, _, state = publication
    monkeypatch.setenv("NPM_TEST_VISIBLE_AFTER", "null")
    monkeypatch.setenv("NPM_TEST_MODE", mode)
    with pytest.raises(visibility.VisibilityPending):
        visibility.confirm(candidate)
    records = json.loads(state.read_text())["requests"]
    assert [request["time"] for request in records] == [0, 5, 20, 60, 180, 420, 900, 1740]
    assert all(request["method"] == "GET" for request in records)


@pytest.mark.parametrize("mode", ["wrong_integrity", "wrong_package", "malformed", "denied", "unknown_transport", "verifier_failure", "bad_output"])
def test_integrity_or_verifier_failures_are_fatal_and_never_retried(publication, monkeypatch, mode):
    candidate, _, state = publication
    monkeypatch.setenv("NPM_TEST_MODE", mode)
    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        visibility.confirm(candidate)
    observed = json.loads(state.read_text())
    assert observed["elapsed"] == 0 and len(observed["requests"]) <= 1


@pytest.mark.parametrize("field,value", [("tested_sha", "a" * 40), ("sha256", "0" * 64),
                                        ("integrity", "sha512-wrong"), ("version", "999.0.0")])
def test_wrong_retained_bytes_or_checkout_fail_before_any_registry_request(publication, field, value):
    candidate_path, candidate, state = publication
    candidate_path.write_text(json.dumps({**candidate, field: value}))
    with pytest.raises((ValueError, visibility.release.DeploymentError)):
        visibility.confirm(candidate_path)
    assert json.loads(state.read_text())["requests"] == []


def test_nested_source_operation_does_not_replenish_npm_retry_budget(publication, monkeypatch):
    candidate, _, state = publication
    monkeypatch.setenv("NPM_TEST_VISIBLE_AFTER", "null")
    with retry.operation(profile=retry.UNATTENDED):
        for _ in range(2):
            with pytest.raises(visibility.VisibilityPending):
                visibility.confirm(candidate)
    requests = json.loads(state.read_text())["requests"]
    assert [request["time"] for request in requests] == [0, 5, 20, 60, 180, 420, 900, 1740]


def test_an_existing_short_operation_cannot_gain_a_larger_nested_npm_budget(publication, monkeypatch):
    candidate, _, state = publication
    monkeypatch.setenv("NPM_TEST_VISIBLE_AFTER", "null")
    with retry.operation(profile=retry.SHORT), pytest.raises(visibility.VisibilityPending):
        visibility.confirm(candidate)
    assert [request["time"] for request in json.loads(state.read_text())["requests"]] == [0, 5, 20]


def test_propagation_cli_requires_explicit_accepted_publication_before_any_read(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/npm_visibility.py"), "--candidate", str(tmp_path / "missing.json")],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and "require confirmed accepted publication" in result.stderr
    assert "FileNotFoundError" not in result.stderr
