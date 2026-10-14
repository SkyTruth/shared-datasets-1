"""Bucket-local collection checks. No audit-policy or Log Router dependency."""
from __future__ import annotations
from urllib.parse import quote


class Health:
    def __init__(self, classifier, client, project, raw_bucket):
        self.classifier, self.client = classifier, client
        self.project, self.raw_bucket = project, raw_bucket
        public = [row for row in classifier.assets.values() if row["access_tier"] == "public"]
        if not public:
            raise ValueError("Bucket usage probe requires a public sentinel")
        self.public = public[0]

    def configuration_healthy(self):
        if not self.classifier.config.get("collection_enabled", False):
            return {"gcs_usage": False, "app_usage": False}
        raw = self.client.get_bucket(self.raw_bucket, timeout=30)
        protected = (raw.iam_configuration.uniform_bucket_level_access_enabled and
                     raw.iam_configuration.public_access_prevention == "enforced")
        retained = list(raw.lifecycle_rules) == [{"action": {"type": "Delete"},
                    "condition": {"age": self.classifier.config["raw_retention_days"]}}]
        if not protected or not retained:
            return {"gcs_usage": False, "app_usage": False}
        logging = self.client.get_bucket(self.classifier.bucket, timeout=30).get_logging()
        if logging is not None and not isinstance(logging, dict):
            raise TypeError("Bucket logging must be a mapping or None")
        native = bool(logging and logging.get("logBucket") == self.raw_bucket and
                      logging.get("logObjectPrefix") == "storage-usage")
        bindings = list(raw.get_iam_policy(requested_policy_version=3, timeout=30).bindings)
        configured = [(name, entry) for name, entry in self.classifier.producers.items() if entry["status"] == "configured"]
        receipts = bool(configured) and all(any(
            binding["role"] == "roles/storage.objectCreator" and
            "serviceAccount:" + entry["service_account"] in binding["members"] and
            binding.get("condition", {}).get("expression") ==
                f"resource.name.startsWith('projects/_/buckets/{self.raw_bucket}/objects/app-usage/{producer}/')"
            for binding in bindings) for producer, entry in configured)
        return {"gcs_usage": native, "app_usage": receipts}

    def probe(self, source, token):
        if source != "gcs_usage":
            raise ValueError("Only native bucket logs have a synthetic traffic probe")
        path = self.public["canonical_path"].split(f"gs://{self.classifier.bucket}/", 1)[1]
        import urllib.request
        request = urllib.request.Request(
            f"https://storage.googleapis.com/{self.classifier.bucket}/{quote(path, safe='/')}?usage_probe={token}",
            method="HEAD", headers={"User-Agent": token})
        with urllib.request.urlopen(request, timeout=30):
            pass
        return self.public["asset_slug"]
