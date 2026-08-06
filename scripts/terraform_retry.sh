#!/usr/bin/env bash
# Run a Terraform command, retrying only when it fails to acquire the state lock.
#
# `-lock-timeout` waits for a lock that is *held*, but the GCS backend has a
# failure mode it does not cover: it creates the lock with ifGenerationMatch=0,
# and on 412 it reads the existing lock to report the holder. When the holder
# releases in that window the read fails with "object doesn't exist", and the
# backend surfaces the compound error as fatal. Two runs starting together hit
# this reliably, which is how separate concurrency lanes broke production on
# 2026-08-05.
#
# Anything that is not a lock-acquisition failure exits immediately with the
# original status: this must not paper over real Terraform errors.
set -uo pipefail

TERRAFORM_RETRY_ATTEMPTS="${TERRAFORM_RETRY_ATTEMPTS:-5}"
TERRAFORM_RETRY_BASE_DELAY="${TERRAFORM_RETRY_BASE_DELAY:-15}"
TERRAFORM_BIN="${TERRAFORM_BIN:-terraform}"

if [[ $# -eq 0 ]]; then
  echo "usage: terraform_retry.sh <terraform args...>" >&2
  exit 2
fi

lock_failure() {
  grep -qiE "Error acquiring the state lock|Error releasing the state lock" "$1"
}

attempt=1
while true; do
  output_file="$(mktemp)"
  set +e
  "${TERRAFORM_BIN}" "$@" 2>&1 | tee "${output_file}"
  status="${PIPESTATUS[0]}"
  set -e

  if [[ "${status}" -eq 0 ]]; then
    rm -f "${output_file}"
    exit 0
  fi

  if ! lock_failure "${output_file}"; then
    rm -f "${output_file}"
    exit "${status}"
  fi

  if [[ "${attempt}" -ge "${TERRAFORM_RETRY_ATTEMPTS}" ]]; then
    echo "terraform_retry: state lock still contended after ${attempt} attempt(s); giving up" >&2
    rm -f "${output_file}"
    exit "${status}"
  fi

  delay=$(( TERRAFORM_RETRY_BASE_DELAY * attempt ))
  echo "terraform_retry: state lock contended (attempt ${attempt}/${TERRAFORM_RETRY_ATTEMPTS}); retrying in ${delay}s" >&2
  rm -f "${output_file}"
  sleep "${delay}"
  attempt=$(( attempt + 1 ))
done
