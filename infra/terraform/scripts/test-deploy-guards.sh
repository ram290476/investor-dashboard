#!/usr/bin/env bash
# Fixture tests for the production deploy guards.
# The large state list is the Deploy 38137774464 failure: `grep -q` under
# pipefail returns non-zero on a list that does contain the ECR repository,
# and the old if/elif then chooses bootstrap. decide-bootstrap.sh must skip.
set -euo pipefail

cd "$(dirname "$0")"

expect_decision() {
  local file="$1"
  local want="$2"
  local got status
  set +e
  got="$(bash ./decide-bootstrap.sh "${file}")"
  status=$?
  set -e
  if [[ "${status}" -ne 0 || "${got}" != "${want}" ]]; then
    echo "decide ${file}: want ${want} exit 0, got '${got}' exit ${status}" >&2
    exit 1
  fi
}

expect_decide_fail() {
  local status
  set +e
  bash ./decide-bootstrap.sh "$@" >/dev/null 2>&1
  status=$?
  set -e
  if [[ "${status}" -eq 0 ]]; then
    echo "decide should have failed: $*" >&2
    exit 1
  fi
}

expect_gate() {
  local want="$1"
  local file="$2"
  local status err
  set +e
  err="$(bash ./check-plan-destroys.sh "${file}" 2>&1 >/dev/null)"
  status=$?
  set -e
  if [[ "${status}" -ne "${want}" ]]; then
    echo "gate ${file}: want exit ${want}, got ${status} (ALLOW_DESTROY=${ALLOW_DESTROY:-} MAX_DESTROY=${MAX_DESTROY:-})" >&2
    printf '%s\n' "${err}" >&2
    exit 1
  fi
}

roles_plan() {
  local count="$1"
  local dest="$2"
  jq -n --argjson n "${count}" '{
    format_version: "1.2",
    resource_changes: [
      range($n) | {
        address: ("aws_iam_role.extra[" + tostring + "]"),
        mode: "managed",
        type: "aws_iam_role",
        change: {actions: ["delete"]}
      }
    ]
  }' > "${dest}"
}

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

expect_decision testdata/state-ecr-and-jobs.txt skip
expect_decision testdata/state-ecr-only.txt skip
expect_decision testdata/state-jobs-only.txt refuse
expect_decision testdata/state-empty.txt bootstrap
expect_decision testdata/state-lookalikes.txt bootstrap

printf '\n\n' > "${tmp}/blank-lines.txt"
expect_decision "${tmp}/blank-lines.txt" bootstrap

# Last address has no trailing newline. The job Lambda must still count.
printf '%s' 'module.jobs.aws_lambda_function.job["prices"]' > "${tmp}/no-newline.txt"
expect_decision "${tmp}/no-newline.txt" refuse

# Trailing space is not the ECR address. The Lambda still refuses bootstrap.
printf '%s\n' \
  'module.data_lake.aws_ecr_repository.jobs ' \
  'module.jobs.aws_lambda_function.job["prices"]' > "${tmp}/padded-ecr.txt"
expect_decision "${tmp}/padded-ecr.txt" refuse

expect_decide_fail
expect_decide_fail "${tmp}/missing-state.txt"
mkdir -p "${tmp}/state-dir"
expect_decide_fail "${tmp}/state-dir"

# Sorted the way `terraform state list` emits addresses: ECR first, then a
# state list larger than a pipe buffer so grep -q closes the pipe early.
{
  echo 'module.data_lake.aws_ecr_repository.jobs'
  for ((i = 1; i <= 8000; i++)); do
    echo "module.jobs.aws_lambda_function.job[\"job${i}\"]"
  done
} > "${tmp}/large-state.txt"

incident_decision() {
  local file="$1"
  local -a TF_STATE
  mapfile -t TF_STATE < "${file}"
  state_has() { printf '%s\n' "${TF_STATE[@]}" | grep -qx "$1"; }
  if state_has 'module.data_lake.aws_ecr_repository.jobs'; then
    printf '%s\n' skip
  elif printf '%s\n' "${TF_STATE[@]}" | grep -q '^module\.jobs\.aws_lambda_function\.job\['; then
    printf '%s\n' refuse
  else
    printf '%s\n' bootstrap
  fi
}

old="$(incident_decision "${tmp}/large-state.txt" 2>/dev/null)"
if [[ "${old}" != "bootstrap" ]]; then
  echo "expected the old grep -q pipeline to fall through to bootstrap, got '${old}'" >&2
  echo "the fixture may be too small to SIGPIPE grep -q under pipefail" >&2
  exit 1
fi
expect_decision "${tmp}/large-state.txt" skip

unset ALLOW_DESTROY MAX_DESTROY || true

expect_gate 1 testdata/plan-lambda-delete.json

jq -n '{
  format_version: "1.2",
  resource_changes: [
    {
      address: "module.jobs.aws_cloudwatch_log_group.job[\"prices\"]",
      mode: "managed",
      type: "aws_cloudwatch_log_group",
      change: {actions: ["delete", "create"]}
    },
    {
      address: "module.jobs.aws_lambda_function.job[\"prices\"]",
      mode: "managed",
      type: "aws_lambda_function",
      change: {actions: ["update"]}
    }
  ]
}' > "${tmp}/log-replace.json"
expect_gate 1 "${tmp}/log-replace.json"

jq -n '{
  format_version: "1.2",
  resource_changes: [
    {
      address: "module.jobs.aws_lambda_function.job[\"prices\"]",
      mode: "managed",
      type: "aws_lambda_function",
      change: {actions: ["create", "delete"]}
    }
  ]
}' > "${tmp}/create-before-destroy.json"
expect_gate 1 "${tmp}/create-before-destroy.json"

jq -n '{
  format_version: "1.2",
  resource_changes: [
    {
      address: "module.jobs.aws_lambda_function.job[\"prices\"]",
      mode: "managed",
      type: "aws_lambda_function",
      change: {actions: ["update"]}
    },
    {
      address: "module.jobs.aws_cloudwatch_log_group.job[\"prices\"]",
      mode: "managed",
      type: "aws_cloudwatch_log_group",
      change: {actions: ["no-op"]}
    },
    {
      address: "data.aws_caller_identity.current",
      mode: "data",
      type: "aws_caller_identity",
      change: {actions: ["read"]}
    }
  ]
}' > "${tmp}/normal-deploy.json"
expect_gate 0 "${tmp}/normal-deploy.json"

# A data source must not trip the Lambda rule, even if its type string matches.
jq -n '{
  resource_changes: [
    {
      address: "data.aws_lambda_function.existing",
      mode: "data",
      type: "aws_lambda_function",
      change: {actions: ["delete"]}
    }
  ]
}' > "${tmp}/data-source.json"
expect_gate 0 "${tmp}/data-source.json"

roles_plan 10 "${tmp}/ten.json"
roles_plan 11 "${tmp}/eleven.json"
expect_gate 0 "${tmp}/ten.json"
expect_gate 1 "${tmp}/eleven.json"

echo '{}' > "${tmp}/empty-object.json"
printf '%s\n' '{"resource_changes": null}' > "${tmp}/null-changes.json"
expect_gate 0 "${tmp}/empty-object.json"
expect_gate 0 "${tmp}/null-changes.json"

printf '%s\n' '{"resource_changes": {"not": "a list"}}' > "${tmp}/bad-shape.json"
printf '%s\n' '{' > "${tmp}/invalid.json"
printf '%s\n' '[]' > "${tmp}/array.json"
expect_gate 1 "${tmp}/bad-shape.json"
expect_gate 1 "${tmp}/invalid.json"
expect_gate 1 "${tmp}/array.json"
expect_gate 1 "${tmp}/missing-plan.json"

# Address backup when type was dropped from the JSON.
jq -n '{
  resource_changes: [
    {
      address: "module.jobs.aws_lambda_function.job[\"prices\"]",
      mode: "managed",
      change: {actions: ["delete"]}
    }
  ]
}' > "${tmp}/address-only.json"
expect_gate 1 "${tmp}/address-only.json"

ALLOW_DESTROY=true expect_gate 0 testdata/plan-lambda-delete.json
ALLOW_DESTROY=true expect_gate 0 "${tmp}/eleven.json"
ALLOW_DESTROY=1 expect_gate 1 testdata/plan-lambda-delete.json
ALLOW_DESTROY=TRUE expect_gate 1 testdata/plan-lambda-delete.json
ALLOW_DESTROY=false expect_gate 1 testdata/plan-lambda-delete.json
ALLOW_DESTROY=true expect_gate 1 "${tmp}/invalid.json"
unset ALLOW_DESTROY

MAX_DESTROY=2 expect_gate 1 "${tmp}/ten.json"
MAX_DESTROY=10 expect_gate 0 "${tmp}/ten.json"
MAX_DESTROY=100 expect_gate 1 testdata/plan-lambda-delete.json
MAX_DESTROY=08 expect_gate 1 "${tmp}/normal-deploy.json"
unset MAX_DESTROY

echo "deploy guard fixtures passed"
