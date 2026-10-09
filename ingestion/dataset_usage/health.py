"""Read-only collection checks and nonce-bound daily probes."""
from __future__ import annotations
import datetime as dt
import json
from urllib.parse import quote

from ingestion.dataset_usage.model import iso


class Health:
    def __init__(self, classifier, client, project, raw_bucket):
        from google.auth.transport.requests import AuthorizedSession
        import google.auth
        self.classifier = classifier
        self.client = client
        self.project = project
        self.raw_bucket = raw_bucket
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.session = AuthorizedSession(credentials)
        public = [row for row in classifier.assets.values() if row["access_tier"] == "public"]
        restricted = [row for row in classifier.assets.values() if row["access_tier"] != "public"]
        if not public or not restricted:
            raise ValueError("Usage probes require public and restricted sentinel assets")
        self.public, self.restricted = public[0], restricted[0]

    def configuration_healthy(self):
        bucket = self.client.get_bucket(self.classifier.bucket, timeout=30)
        logging = bucket.get_logging()
        # The Storage SDK returns None when access logging is not configured.
        if logging is None:
            return False
        if not isinstance(logging, dict):
            raise TypeError("Bucket logging must be a mapping or None")
        if logging.get("logBucket") != self.raw_bucket or logging.get("logObjectPrefix") != "storage-usage":
            return False
        url = f"https://logging.googleapis.com/v2/projects/{self.project}/sinks/dataset-usage"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        sink = response.json()
        if sink.get("disabled") or sink.get("destination") != f"storage.googleapis.com/{self.raw_bucket}":
            return False
        # Bind the live filter to the reviewed Terraform filter, not merely the
        # sink name. Any out-of-band modification suspends coverage.
        from ingestion.dataset_usage.model import digest
        expected = digest(self.classifier.config["sink_filter"].strip())
        if digest(sink["filter"].strip()) != expected:
            return False
        # The exclusion belongs only to _Default, never to the complete raw sink.
        if any(not entry.get("disabled", False) for entry in sink.get("exclusions", [])):
            return False
        response = self.session.get(f"https://logging.googleapis.com/v2/projects/{self.project}/exclusions/dataset-usage-exported-copy", timeout=30)
        response.raise_for_status()
        exclusion = response.json()
        if exclusion.get("disabled") or digest(exclusion["filter"].strip()) != expected:
            return False
        raw = self.client.get_bucket(self.raw_bucket, timeout=30)
        if list(raw.lifecycle_rules) != [{"action": {"type": "Delete"}, "condition": {"age": self.classifier.config["raw_retention_days"]}}]:
            return False
        response = self.session.post(f"https://cloudresourcemanager.googleapis.com/v1/projects/{self.project}:getIamPolicy", json={}, timeout=30)
        response.raise_for_status()
        configs = response.json().get("auditConfigs", [])
        read_configs = [entry for config in configs if config["service"] in {"storage.googleapis.com", "allServices"} for entry in config.get("auditLogConfigs", []) if entry["logType"] == "DATA_READ"]
        if not read_configs or any(entry.get("exemptedMembers") for entry in read_configs):
            return False
        now = dt.datetime.now(dt.UTC)
        params = {
            "filter": 'metric.type="logging.googleapis.com/exports/error_count" AND resource.type="logging_sink" AND resource.labels.name="dataset-usage"',
            "interval.startTime": iso(now - dt.timedelta(days=2)), "interval.endTime": iso(now), "view": "FULL"}
        while True:
            response = self.session.get(f"https://monitoring.googleapis.com/v3/projects/{self.project}/timeSeries", params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if any(int(point["value"].get("int64Value", 0)) > 0 for series in data.get("timeSeries", []) for point in series.get("points", [])):
                return False
            if not data.get("nextPageToken"):
                return True
            params["pageToken"] = data["nextPageToken"]

    def probe(self, source, token):
        row = self.restricted if source == "gcs_audit" else self.public
        slug = row["asset_slug"]
        if source == "catalog":
            print(json.dumps({"event": "dataset_usage_probe", "asset_slug": slug, "probe": token}), flush=True)
            return slug
        path = row["canonical_path"].split(f"gs://{self.classifier.bucket}/", 1)[1]
        if source == "cdn":
            url = f"https://{self.classifier.config['cdn_host']}/artifacts/{quote(path, safe='/')}?usage_probe={token}"
            import urllib.request
            request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": token})
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read(1)
        elif source == "gcs_usage":
            import urllib.request
            request = urllib.request.Request(f"https://storage.googleapis.com/{self.classifier.bucket}/{quote(path, safe='/')}?usage_probe={token}", method="HEAD", headers={"User-Agent": token})
            with urllib.request.urlopen(request, timeout=30):
                pass
        else:
            url = f"https://storage.googleapis.com/storage/v1/b/{self.classifier.bucket}/o/{quote(path, safe='')}?alt=media"
            with self.session.get(url, headers={"Range": "bytes=0-0", "x-goog-custom-audit-usage-probe": token, "User-Agent": token}, stream=True, timeout=30) as response:
                response.raise_for_status()
                next(response.iter_content(chunk_size=1), b"")
        return slug
