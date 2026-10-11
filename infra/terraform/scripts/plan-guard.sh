#!/usr/bin/env bash
# Plan the current configuration and refuse an unsafe destroy set.
#
# Arguments are forwarded to `terraform plan` (for example -var=jobs_image_uri=...).
# On success the saved plan is ./tfplan, or PLAN_OUT when that is set. Apply that
# file. Do not run a second apply with -var: it can differ from the plan that
# passed the gate.
#
# terraform plan -detailed-exitcode returns 0 (no changes), 2 (changes), or 1
# (error). Exit 2 is success. Anything else aborts, and so does `terraform show`
# or the destroy policy. The plan file is removed when the gate fails so a later
# step cannot apply it.
set -euo pipefail

plan_path="${PLAN_OUT:-tfplan}"
status=0
terraform plan -input=false -detailed-exitcode -out="${plan_path}" "$@" || status=$?

if [[ "${status}" -ne 0 && "${status}" -ne 2 ]]; then
  rm -f "${plan_path}"
  echo "::error::terraform plan failed (exit ${status}); aborting deploy." >&2
  exit 1
fi

plan_json="$(mktemp)"
trap 'rm -f "${plan_json}"' EXIT
if ! terraform show -json "${plan_path}" > "${plan_json}"; then
  rm -f "${plan_path}"
  echo "::error::terraform show -json failed; aborting deploy." >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! bash "${script_dir}/check-plan-destroys.sh" "${plan_json}"; then
  rm -f "${plan_path}"
  exit 1
fi
