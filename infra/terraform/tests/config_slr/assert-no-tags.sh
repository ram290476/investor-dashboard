#!/usr/bin/env bash
# Plan the fixture stack with the real AWS provider (no AWS API calls) and
# require that the untagged Config role does not inherit default_tags, while
# the control role on the default provider still does.
set -euo pipefail

cd "$(dirname "$0")"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_EC2_METADATA_DISABLED=true

terraform init -backend=false -input=false >/dev/null
plan_file="$(mktemp)"
plan_json="$(mktemp)"
trap 'rm -f "${plan_file}" "${plan_json}"' EXIT

terraform plan -input=false -out="${plan_file}" >/dev/null
terraform show -json "${plan_file}" > "${plan_json}"

python3 - "${plan_json}" << 'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    plan = json.load(handle)

by_address = {change["address"]: change for change in plan.get("resource_changes") or []}

def after(address):
    change = by_address[address]["change"]
    return change.get("after") or {}, change.get("after_unknown") or {}

config_after, config_unknown = after("aws_iam_service_linked_role.config")
control_after, _control_unknown = after("aws_iam_service_linked_role.control")

config_tags = config_after.get("tags")
config_tags_all = config_after.get("tags_all")
tags_all_unknown = config_unknown.get("tags_all", False) is True or (
    isinstance(config_unknown.get("tags_all"), dict) and config_unknown.get("tags_all")
)

if config_tags not in (None, {}):
    raise SystemExit(f"config role tags must be empty, got {config_tags!r}")

# Unknown tags_all means the provider has no tags to send. A map means it
# planned concrete tags; that map must be empty and must not be default_tags.
if config_tags_all not in (None, {}) and not tags_all_unknown:
    raise SystemExit(f"config role tags_all must be empty, got {config_tags_all!r}")
if isinstance(config_tags_all, dict) and config_tags_all:
    raise SystemExit(f"config role inherited tags: {config_tags_all!r}")

control_tags_all = control_after.get("tags_all") or {}
if control_tags_all.get("Project") != "investor-dashboard":
    raise SystemExit(
        "default provider must still apply default_tags to other resources, "
        f"got {control_tags_all!r}"
    )

actions = by_address["aws_iam_service_linked_role.config"]["change"]["actions"]
if actions != ["create"]:
    raise SystemExit(f"fixture plan should create the role, got {actions!r}")
print("config service-linked role plans with no tags; default_tags still apply elsewhere")
PY
