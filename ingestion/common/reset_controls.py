"""Project-local reset preconditions; existing administrators remain trusted.

The protected deployment identity reads job status. The existing publisher
writes the reviewed reset. No IAM policies or organization access are needed.
"""

from __future__ import annotations

from ingestion.common import publication as p

PROJECT = "shared-datasets-1"
BUCKET = "skytruth-shared-datasets-1"
REGION = "us-central1"
PUBLISHER_ACCOUNT = f"shared-datasets-publisher@{PROJECT}.iam.gserviceaccount.com"
DEPLOYER_ACCOUNT = f"shared-datasets-terraform@{PROJECT}.iam.gserviceaccount.com"
ASSET_JOBS = {"wdpa-marine": "wdpa-monthly", "wdpa-terrestrial": "wdpa-monthly",
              "ims-sea-ice-extent": "sea-ice-daily"}


class GoogleControlReader:
    def __init__(self, session):
        self.session = session

    def read(self, url, *, params=None):
        response = self.session.get(url, params=params, timeout=60, allow_redirects=False)
        p.require(response.status_code == 200, f"reset job check failed ({response.status_code}): {url}")
        value = p.strict_json(response.content)
        p.require(isinstance(value, dict), "reset job response must be an object")
        return value

    def check_quiescent(self, asset_slug):
        job = ASSET_JOBS[asset_slug]
        parent = f"projects/{PROJECT}/locations/{REGION}/jobs/{job}"
        scheduler = self.read(f"https://cloudscheduler.googleapis.com/v1/{parent}")
        p.require(scheduler.get("state") == "PAUSED", f"pause the {job} schedule before installing its reset")
        query, seen = {}, set()
        while True:
            page = self.read(f"https://run.googleapis.com/v2/{parent}/executions", params=query)
            executions = page.get("executions", [])
            p.require(isinstance(executions, list) and all(isinstance(row, dict) for row in executions), "invalid execution list")
            for execution in executions:
                p.require(bool(execution.get("completionTime")) and not execution.get("reconciling", False),
                          f"wait for running or pending {job} executions before installing its reset")
            token = page.get("nextPageToken")
            if token is None or token == "":
                return
            p.require(isinstance(token, str) and token not in seen, "execution pagination did not advance")
            seen.add(token)
            query = {"pageToken": token}
