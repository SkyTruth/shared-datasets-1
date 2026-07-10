#!/usr/bin/env bash
set -euo pipefail

root_name="${1:?Terraform root name is required}"
terraform_dir="${2:?Terraform directory is required}"
case "${root_name}:${terraform_dir}" in
  prod:terraform/envs/prod|preview:terraform/envs/preview) ;;
  *) echo "Unsupported Terraform state migration root: ${root_name}:${terraform_dir}" >&2; exit 1 ;;
esac

legacy_bucket="skytruth-shared-datasets-1"
state_bucket="skytruth-shared-datasets-1-terraform-state"
prefix="000-system/terraform/state/${root_name}"
source_uri="gs://${legacy_bucket}/${prefix}/default.tfstate"
destination="gs://${state_bucket}/${prefix}/default.tfstate"
work_dir="${RUNNER_TEMP:?RUNNER_TEMP is required}/terraform-state-migration-${root_name}"
umask 077
install -d -m 700 "${work_dir}"

if gcloud storage objects describe "${destination}" >/dev/null 2>&1; then
  echo "Refusing to overwrite existing destination state: ${destination}" >&2
  exit 1
fi

source_generation_before="$(gcloud storage objects describe "${source_uri}" --format='value(generation)')"
if [[ ! "${source_generation_before}" =~ ^[0-9]+$ ]]; then
  echo "Could not record the legacy source generation: ${source_uri}" >&2
  exit 1
fi

terraform -chdir="${terraform_dir}" init -input=false -reconfigure \
  -backend-config="bucket=${legacy_bucket}" \
  -backend-config="prefix=${prefix}"
terraform -chdir="${terraform_dir}" state pull > "${work_dir}/source.tfstate"
terraform -chdir="${terraform_dir}" state list | LC_ALL=C sort > "${work_dir}/source.addresses"

source_generation_after="$(gcloud storage objects describe "${source_uri}" --format='value(generation)')"
if [[ "${source_generation_after}" != "${source_generation_before}" ]]; then
  echo "Legacy source state changed while it was being captured: ${source_generation_before} -> ${source_generation_after}" >&2
  exit 1
fi

terraform -chdir="${terraform_dir}" init -input=false -migrate-state -force-copy
terraform -chdir="${terraform_dir}" state pull > "${work_dir}/destination.tfstate"
terraform -chdir="${terraform_dir}" state list | LC_ALL=C sort > "${work_dir}/destination.addresses"

python - "${work_dir}/source.tfstate" "${work_dir}/destination.tfstate" <<'PY'
import json
import sys

source = json.load(open(sys.argv[1]))
destination = json.load(open(sys.argv[2]))
for key in ("lineage", "serial", "outputs"):
    if source.get(key) != destination.get(key):
        raise SystemExit(f"migrated state mismatch for {key}")
PY

if ! cmp -s "${work_dir}/source.addresses" "${work_dir}/destination.addresses"; then
  echo "Migrated state resource addresses do not match:" >&2
  diff -u "${work_dir}/source.addresses" "${work_dir}/destination.addresses" >&2 || true
  exit 1
fi

destination_generation="$(gcloud storage objects describe "${destination}" --format='value(generation)')"
python - \
  "${work_dir}/source.tfstate" \
  "${work_dir}/source.addresses" \
  "${source_generation_before}" \
  "${destination_generation}" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1]))
addresses = [line for line in open(sys.argv[2]).read().splitlines() if line]
print(json.dumps({
    "source_generation": sys.argv[3],
    "destination_generation": sys.argv[4],
    "lineage": state.get("lineage"),
    "serial": state.get("serial"),
    "resource_addresses": addresses,
}, indent=2, sort_keys=True))
PY
