"""An operationally credential-free environment for host Docker validation.

This isolates default configuration and inherited credentials. Host Python and
Docker retain their normal filesystem/API capabilities; this is not a sandbox.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit

NETWORK_ENV = {
    "PATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "DOCKER_API_VERSION",
}
TLS_FILES = frozenset({"ca.pem", "cert.pem", "key.pem"})


def endpoint_kind(host: str) -> str:
    address = urlsplit(host)
    if address.username or address.password or address.query or address.fragment:
        raise ValueError("Docker endpoint must not embed authentication or query data")
    if address.scheme == "unix" and not address.netloc and address.path.startswith("/"):
        return "unix"
    if address.scheme == "tcp" and address.hostname and address.path in {"", "/"}:
        _ = address.port  # Reject malformed port declarations before running Docker.
        return "tcp"
    raise ValueError("Host image validation supports explicit Unix or TCP Docker transport; SSH/agent transport is unsupported")


def transport(caller: dict[str, str]) -> tuple[str, bool, bool, Path | None, set[str]]:
    # DOCKER_CONTEXT overrides DOCKER_HOST in the official CLI. Resolve it first
    # rather than redirecting an existing connection after removing user config.
    if caller.get("DOCKER_HOST") and not caller.get("DOCKER_CONTEXT"):
        host = caller["DOCKER_HOST"]
        tls = bool(caller.get("DOCKER_TLS") or caller.get("DOCKER_TLS_VERIFY"))
        verify = bool(caller.get("DOCKER_TLS_VERIFY"))
        source = None
        files = set()
        if tls:
            certificate_path = caller.get("DOCKER_CERT_PATH") or caller.get("DOCKER_CONFIG")
            if not certificate_path:
                if not caller.get("HOME"):
                    raise ValueError("Docker TLS requires an explicit certificate configuration")
                certificate_path = str(Path(caller["HOME"]) / ".docker")
            source = Path(certificate_path)
            files = {name for name in TLS_FILES if (source / name).is_file()}
        return host, tls, verify, source, files
    command = ["docker", "context", "inspect"]
    if caller.get("DOCKER_CONTEXT"):
        if caller["DOCKER_CONTEXT"].startswith("-"):
            raise ValueError("invalid Docker context name")
        command.append(caller["DOCKER_CONTEXT"])
    try:
        contexts = json.loads(subprocess.check_output(command, env=caller, text=True, stderr=subprocess.PIPE))
        if not isinstance(contexts, list) or len(contexts) != 1:
            raise ValueError("Docker context inspection must resolve exactly one context")
        context = contexts[0]
        endpoint = context["Endpoints"]["docker"]
        host = endpoint["Host"]
        if not isinstance(host, str) or type(endpoint["SkipTLSVerify"]) is not bool:
            raise ValueError("Docker context endpoint has invalid transport metadata")
        verify = not endpoint["SkipTLSVerify"]
        files = context.get("TLSMaterial", {}).get("docker", [])
        if not isinstance(files, list) or len(set(files)) != len(files) or not set(files) <= TLS_FILES:
            raise ValueError("Docker context contains unsupported TLS material")
        tls = bool(files or endpoint["SkipTLSVerify"])
        source = None
        if files:
            directory = Path(context["Storage"]["TLSPath"])
            if not directory.is_absolute():
                raise ValueError("Docker context TLS storage must be an explicit directory")
            # Official Docker CLI stores TLS files by context, then endpoint.
            source = directory / "docker"
        return host, tls, verify, source, set(files)
    except (KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        raise ValueError("Cannot resolve the Docker context; configure a supported explicit endpoint") from exc


@contextmanager
def isolated_host_environment(work: Path, caller: dict[str, str]):
    host, tls, verify, source, files = transport(caller)
    kind = endpoint_kind(host)
    if tls and kind != "tcp":
        raise ValueError("Docker TLS requires a TCP endpoint")
    if ("cert.pem" in files) != ("key.pem" in files):
        raise ValueError("Docker TLS client certificate and key must be supplied together")
    if not caller.get("PATH"):
        raise ValueError("Host image validation requires an explicit tool PATH")
    with tempfile.TemporaryDirectory(prefix="host-context-", dir=work) as temporary:
        root = Path(temporary)
        environment = {key: value for key, value in caller.items() if key in NETWORK_ENV}
        for key, directory in {
            "HOME": "home", "DOCKER_CONFIG": "docker", "XDG_CONFIG_HOME": "xdg", "CLOUDSDK_CONFIG": "gcloud",
        }.items():
            target = root / directory
            target.mkdir(mode=0o700)
            environment[key] = str(target)
        (root / "docker/config.json").write_text("{}\n")
        environment["DOCKER_HOST"] = host
        if tls:
            certificates = root / "docker-tls"
            certificates.mkdir(mode=0o700)
            for name in sorted(files):
                path = source / name
                if not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
                    raise ValueError(f"Missing or invalid Docker transport TLS file: {name}")
                target = certificates / name
                target.write_bytes(path.read_bytes())
                target.chmod(0o600)
            environment["DOCKER_CERT_PATH"] = str(certificates)
            environment["DOCKER_TLS_VERIFY" if verify else "DOCKER_TLS"] = "1"
        yield environment
