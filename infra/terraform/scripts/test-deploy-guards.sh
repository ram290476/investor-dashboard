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

expect_gate_text() {
  local want="$1"
  local file="$2"
  local needle="$3"
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
  if ! grep -q -F -- "${needle}" <<< "${err}"; then
    echo "gate ${file}: output missing '${needle}'" >&2
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
# Any delete is blocked. The old default limit of 10 let a single replace through.
expect_gate 1 "${tmp}/ten.json"
expect_gate 1 "${tmp}/eleven.json"

echo '{}' > "${tmp}/empty-object.json"
printf '%s\n' '{"resource_changes": null}' > "${tmp}/null-changes.json"
expect_gate 0 "${tmp}/empty-object.json"
expect_gate 1 "${tmp}/null-changes.json"

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
ALLOW_DESTROY='' expect_gate 1 testdata/plan-lambda-delete.json
ALLOW_DESTROY=true expect_gate 1 "${tmp}/invalid.json"
unset ALLOW_DESTROY

# MAX_DESTROY does not raise the zero-delete threshold. A non-integer still aborts.
MAX_DESTROY=2 expect_gate 1 "${tmp}/ten.json"
MAX_DESTROY=10 expect_gate 1 "${tmp}/ten.json"
MAX_DESTROY=100 expect_gate 1 testdata/plan-lambda-delete.json
MAX_DESTROY=08 expect_gate 1 "${tmp}/normal-deploy.json"
unset MAX_DESTROY

# Issue #323: the replace from deploy run 40567560827 must be blocked, and the
# cases below are the fixtures that CI runs with this script.
expect_gate_text 1 testdata/plan-ecr-lifecycle-replace.json \
  "module.data_lake.aws_ecr_lifecycle_policy.jobs"
ALLOW_DESTROY=false expect_gate_text 1 testdata/plan-ecr-lifecycle-replace.json \
  "module.data_lake.aws_ecr_lifecycle_policy.jobs"
expect_gate_text 1 testdata/plan-create-before-destroy.json 'module.x.aws_y.z["k"]'
expect_gate_text 1 testdata/plan-single-delete.json "module.audit_logging.aws_iam_role.config"
expect_gate 0 testdata/plan-create-update-noop.json
expect_gate 0 testdata/plan-empty.json
expect_gate 0 testdata/plan-forget.json
ALLOW_DESTROY=true expect_gate_text 0 testdata/plan-ecr-lifecycle-replace.json "::warning::"
ALLOW_DESTROY=true expect_gate_text 0 testdata/plan-ecr-lifecycle-replace.json \
  "module.data_lake.aws_ecr_lifecycle_policy.jobs"
ALLOW_DESTROY=1 expect_gate 1 testdata/plan-ecr-lifecycle-replace.json
ALLOW_DESTROY=TRUE expect_gate 1 testdata/plan-ecr-lifecycle-replace.json
ALLOW_DESTROY='' expect_gate 1 testdata/plan-ecr-lifecycle-replace.json
unset ALLOW_DESTROY
expect_gate 1 testdata/plan-malformed.json
expect_gate 1 testdata/plan-actions-not-array.json
expect_gate 1 testdata/plan-actions-missing.json
expect_gate 1 testdata/plan-resource-changes-null.json
ALLOW_DESTROY=true expect_gate 1 testdata/plan-malformed.json
ALLOW_DESTROY=true expect_gate 1 testdata/plan-actions-not-array.json
ALLOW_DESTROY=true expect_gate 1 testdata/plan-actions-missing.json
ALLOW_DESTROY=true expect_gate 1 "${tmp}/missing-plan.json"
unset ALLOW_DESTROY

# A data source is ignored even when its actions are not an array.
jq -n '{
  resource_changes: [
    {
      address: "data.aws_caller_identity.current",
      mode: "data",
      type: "aws_caller_identity",
      change: {actions: "not-an-array"}
    }
  ]
}' > "${tmp}/data-bad-actions.json"
expect_gate 0 "${tmp}/data-bad-actions.json"

# A delete at the end of a large plan must still count. Piping the summary
# into a command that closes early would SIGPIPE and can look like a pass.
jq -n '{
  format_version: "1.2",
  resource_changes: (
    [range(4000) | {
      address: ("aws_s3_bucket.kept[" + tostring + "]"),
      mode: "managed",
      type: "aws_s3_bucket",
      change: {actions: ["update"]}
    }] + [{
      address: "module.data_lake.aws_ecr_lifecycle_policy.jobs",
      mode: "managed",
      type: "aws_ecr_lifecycle_policy",
      change: {actions: ["delete", "create"]}
    }]
  )
}' > "${tmp}/large-plan-replace.json"
expect_gate_text 1 "${tmp}/large-plan-replace.json" \
  "module.data_lake.aws_ecr_lifecycle_policy.jobs"
jq -n '{
  resource_changes: [range(4000) | {
    address: ("aws_s3_bucket.kept[" + tostring + "]"),
    mode: "managed",
    type: "aws_s3_bucket",
    change: {actions: ["no-op"]}
  }]
}' > "${tmp}/large-plan-noop.json"
expect_gate 0 "${tmp}/large-plan-noop.json"

expect_self() {
  local want="$1"
  local file="$2"
  local status
  set +e
  bash ./check-self-policy.sh "${file}" >/dev/null 2>&1
  status=$?
  set -e
  if [[ "${status}" -ne "${want}" ]]; then
    echo "self-policy ${file}: want exit ${want}, got ${status}" >&2
    exit 1
  fi
}

self_plan() {
  jq -n --arg a "$1" '{resource_changes: [{address: "module.github_deploy[0].aws_iam_role_policy.iam_for_stack", mode: "managed", type: "aws_iam_role_policy", change: {actions: [$a]}}]}' > "$2"
}
self_plan update "${tmp}/self-update.json"
self_plan no-op "${tmp}/self-noop.json"
expect_self 1 "${tmp}/self-update.json"
expect_self 0 "${tmp}/self-noop.json"
expect_self 0 "${tmp}/empty-object.json"
expect_self 0 testdata/plan-lambda-delete.json
expect_self 1 "${tmp}/invalid.json"

# Tagging the live digest runs only after terraform apply exits 0.
# The script refuses any other APPLY_SUCCEEDED value, and deploy.yml sets
# APPLY_SUCCEEDED=1 on the same line, after the jobs apply, nowhere else.
stub_bin="${tmp}/bin"
mkdir -p "${stub_bin}"
cat > "${stub_bin}/aws" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${AWS_STUB_LOG}"
if [[ "${1}" == "ecr" && "${2}" == "batch-get-image" ]]; then
  printf '%s\n' '{"images":[{"imageManifest":"{\"schemaVersion\":2}"}]}'
  exit 0
fi
if [[ "${1}" == "ecr" && "${2}" == "put-image" ]]; then
  if [[ "${AWS_STUB_PUT_STATUS:-0}" != "0" ]]; then
    echo "ImageAlreadyExistsException: tag exists" >&2
    exit 1
  fi
  exit 0
fi
echo "unexpected aws invocation: $*" >&2
exit 1
EOF
chmod +x "${stub_bin}/aws"

aws_log="${tmp}/aws.log"
: > "${aws_log}"

expect_tag_refuse() {
  local status
  : > "${aws_log}"
  set +e
  env "$@" PATH="${stub_bin}:${PATH}" AWS_STUB_LOG="${aws_log}" \
    bash ./tag-deployed-image.sh \
    "123456789012.dkr.ecr.us-west-1.amazonaws.com/invdash/jobs" \
    "abc123-1" >/dev/null 2>&1
  status=$?
  set -e
  if [[ "${status}" -eq 0 ]]; then
    echo "tag script should have refused: $*" >&2
    exit 1
  fi
  if [[ -s "${aws_log}" ]]; then
    echo "tag script called AWS before apply succeeded: $*" >&2
    exit 1
  fi
}

expect_tag_refuse
expect_tag_refuse APPLY_SUCCEEDED=
expect_tag_refuse APPLY_SUCCEEDED=0
expect_tag_refuse APPLY_SUCCEEDED=true
expect_tag_refuse APPLY_SUCCEEDED=yes

: > "${aws_log}"
APPLY_SUCCEEDED=1 AWS_STUB_LOG="${aws_log}" PATH="${stub_bin}:${PATH}" \
  bash ./tag-deployed-image.sh \
  "123456789012.dkr.ecr.us-west-1.amazonaws.com/invdash/jobs" \
  "abc123-1" >/dev/null
if ! grep -q -- '--repository-name invdash/jobs' "${aws_log}"; then
  echo "tag script did not address invdash/jobs" >&2
  exit 1
fi
if ! grep -q -- '--image-tag deployed-abc123-1' "${aws_log}"; then
  echo "tag script did not add deployed-abc123-1" >&2
  cat "${aws_log}" >&2
  exit 1
fi

: > "${aws_log}"
APPLY_SUCCEEDED=1 AWS_STUB_PUT_STATUS=1 AWS_STUB_LOG="${aws_log}" PATH="${stub_bin}:${PATH}" \
  bash ./tag-deployed-image.sh \
  "123456789012.dkr.ecr.us-west-1.amazonaws.com/invdash/jobs" \
  "abc123-1" >/dev/null

set +e
APPLY_SUCCEEDED=1 PATH="${stub_bin}:${PATH}" AWS_STUB_LOG="${aws_log}" \
  bash ./tag-deployed-image.sh >/dev/null 2>&1
status=$?
set -e
if [[ "${status}" -eq 0 ]]; then
  echo "tag script should reject a missing repository URL" >&2
  exit 1
fi

# This file already cd'd into infra/terraform/scripts. $0 may be relative to
# the caller's directory, so do not resolve it again from here.
repo_root="$(cd ../../.. && pwd)"
awk '
  /^      - name: / {
    in_jobs = index($0, "Deploy job functions") > 0
    if (!in_jobs) {
      jobs_applied = 0
    }
  }
  in_jobs && /terraform apply -input=false -auto-approve tfplan/ {
    jobs_applied = 1
  }
  /tag-deployed-image\.sh/ {
    if (!in_jobs || !jobs_applied) {
      print "tag-deployed-image.sh must run only after a successful apply in the job deploy step" > "/dev/stderr"
      exit 1
    }
    if (index($0, "APPLY_SUCCEEDED=1") == 0) {
      print "tag-deployed-image.sh must be invoked with APPLY_SUCCEEDED=1" > "/dev/stderr"
      exit 1
    }
    count++
  }
  END {
    if (count != 1) {
      print "expected exactly one tag-deployed-image.sh invocation" > "/dev/stderr"
      exit 1
    }
  }
' "${repo_root}/.github/workflows/deploy.yml"

# Every terraform apply in deploy.yml, including bootstrap, must be the saved
# plan that plan-guard.sh just checked in the same step. An ad-hoc apply -var
# is rejected.
deploy_file="${repo_root}/.github/workflows/deploy.yml"
apply_lines=()
while IFS= read -r line_no; do
  apply_lines+=("${line_no}")
done < <(grep -nE '^[[:space:]]*terraform apply' "${deploy_file}" | cut -d: -f1)
if [[ "${#apply_lines[@]}" -lt 1 ]]; then
  echo "deploy.yml has no terraform apply" >&2
  exit 1
fi
guard_count="$(grep -cE '^[[:space:]]*bash scripts/plan-guard.sh' "${deploy_file}")"
if [[ "${guard_count}" -ne "${#apply_lines[@]}" ]]; then
  echo "deploy.yml has ${#apply_lines[@]} terraform apply commands and ${guard_count} plan-guard.sh calls" >&2
  exit 1
fi
for line_no in "${apply_lines[@]}"; do
  text="$(sed -n "${line_no}p" "${deploy_file}")"
  if [[ ! "${text}" =~ ^[[:space:]]*terraform[[:space:]]+apply[[:space:]]+-input=false[[:space:]]+-auto-approve[[:space:]]+tfplan[[:space:]]*$ ]]; then
    echo "terraform apply must be the guarded saved plan, got:${text}" >&2
    exit 1
  fi
  header_line="$(awk -v n="${line_no}" 'NR < n && /^      - name: / { h = NR } END { print h + 0 }' "${deploy_file}")"
  if [[ "${header_line}" -lt 1 ]]; then
    echo "terraform apply at line ${line_no} is not inside a step" >&2
    exit 1
  fi
  chunk="$(sed -n "${header_line},${line_no}p" "${deploy_file}")"
  if ! grep -qE '^[[:space:]]*bash scripts/plan-guard.sh' <<< "${chunk}"; then
    echo "terraform apply at line ${line_no} is not preceded by plan-guard.sh in the same step" >&2
    exit 1
  fi
done

echo "deploy guard fixtures passed"
