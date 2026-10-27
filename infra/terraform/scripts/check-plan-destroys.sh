#!/usr/bin/env bash
# Refuse a Terraform plan that deletes or replaces any managed resource.
#
# Usage: check-plan-destroys.sh PLAN_JSON
#   PLAN_JSON is the file written by `terraform show -json tfplan`.
#
# Exit 0 when the plan may be applied. Exit 1 when the file is missing, the
# JSON cannot be interpreted, or any managed resource change includes the
# action "delete". That is a plain delete, or a replace in either order
# (["delete","create"] or ["create","delete"]). The threshold is zero for
# every resource type. A caller that cannot parse the plan must abort; it
# must not fall through to apply.
#
# Data sources (mode "data") are ignored. An action of "forget" is not a
# delete: Terraform uses it for a removed block with destroy = false, which
# drops the object from state and leaves it in AWS. A removed block that
# destroys the object is action "delete" and is blocked. There is no separate
# removed-block list in the plan JSON this script reads.
#
# ALLOW_DESTROY=true (exactly that string) waives the policy after the JSON
# has parsed, prints a warning, and lists each destroyed or replaced address.
# The deploy workflow sets it only for workflow_dispatch when the production
# environment variable DEPLOY_ALLOW_DESTROY is true. Any other value,
# including "false", "1", "TRUE", and an empty string, keeps the policy.
# Parse failures still abort when the waiver is set.
#
# MAX_DESTROY is not a waiver. Deploy run 40567560827 replaced one ECR
# lifecycle policy because the old default of 10 allowed it. A value that is
# set and is not a non-negative integer aborts, so a typo cannot change the
# decision. A valid value does not raise the zero-delete threshold.
#
# jq reads the plan file itself. Do not pipe this into head or grep -q: a
# consumer that exits early SIGPIPEs the producer under pipefail and can look
# like a pass.
set -euo pipefail

plan_json="${1:-}"

if [[ -z "${plan_json}" || ! -f "${plan_json}" || ! -r "${plan_json}" ]]; then
  echo "::error::Destroy gate needs a readable terraform show -json file; aborting deploy." >&2
  exit 1
fi

if [[ -n "${MAX_DESTROY+x}" ]]; then
  if ! [[ "${MAX_DESTROY}" =~ ^(0|[1-9][0-9]*)$ ]]; then
    echo "::error::MAX_DESTROY must be a non-negative integer; aborting deploy." >&2
    exit 1
  fi
fi

summary_file="$(mktemp)"
trap 'rm -f "${summary_file}"' EXIT

if ! jq -e -c '
  def managed_actions:
    if .mode == "data" then
      empty
    elif (.change | type) != "object" or (.change.actions | type) != "array" then
      error("change.actions is not an array")
    else
      .
    end;
  if type != "object" then
    error("plan JSON must be an object")
  elif (has("resource_changes") | not) then
    {count: 0, items: []}
  elif (.resource_changes | type) != "array" then
    error("resource_changes must be an array")
  else
    [
      .resource_changes[]
      | if type != "object" then
          error("resource_changes entries must be objects")
        else
          .
        end
      | managed_actions
      | select(.change.actions | index("delete") != null)
      | {
          address: (.address // "unknown"),
          type: (.type // "unknown"),
          actions: (.change.actions | join(","))
        }
    ] as $d
    | {count: ($d | length), items: $d}
  end
' "${plan_json}" > "${summary_file}"; then
  echo "::error::Could not parse the Terraform plan; aborting deploy." >&2
  exit 1
fi

count="$(jq -r '.count' "${summary_file}")"
if ! [[ "${count}" =~ ^(0|[1-9][0-9]*)$ ]]; then
  echo "::error::Plan summary was not numeric; aborting deploy." >&2
  exit 1
fi

list_destroys() {
  jq -r '.items[] | "\(.type) \(.address) [\(.actions)]"' "${summary_file}" >&2
}

if [[ "${ALLOW_DESTROY:-}" == "true" ]]; then
  echo "::warning::DEPLOY_ALLOW_DESTROY is true; destroy limits are waived for this plan (${count} delete/replace)." >&2
  if [[ "${count}" -gt 0 ]]; then
    list_destroys
  fi
  exit 0
fi

if (( count > 0 )); then
  echo "::error::Refusing deploy: plan deletes or replaces ${count} resource(s). Any delete or replace is blocked. An admin must set the production environment variable DEPLOY_ALLOW_DESTROY to true, dispatch Deploy production from main, then unset that variable. A workflow_run deploy ignores it." >&2
  list_destroys
  exit 1
fi

echo "Destroy gate passed (0 delete/replace action(s))."
