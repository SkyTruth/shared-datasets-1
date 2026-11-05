"""Verify public catalog routes and one canonical metadata sidecar without redirects."""
import json
import time
import urllib.request

BASE = "https://tiles.skytruth.org"
BUCKET = "gs://skytruth-shared-datasets-1/"


def check(url, content_type=None):
    redirect = urllib.request.HTTPRedirectHandler()
    redirect.redirect_request = lambda *args: None
    opener = urllib.request.build_opener(redirect)
    for attempt in range(12):
        try:
            with opener.open(url, timeout=30) as response:
                if response.status != 200 or response.headers.get("Location") or (
                    content_type and content_type not in response.headers.get("Content-Type", "")
                ):
                    raise ValueError(f"{url}: unexpected status or headers")
            print(f"Verified {url}", flush=True)
            return
        except (OSError, ValueError):
            if attempt == 11:
                raise
            time.sleep(10)


def main():
    for path, content_type in (("shared-datasets-catalog.csv", "text/csv"),
                               ("web/catalog.json", "application/json"), ("web/index.html", "text/html")):
        check(f"{BASE}/_catalog/{path}", content_type)
    with urllib.request.urlopen(f"{BASE}/_catalog/releases/wdpa-marine.json", timeout=30) as response:
        index = json.load(response)
    for release in [index.get("latest_release") or {}, *(index.get("releases") or [])]:
        for entry in release.get("files") or []:
            path = str(entry.get("path") or "")
            if ((str(entry.get("format") or "").lower() == "metadata" or str(entry.get("role") or "").lower() == "metadata")
                    and path.startswith(BUCKET) and path.endswith(".metadata.ndjson.gz")):
                check(f"{BASE}/artifacts/{path.removeprefix(BUCKET)}")
                return
    raise ValueError("WDPA marine release index has no canonical metadata sidecar")


if __name__ == "__main__":
    main()
