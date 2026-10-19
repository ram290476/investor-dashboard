#!/usr/bin/env bash
# Refuse a plan that changes the deploy role's own inline policy.
#
# The NoSelfModification statement denies invdash-terraform-deploy every IAM
# write on itself, so CI can never apply a change to iam_for_stack (Deploy #229
# failed this way after applying everything else). Fail before apply instead.
# An admin applies the change once with:
#   terraform apply -target='module.github_deploy[0].aws_iam_role_policy.iam_for_stack'
#
# Usage: check-self-policy.sh PLAN_JSON
set -euo pipefail

plan_json="${1:?usage: check-self-policy.sh PLAN_JSON}"

if ! jq -e '(.resource_changes // []) | type == "array"' "${plan_json}" >/dev/null 2>&1; then
  echo "::error::check-self-policy: cannot read plan JSON ${plan_json}." >&2
  exit 1
fi

changed="$(jq -r '
  (.resource_changes // [])[]
  | select((.address // "") | test("aws_iam_role_policy\\.iam_for_stack(\\[|$)"))
  | select(((.change.actions // []) - ["no-op", "read"]) | length > 0)
  | .address' "${plan_json}")"

if [[ -n "${changed}" ]]; then
  echo "::error::Plan changes ${changed}: the deploy role cannot modify its own policy (NoSelfModification deny). This needs a one-time admin apply of only that resource: terraform apply -target='module.github_deploy[0].aws_iam_role_policy.iam_for_stack'. Then rerun the deploy." >&2
  exit 1
fi
