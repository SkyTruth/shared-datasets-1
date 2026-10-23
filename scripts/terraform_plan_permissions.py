"""Derive read-only permission probes from a trusted saved Terraform plan.

Resource allowlists still authorize the mutation. This module proves the exact
operations have live authority and rejects unsupported/unknown plan contracts.
For an object created by this same plan, only permission on its known parent can
be tested before creation; subsequent applies use the concrete resource.
"""
from __future__ import annotations

from collections import defaultdict
import re
from urllib.parse import quote, urlencode

PROJECT = "shared-datasets-1"
REGION = "us-central1"
PROJECT_URL = f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:testIamPermissions"
NAME = re.compile(r"[A-Za-z0-9_.@-]+")
# Compute's resource permission probes themselves require collection-list
# authority. The regional names are independent IAM permissions, not aliases
# for their global counterparts. Sources and API discovery paths are retained
# in tests/fixtures/ci_failures/compute_probe_call_contract.json.
COMPUTE_PROBE_PERMISSIONS = {
    ("global", "urlMaps"): "compute.urlMaps.list",
    ("global", "backendBuckets"): "compute.backendBuckets.list",
    ("global", "backendServices"): "compute.backendServices.list",
    ("regions", "backendBuckets"): "compute.regionBackendBuckets.list",
    ("regions", "backendServices"): "compute.regionBackendServices.list",
}


def require(condition, message):
    if not condition:
        raise ValueError("PLAN_PERMISSION_CONTRACT: " + message)


def probe_call_checks(required):
    """Prove Compute probe-call prerequisites before invoking resource checks.

    These project-level reads authorize calling testIamPermissions only. They
    never replace the permission check on the actual mutation resource.
    """
    permissions = set()
    for url, _ in required:
        if not url.startswith("https://compute.googleapis.com/"):
            continue
        match = re.fullmatch(
            r"https://compute\.googleapis\.com/compute/v1/projects/" + PROJECT
            + r"/(global|regions/" + REGION + r")/(urlMaps|backendBuckets|backendServices)/([A-Za-z0-9_.-]+)/testIamPermissions",
            url,
        )
        require(match is not None, "unsupported Compute permission-probe endpoint")
        scope = "global" if match[1] == "global" else "regions"
        permission = COMPUTE_PROBE_PERMISSIONS.get((scope, match[2]))
        require(permission is not None, "unsupported Compute permission-probe collection")
        permissions.add(permission)
    return [(PROJECT_URL, tuple(sorted(permissions)))] if permissions else []


def text(value, field):
    require(isinstance(value, str) and value and NAME.fullmatch(value), f"missing or invalid {field}")
    return value


def bucket_name(value):
    require(isinstance(value, str), "bucket identity unavailable")
    if value.startswith("b/"):
        value = value[2:]
    require(re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,220}[a-z0-9]", value), "invalid bucket identity")
    return value


def storage_url(bucket, permissions, folder=None):
    resource = f"b/{quote(bucket_name(bucket), safe='')}"
    if folder is not None:
        require(isinstance(folder, str) and folder and not folder.startswith("/") and ".." not in folder.split("/"), "managed-folder identity unavailable")
        resource += "/managedFolders/" + quote(folder, safe="")
    return "https://storage.googleapis.com/storage/v1/" + resource + "/iam/testPermissions?" + urlencode([("permissions", p) for p in sorted(permissions)])


def references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "references":
                yield from item
            else:
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def configured_references(plan, address):
    normalized = re.sub(r"\[[^]]+\]", "", address)
    def walk(module, prefix="", inherited=()):
        for resource in module.get("resources", []):
            local = resource["address"]
            full = local if local.startswith("module.") else prefix + local
            if re.sub(r"\[[^]]+\]", "", full) == normalized:
                return set(inherited) | set(references(resource.get("expressions", {})))
        for name, call in module.get("module_calls", {}).items():
            found = walk(call.get("module", {}), prefix + "module." + name + ".", (*inherited, *references(call.get("expressions", {}))))
            if found is not None:
                return found
        return None
    return walk(plan.get("configuration", {}).get("root_module", {})) or set()


def plan_checks(plan, *, project_number, target=None):
    require(isinstance(plan, dict) and plan.get("format_version") in {"1.1", "1.2"}, "saved Terraform plan format is missing or unsupported")
    require(not plan.get("errored") and not plan.get("deferred_changes"), "errored or deferred saved plan cannot establish complete authority")
    require(re.fullmatch(r"[1-9][0-9]*", str(project_number)), "project number unavailable")
    rows = plan.get("resource_changes", [])
    require(isinstance(rows, list), "resource change enumeration unavailable")
    probes = defaultdict(set)
    created = defaultdict(set)
    created_addresses = defaultdict(set)
    for row in rows:
        if row.get("change", {}).get("actions") == ["create"]:
            kind = row.get("type")
            value = row["change"].get("after") or {}
            created_addresses[kind].add(row["address"])
            if kind == "google_storage_bucket":
                created[kind].add(bucket_name(value.get("name")))
            elif kind == "google_service_account":
                created[kind].add(text(value.get("account_id"), "account_id") + f"@{PROJECT}.iam.gserviceaccount.com")
            elif kind == "google_cloud_run_v2_service":
                created[kind].add(text(value.get("name"), "service name"))
            elif kind == "google_cloud_run_v2_job":
                created[kind].add(text(value.get("name"), "job name"))
            elif kind == "google_storage_managed_folder":
                created[kind].add((bucket_name(value.get("bucket")), value.get("name")))

    def add(url, permissions):
        probes[url].update(permissions)

    def project(value):
        require(str(value.get("project")) in {PROJECT, str(project_number)}, "mutation project is missing or outside production")

    def region(value):
        require(value.get("location", value.get("region")) == REGION, "mutation region is missing or outside production")

    def future_unknown(row, kind):
        refs = configured_references(plan, row["address"])
        for address in created_addresses[kind]:
            base = re.sub(r"\[[^]]+\]", "", address)
            module = base.rsplit(".", 2)[0] if base.startswith("module.") else None
            if any(ref.startswith(base + ".") or (module and ref.startswith(module + ".")) for ref in refs):
                return True
        return False

    def sa(raw, permissions, row):
        if raw is None:
            require(future_unknown(row, "google_service_account"), "unknown service account lacks an explicit creation dependency")
            add(PROJECT_URL, permissions)
            return
        require(isinstance(raw, str), "invalid service account identity")
        if raw.startswith("projects/"):
            match = re.fullmatch(r"projects/([^/]+)/serviceAccounts/([^/]+)", raw)
            require(match and match[1] in {PROJECT, str(project_number)}, "foreign service account")
            raw = match[2]
        require(re.fullmatch(r"[a-z0-9-]+@" + re.escape(PROJECT) + r"\.iam\.gserviceaccount\.com", raw), "foreign or unknown service account")
        if raw in created["google_service_account"]:
            add(PROJECT_URL, permissions)
        else:
            add(f"https://iam.googleapis.com/v1/projects/{PROJECT}/serviceAccounts/{raw}:testIamPermissions", permissions)

    def run_resource(value, collection, permissions, row):
        project(value)
        region(value)
        name = value.get("name")
        kind = "google_cloud_run_v2_" + ("service" if collection == "services" else "job")
        if name is None:
            require(future_unknown(row, kind), "unknown Cloud Run resource lacks creation dependency")
            add(PROJECT_URL, permissions)
        elif name in created[kind]:
            add(PROJECT_URL, permissions)
        else:
            text(name, "Cloud Run resource name")
            add(f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/{collection}/{name}:testIamPermissions", permissions)

    def runtime_identity(value, row, kind):
        block = value.get("template", [])
        require(isinstance(block, list) and len(block) == 1, "runtime template unavailable")
        block = block[0]
        if kind == "google_cloud_run_v2_job":
            blocks = block.get("template", [])
            require(isinstance(blocks, list) and len(blocks) == 1, "job runtime template unavailable")
            block = blocks[0]
        sa(block.get("service_account"), ("iam.serviceAccounts.actAs",), row)

    backend_fields = {"default_service", "service", "backend_service"}

    def backend_unknown(value):
        if isinstance(value, dict):
            for field, child in value.items():
                require(not (child is True and field in backend_fields | {"path_matcher", "path_rule", "route_rules", "route_action", "weighted_backend_services"}), "URL-map backend dependencies remain unknown")
                backend_unknown(child)
        elif isinstance(value, list):
            for child in value:
                backend_unknown(child)

    def backend_references(value, field=None):
        if isinstance(value, dict):
            for name, child in value.items():
                backend_references(child, name)
        elif isinstance(value, list):
            for child in value:
                backend_references(child, field)
        elif value and field in backend_fields:
            require(isinstance(value, str), "invalid URL-map backend identity")
            match = re.fullmatch(r"(?:https://(?:www\.googleapis\.com|compute\.googleapis\.com)/compute/v1/)?projects/([^/]+)/(global|regions/[^/]+)/(backendBuckets|backendServices)/([^/]+)", value)
            require(match and match[1] == PROJECT and match[2] in {"global", "regions/" + REGION}, "foreign or unresolved URL-map backend")
            collection, name = match[3], text(match[4], "backend name")
            add(f"https://compute.googleapis.com/compute/v1/projects/{PROJECT}/{match[2]}/{collection}/{name}/testIamPermissions", ("compute." + collection + ".use",))

    found_map = False
    for row in rows:
        change = row.get("change", {})
        actions = change.get("actions")
        require(isinstance(actions, list), "resource actions unavailable")
        if row.get("mode", "managed") == "data" or actions in ([], ["read"]):
            continue
        kind = row.get("type")
        require(isinstance(kind, str), "resource type unavailable")
        value = change.get("after") or change.get("before") or {}
        if kind == "google_compute_url_map" and target == "pmtiles-cdn":
            project(value)
            name = text(value.get("name"), "URL map name")
            require(name == "shared-datasets-pmtiles-cdn", "unexpected CDN cache target")
            add(f"https://compute.googleapis.com/compute/v1/projects/{PROJECT}/global/urlMaps/{name}/testIamPermissions", ("compute.urlMaps.invalidateCache",))
            found_map = True
        if actions == ["no-op"]:
            continue
        require(actions in (["create"], ["update"], ["delete"], ["delete", "create"], ["create", "delete"]), "unsupported resource action sequence")
        for action in actions:
            current = (change.get("before") if action == "delete" else change.get("after")) or {}
            # Some deletion providers retain all identity fields only in before.
            require(isinstance(current, dict) and current, "mutation values unavailable")
            if kind == "google_project_iam_custom_role":
                project(current)
                text(current.get("role_id"), "role_id")
                add(PROJECT_URL, ("iam.roles." + action, "iam.roles.get"))
            elif kind == "google_project_iam_audit_config":
                project(current)
                add(PROJECT_URL, ("resourcemanager.projects.getIamPolicy", "resourcemanager.projects.setIamPolicy"))
            elif kind == "google_logging_project_sink":
                project(current)
                add(PROJECT_URL, ("logging.sinks.get", "logging.sinks." + action))
            elif kind == "google_logging_project_exclusion":
                project(current)
                text(current.get("name"), "logging exclusion name")
                add(PROJECT_URL, ("logging.exclusions.get", "logging.exclusions." + action))
            elif kind == "google_project_iam_member":
                project(current)
                add(PROJECT_URL, ("resourcemanager.projects.getIamPolicy", "resourcemanager.projects.setIamPolicy"))
            elif kind == "google_service_account":
                project(current)
                account = text(current.get("account_id"), "account_id") + f"@{PROJECT}.iam.gserviceaccount.com"
                if action == "create":
                    add(PROJECT_URL, ("iam.serviceAccounts.create", "iam.serviceAccounts.get"))
                else:
                    sa(account, ("iam.serviceAccounts.get", "iam.serviceAccounts." + action), row)
            elif kind == "google_service_account_iam_member":
                sa(current.get("service_account_id"), ("iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy"), row)
            elif kind == "google_storage_bucket":
                project(current)
                bucket = bucket_name(current.get("name"))
                if action == "create":
                    add(PROJECT_URL, ("storage.buckets.create", "storage.buckets.get"))
                else:
                    require(not (action == "delete" and current.get("force_destroy")), "force-destroy requires separate explicit object authorization")
                    add(storage_url(bucket, ("storage.buckets.get", "storage.buckets." + action)), ("storage.buckets.get", "storage.buckets." + action))
            elif kind == "google_storage_bucket_iam_member":
                bucket = bucket_name(current.get("bucket"))
                needed = ("storage.buckets.getIamPolicy", "storage.buckets.setIamPolicy")
                add(PROJECT_URL if bucket in created["google_storage_bucket"] else storage_url(bucket, needed), needed)
            elif kind == "google_storage_managed_folder":
                bucket, folder = bucket_name(current.get("bucket")), current.get("name")
                require(action in {"create", "delete"}, "managed-folder update contract unsupported")
                needed = ("storage.managedFolders." + action, "storage.managedFolders.get")
                add(storage_url(bucket, needed, None if action == "create" else folder), needed)
            elif kind == "google_storage_managed_folder_iam_member":
                bucket, folder = bucket_name(current.get("bucket")), current.get("managed_folder")
                needed = ("storage.managedFolders.getIamPolicy", "storage.managedFolders.setIamPolicy")
                if folder is None:
                    require(future_unknown(row, "google_storage_managed_folder"), "unknown managed folder lacks creation dependency")
                future = folder is None or (bucket, folder) in created["google_storage_managed_folder"]
                add(storage_url(bucket, needed, None if future else folder), needed)
            elif kind == "google_secret_manager_secret_iam_member":
                project(current)
                secret = current.get("secret_id")
                if isinstance(secret, str) and secret.startswith("projects/"):
                    match = re.fullmatch(r"projects/([^/]+)/secrets/([^/]+)", secret)
                    require(match and match[1] in {PROJECT, str(project_number)}, "foreign secret")
                    secret = match[2]
                secret = text(secret, "secret_id")
                add(f"https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets/{secret}:testIamPermissions", ("secretmanager.secrets.getIamPolicy", "secretmanager.secrets.setIamPolicy"))
            elif kind == "google_artifact_registry_repository_iam_member":
                project(current)
                region(current)
                name = text(current.get("repository"), "repository")
                add(f"https://artifactregistry.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/repositories/{name}:testIamPermissions", ("artifactregistry.repositories.getIamPolicy", "artifactregistry.repositories.setIamPolicy"))
            elif kind in {"google_cloud_run_v2_job", "google_cloud_run_v2_service"}:
                plural = "jobs" if kind.endswith("job") else "services"
                needed = ("run." + plural + "." + action, "run." + plural + ".get")
                if action == "create":
                    project(current)
                    region(current)
                    add(PROJECT_URL, needed)
                else:
                    run_resource(current, plural, needed, row)
                add(PROJECT_URL, ("run.operations.get",))
                if action != "delete":
                    runtime_identity(current, row, kind)
            elif kind in {"google_cloud_run_v2_job_iam_member", "google_cloud_run_v2_service_iam_member"}:
                plural = "jobs" if kind == "google_cloud_run_v2_job_iam_member" else "services"
                run_resource(current, plural, ("run." + plural + ".getIamPolicy", "run." + plural + ".setIamPolicy"), row)
            elif kind == "google_cloud_scheduler_job":
                project(current)
                region(current)
                text(current.get("name"), "scheduler job name")
                add(PROJECT_URL, ("cloudscheduler.jobs." + action, "cloudscheduler.jobs.get"))
                if action == "create" or (action == "update" and (change.get("before") or {}).get("paused") != current.get("paused")):
                    require(not change.get("after_unknown", {}).get("paused"), "scheduler state transition is unknown")
                    add(PROJECT_URL, ("cloudscheduler.jobs." + ("pause" if current.get("paused") else "enable"),))
                if action != "delete":
                    for http in current.get("http_target", []):
                        for method in ("oauth_token", "oidc_token"):
                            for token in http.get(method, []):
                                sa(token.get("service_account_email"), ("iam.serviceAccounts.actAs",), row)
            elif kind == "google_iap_web_cloud_run_service_iam_member":
                project(current)
                region(current)
                name = current.get("cloud_run_service_name")
                if isinstance(name, str) and name.startswith("projects/"):
                    match = re.fullmatch(r"projects/([^/]+)/iap_web/cloud_run-([^/]+)/services/([^/]+)", name)
                    require(match and match[1] in {PROJECT, str(project_number)} and match[2] == REGION, "foreign IAP service")
                    name = match[3]
                needed = ("iap.webServices.getIamPolicy", "iap.webServices.setIamPolicy")
                if name is None or name in created["google_cloud_run_v2_service"]:
                    require(name is not None or future_unknown(row, "google_cloud_run_v2_service"), "unknown IAP service lacks creation dependency")
                    add(PROJECT_URL, needed)
                else:
                    name = text(name, "IAP Cloud Run service")
                    add(f"https://iap.googleapis.com/v1/projects/{project_number}/iap_web/cloud_run-{REGION}/services/{name}:testIamPermissions", needed)
            elif kind == "google_compute_url_map":
                project(current)
                name = text(current.get("name"), "URL map name")
                needed = ("compute.urlMaps." + action, "compute.urlMaps.get")
                add(PROJECT_URL if action == "create" else f"https://compute.googleapis.com/compute/v1/projects/{PROJECT}/global/urlMaps/{name}/testIamPermissions", needed)
                add(PROJECT_URL, ("compute.globalOperations.get",))
                if action != "delete":
                    backend_unknown(change.get("after_unknown", {}))
                    backend_references(current)
            elif kind == "google_monitoring_alert_policy":
                project(current)
                add(PROJECT_URL, ("monitoring.alertPolicies." + action, "monitoring.alertPolicies.get"))
                if current.get("notification_channels") and action != "delete":
                    add(PROJECT_URL, ("monitoring.notificationChannels.get",))
                if any(condition.get("condition_matched_log") for condition in current.get("conditions", [])):
                    for operation in ({"create", "delete"} if action == "update" else {action}):
                        add(PROJECT_URL, ("logging.notificationRules." + operation,))
            else:
                raise ValueError("PLAN_PERMISSION_CONTRACT: unsupported mutation class " + kind)
    require(target != "pmtiles-cdn" or found_map, "CDN post-apply cache target missing from saved plan")
    return [(url, tuple(sorted(permissions))) for url, permissions in sorted(probes.items())]
