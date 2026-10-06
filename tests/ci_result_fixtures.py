"""Complete small image identities for CI evidence boundary tests."""

import hashlib


def production_images(tested_sha):
    images = {}
    for target in ("eamlis-monthly", "sea-ice-daily", "catalog-viewer"):
        images[target] = {
            "config_digest": "sha256:" + hashlib.sha256((target + "config").encode()).hexdigest(),
            "source_tag": f"shared-datasets-preflight/{target}:{tested_sha}",
            "archive": f"{target}.docker.tar",
            "archive_sha256": hashlib.sha256((target + "archive").encode()).hexdigest(),
            "archive_size": 10240,
            "platform": "linux/amd64",
        }
    return {"schema_version": 1, "tested_sha": tested_sha, "images": images}
