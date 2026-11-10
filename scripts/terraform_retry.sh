#!/usr/bin/env bash
# Retry lock acquisition and proven transient reads during plan only.
# GCS can reject lock creation with 412, then find that the holder already
# released it; Terraform's -lock-timeout does not cover that acquisition race.
# Apply transport failures can follow partial writes: only pre-mutation lock
# acquisition may retry apply. Authorization, invalid plans and lock-release
# failures always retain their original exit status.
set -uo pipefail

TERRAFORM_RETRY_ATTEMPTS="${TERRAFORM_RETRY_ATTEMPTS:-5}"
TERRAFORM_RETRY_BASE_DELAY="${TERRAFORM_RETRY_BASE_DELAY:-15}"
TERRAFORM_BIN="${TERRAFORM_BIN:-terraform}"

if [[ $# -eq 0 ]]; then
  echo "usage: terraform_retry.sh <terraform args...>" >&2
  exit 2
fi

retryable_failure() {
  if grep -Eqi 'Error releasing the state lock|Permission denied|Forbidden|Unauthorized|Unauthenticated|PERMISSION_DENIED|invalid_grant|status code (400|401|403)|HTTP (400|401|403)|Error (400|401|403):|Invalid |Saved plan' "$1"; then
    return 1
  fi
  if grep -qi "Error acquiring the state lock" "$1"; then
    reason='state lock contended'
    return 0
  fi
  # Plans only read cloud resources. A provider failure during apply can follow
  # partial writes, so never replay apply for a transport/server failure.
  if [[ "${operation}" == plan ]] && grep -Eqi 'TLS handshake timeout|The handshake operation timed out|status code (500|502|503|504)|googleapi: Error (500|502|503|504):' "$1"; then
    reason='transient plan read failure'
    return 0
  fi
  return 1
}

operation=''
for argument in "$@"; do
  if [[ "$argument" != -* ]]; then operation="$argument"; break; fi
done
output_file="$(mktemp)"
trap 'rm -f "$output_file"' EXIT
attempt=1
while true; do
  set +e
  "${TERRAFORM_BIN}" "$@" 2>&1 | tee "${output_file}"
  status="${PIPESTATUS[0]}"
  set -e

  if [[ "${status}" -eq 0 ]]; then
    exit 0
  fi

  if ! retryable_failure "${output_file}"; then
    exit "${status}"
  fi

  if [[ "${attempt}" -ge "${TERRAFORM_RETRY_ATTEMPTS}" ]]; then
    echo "terraform_retry: ${reason} after ${attempt} attempt(s); giving up" >&2
    exit "${status}"
  fi

  delay=$(( TERRAFORM_RETRY_BASE_DELAY * attempt ))
  echo "terraform_retry: ${reason} (attempt ${attempt}/${TERRAFORM_RETRY_ATTEMPTS}); retrying in ${delay}s" >&2
  sleep "${delay}"
  attempt=$(( attempt + 1 ))
done
