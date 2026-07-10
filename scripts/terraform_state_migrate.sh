#!/usr/bin/env bash
set -euo pipefail

root_name="${1:?Terraform root name is required}"
terraform_dir="${2:?Terraform directory is required}"
legacy_bucket="skytruth-shared-datasets-1"
state_bucket="skytruth-shared-datasets-1-terraform-state"
prefix="000-system/terraform/state/${root_name}"
destination="gs://${state_bucket}/${prefix}/default.tfstate"
work_dir="${RUNNER_TEMP:?RUNNER_TEMP is required}/terraform-state-migration-${root_name}"
mkdir -p "${work_dir}"

if gcloud storage objects describe "${destination}" >/dev/null 2>&1; then
  echo "Refusing to overwrite existing destination state: ${destination}" >&2
  exit 1
fi

terraform -chdir="${terraform_dir}" init -input=false -reconfigure \
  -backend-config="bucket=${legacy_bucket}" \
  -backend-config="prefix=${prefix}"
terraform -chdir="${terraform_dir}" state pull > "${work_dir}/source.tfstate"

terraform -chdir="${terraform_dir}" init -input=false -migrate-state -force-copy
terraform -chdir="${terraform_dir}" state pull > "${work_dir}/destination.tfstate"

python - "${work_dir}/source.tfstate" "${work_dir}/destination.tfstate" <<'PY'
import json
import sys

source = json.load(open(sys.argv[1]))
destination = json.load(open(sys.argv[2]))
for key in ("lineage", "serial", "outputs"):
    if source.get(key) != destination.get(key):
        raise SystemExit(f"migrated state mismatch for {key}")

def addresses(state):
    return sorted(
        (item.get("module", ""), item.get("mode", "managed"), item.get("type", ""), item.get("name", ""))
        for item in state.get("resources", [])
    )

if addresses(source) != addresses(destination):
    raise SystemExit("migrated state resource addresses do not match")
PY

gcloud storage objects describe "${destination}" --format='value(generation)'
