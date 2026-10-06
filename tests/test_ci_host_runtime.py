"""Keep host image checks connected to Docker without inherited cloud identity."""

import json
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from scripts.ci_host_runtime import isolated_host_environment


def context(host="unix:///owned/docker.sock", *, tls_path=None, files=(), skip_verify=False):
    return json.dumps([{
        "Endpoints": {"docker": {"Host": host, "SkipTLSVerify": skip_verify}},
        "TLSMaterial": {"docker": list(files)}, "Storage": {"TLSPath": str(tls_path)},
    }])


def test_secret_environment_and_default_adc_are_not_forwarded(tmp_path):
    original = tmp_path / "original-home"
    adc = original / ".config/gcloud/application_default_credentials.json"
    adc.parent.mkdir(parents=True)
    adc.write_text('{"token": "must-not-be-forwarded"}')
    docker = original / ".docker"
    docker.mkdir()
    (docker / "config.json").write_text('{"auths": {"private-registry": "secret"}}')
    caller = {
        "PATH": "/owned/bin", "HOME": str(original), "DOCKER_HOST": "unix:///owned/docker.sock",
        "DOCKER_CONFIG": str(docker), "GOOGLE_APPLICATION_CREDENTIALS": str(adc), "GOOGLE_CLOUD_PROJECT": "production",
        "GITHUB_TOKEN": "secret", "GH_TOKEN": "secret", "GITHUB_RUN_ID": "123", "GITHUB_EVENT_PATH": "/real-event",
        "SSH_AUTH_SOCK": "/agent", "AWS_SECRET_ACCESS_KEY": "secret", "NPM_TOKEN": "secret",
        "HTTPS_PROXY": "http://proxy.test", "SSL_CERT_FILE": "/owned/ca.pem", "LANG": "C.UTF-8",
    }
    with isolated_host_environment(tmp_path, caller) as environment:
        assert environment["PATH"] == caller["PATH"]
        assert environment["HTTPS_PROXY"] == caller["HTTPS_PROXY"]
        assert environment["SSL_CERT_FILE"] == caller["SSL_CERT_FILE"]
        assert not any(key in environment for key in (
            "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_RUN_ID",
            "GITHUB_EVENT_PATH", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY", "NPM_TOKEN",
        ))
        assert Path(environment["HOME"]) != original
        assert not (Path(environment["HOME"]) / ".config/gcloud/application_default_credentials.json").exists()
        assert not (Path(environment["CLOUDSDK_CONFIG"]) / "application_default_credentials.json").exists()
        assert json.loads((Path(environment["DOCKER_CONFIG"]) / "config.json").read_text()) == {}
        isolated_home = Path(environment["HOME"])
    assert not isolated_home.exists()
    assert adc.exists()
    assert json.loads((docker / "config.json").read_text())["auths"]


def test_explicit_endpoint_needs_no_context_or_user_configuration(tmp_path):
    with mock.patch("scripts.ci_host_runtime.subprocess.check_output") as inspect:
        with isolated_host_environment(tmp_path, {"PATH": "/bin", "DOCKER_HOST": "tcp://localhost:2375"}) as environment:
            assert environment["DOCKER_HOST"] == "tcp://localhost:2375"
            assert "DOCKER_TLS_VERIFY" not in environment
        inspect.assert_not_called()


def test_context_endpoint_is_resolved_before_user_config_is_hidden(tmp_path):
    caller = {"PATH": "/bin", "HOME": "/caller", "DOCKER_CONTEXT": "selected", "DOCKER_HOST": "unix:///overridden.sock"}
    with mock.patch("scripts.ci_host_runtime.subprocess.check_output", return_value=context()) as inspect:
        with isolated_host_environment(tmp_path, caller) as environment:
            assert environment["DOCKER_HOST"] == "unix:///owned/docker.sock"
            assert "DOCKER_CONTEXT" not in environment
    assert inspect.call_args.args[0] == ["docker", "context", "inspect", "selected"]
    assert inspect.call_args.kwargs["env"] is caller


@pytest.mark.parametrize("skip_verify", [False, True])
def test_context_preserves_only_required_daemon_tls_material(tmp_path, skip_verify):
    original = tmp_path / "tls-store" / "docker"
    original.mkdir(parents=True)
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (original / name).write_text("transport-only-" + name)
    (original / "registry-token").write_text("not-transport")
    metadata = context("tcp://daemon.test:2376", tls_path=original.parent, files=("ca.pem", "cert.pem", "key.pem"), skip_verify=skip_verify)
    with mock.patch("scripts.ci_host_runtime.subprocess.check_output", return_value=metadata):
        with isolated_host_environment(tmp_path, {"PATH": "/bin"}) as environment:
            copied = Path(environment["DOCKER_CERT_PATH"])
            assert copied != original
            assert {path.name for path in copied.iterdir()} == {"ca.pem", "cert.pem", "key.pem"}
            assert (copied / "key.pem").read_text() == "transport-only-key.pem"
            assert (copied / "key.pem").stat().st_mode & 0o777 == 0o600
            assert environment["DOCKER_TLS" if skip_verify else "DOCKER_TLS_VERIFY"] == "1"
    assert not copied.exists()


def test_explicit_tls_copies_daemon_keys_without_registry_auth(tmp_path):
    source = tmp_path / "certificates"
    source.mkdir()
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (source / name).write_text(name)
    (source / "config.json").write_text('{"auths": {"private": "secret"}}')
    caller = {"PATH": "/bin", "DOCKER_HOST": "tcp://daemon.test:2376", "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": str(source)}
    with isolated_host_environment(tmp_path, caller) as environment:
        copied = Path(environment["DOCKER_CERT_PATH"])
        assert {path.name for path in copied.iterdir()} == {"ca.pem", "cert.pem", "key.pem"}
        assert not (copied / "config.json").exists()


@pytest.mark.parametrize("metadata", ["[]", "{}", context("ssh://agent@host"), context(files=("registry-auth",)), context(files=("cert.pem",))])
def test_missing_or_unsupported_context_fails_without_transport_fallback(tmp_path, metadata):
    with mock.patch("scripts.ci_host_runtime.subprocess.check_output", return_value=metadata):
        with pytest.raises(ValueError):
            with isolated_host_environment(tmp_path, {"PATH": "/bin"}):
                pytest.fail("unsupported context was accepted")


def test_missing_context_is_reported_as_configuration_failure(tmp_path):
    with mock.patch("scripts.ci_host_runtime.subprocess.check_output", side_effect=subprocess.CalledProcessError(1, ["docker"])):
        with pytest.raises(ValueError, match="Cannot resolve the Docker context"):
            with isolated_host_environment(tmp_path, {"PATH": "/bin"}):
                pytest.fail("missing context was accepted")


def test_missing_declared_tls_file_fails_before_using_another_identity(tmp_path):
    metadata = context("tcp://daemon.test:2376", tls_path=tmp_path / "missing", files=("ca.pem",))
    with mock.patch("scripts.ci_host_runtime.subprocess.check_output", return_value=metadata):
        with pytest.raises(ValueError, match="Missing or invalid Docker transport TLS file"):
            with isolated_host_environment(tmp_path, {"PATH": "/bin"}):
                pytest.fail("missing TLS file was accepted")
