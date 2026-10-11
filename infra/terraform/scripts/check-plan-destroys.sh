#!/usr/bin/env bash
# Refuse a Terraform plan that would destroy too much infrastructure.
#
# Usage: check-plan-destroys.sh PLAN_JSON
#   PLAN_JSON is the file written by `terraform show -json tfplan`.
#
# Exit 0 when the plan may be applied. Exit 1 when the file is missing, the
# JSON cannot be interpreted, or the destroy policy fails. A caller that cannot
# parse the plan must abort; it must not fall through to apply.
#
# The policy fails when the plan deletes or replaces more than MAX_DESTROY
# managed resources (default 10), or deletes or replaces any aws_lambda_function
# or aws_cloudwatch_log_group. Replacement counts because its actions include
# "delete", and replacing a log group drops the retained events.
#
# ALLOW_DESTROY=true (exactly that string) waives the policy after the JSON has
# parsed. The deploy workflow sets it only for workflow_dispatch, and only when
# the production environment variable DEPLOY_ALLOW_DESTROY is true. Any other
# value, including "false", "1", and an empty string, keeps the policy. Parse
# failures still abort when the waiver is set.
set -euo pipefail

plan_json="${1:-}"
max_destroy="${MAX_DESTROY:-10}"

if [[ -z "${plan_json}" || ! -f "${plan_json}" || ! -r "${plan_json}" ]]; then
  echo "::error::Destroy gate needs a readable terraform show -json file; aborting deploy." >&2
  exit 1
fi

if ! [[ "${max_destroy}" =~ ^(0|[1-9][0-9]*)$ ]]; then
  echo "::error::MAX_DESTROY must be a non-negative integer; aborting deploy." >&2
  exit 1
fi

summary_file="$(mktemp)"
trap 'rm -f "${summary_file}"' EXIT

# jq reads the plan file itself. Keep this out of a pipe: a consumer that exits
# early would SIGPIPE the producer under pipefail and look like a policy pass.
if ! jq -e -c '
  def action_deletes:
    if (.change.actions | type) == "array" then
      (.change.actions | index("delete") != null)
    elif .change == null or .change.actions == null then
      false
    else
      error("change.actions is not an array")
    end;
  if type != "object" then
    error("plan JSON must be an object")
  elif (.resource_changes != null and (.resource_changes | type) != "array") then
    error("resource_changes must be an array")
  else
    [
      (.resource_changes // [])[]
      | select(type == "object")
      | select(.mode != "data")
      | select(action_deletes)
      | {
          address: (.address // "unknown"),
          type: (.type // "unknown"),
          actions: (.change.actions | join(","))
        }
    ] as $d
    | {
        count: ($d | length),
        protected: (
          $d
          | map(select(
              .type == "aws_lambda_function"
              or .type == "aws_cloudwatch_log_group"
              or (.address | test("(^|\\.)aws_lambda_function\\."))
              or (.address | test("(^|\\.)aws_cloudwatch_log_group\\."))
            ))
          | length
        ),
        items: $d
      }
  end
' "${plan_json}" > "${summary_file}"; then
  echo "::error::Could not parse the Terraform plan; aborting deploy." >&2
  exit 1
fi

count="$(jq -r '.count' "${summary_file}")"
protected="$(jq -r '.protected' "${summary_file}")"
if ! [[ "${count}" =~ ^(0|[1-9][0-9]*)$ && "${protected}" =~ ^(0|[1-9][0-9]*)$ ]]; then
  echo "::error::Plan summary was not numeric; aborting deploy." >&2
  exit 1
fi

list_destroys() {
  jq -r '.items[] | "\(.type) \(.address) [\(.actions)]"' "${summary_file}" >&2
}

if [[ "${ALLOW_DESTROY:-}" == "true" ]]; then
  echo "::warning::DEPLOY_ALLOW_DESTROY is true; destroy limits are waived for this plan (${count} delete/replace, ${protected} Lambda or log group)." >&2
  if [[ "${count}" -gt 0 ]]; then
    list_destroys
  fi
  exit 0
fi

if (( count > max_destroy || protected > 0 )); then
  echo "::error::Refusing deploy: plan destroys ${count} resource(s) (limit ${max_destroy}), including ${protected} Lambda function or CloudWatch log group resource(s). An admin must set the production environment variable DEPLOY_ALLOW_DESTROY to true, dispatch Deploy production from main, then unset that variable. A workflow_run deploy ignores it." >&2
  list_destroys
  exit 1
fi

echo "Destroy gate passed (${count} delete/replace action(s); limit ${max_destroy})."
