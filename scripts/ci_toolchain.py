"""Dependency-free pins used before the validation environment exists."""

TOOLCHAIN = {
    "python": "3.12.12",
    "uv": "0.11.8",
    "node22": "22.12.0",
    "node24": "24.13.1",
    "terraform": "1.8.5",
    "gitleaks": "8.30.1",
    "actionlint": "1.7.12",
    "shellcheck": "0.11.0",
}

# Official v0.11.0 release asset digests (Linux gzip archives).
SHELLCHECK_SHA256 = {
    "amd64": "b7af85e41cc99489dcc21d66c6d5f3685138f06d34651e6d34b42ec6d54fe6f6",
    "arm64": "68a8133197a50beb8803f8d42f9908d1af1c5540d4bb05fdfca8c1fa47decefc",
}
