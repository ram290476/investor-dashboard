#!/usr/bin/env bash
# Store a newly regenerated provider API key in SSM Parameter Store (IA-5).
#
# Usage: scripts/rotate-key.sh <key-name> [project]
#   e.g. scripts/rotate-key.sh sam-gov
#
# The value is read silently from the terminal and sent on stdin, so it never
# appears in shell history or the process list. It is encrypted with the
# project's data KMS key. The rotation check uses the parameter's last-modified
# date as "last rotated", and the api-key-changed alert emails the security topic.
set -euo pipefail

name="${1:?usage: rotate-key.sh <key-name> [project]}"
project="${2:-invdash}"
param="/${project}/api-keys/${name}"

found="$(aws ssm describe-parameters \
  --parameter-filters "Key=Name,Values=${param}" \
  --query 'Parameters[0].Name' --output text)"
if [[ "${found}" != "${param}" ]]; then
  echo "No key named ${param}. Known keys:" >&2
  aws ssm describe-parameters \
    --parameter-filters "Key=Path,Option=Recursive,Values=/${project}/api-keys" \
    --query 'Parameters[].Name' --output text | tr '\t' '\n' >&2
  exit 1
fi

read -r -s -p "Paste the new value for ${name}: " value
echo
if [[ -z "${value}" ]]; then
  echo "Empty value; nothing stored." >&2
  exit 1
fi

PARAM="${param}" KEY_ALIAS="alias/${project}-data" VALUE="${value}" python3 - <<'PY' |
import json, os
print(json.dumps({
    "Name": os.environ["PARAM"],
    "Type": "SecureString",
    "KeyId": os.environ["KEY_ALIAS"],
    "Value": os.environ["VALUE"],
    "Overwrite": True,
}))
PY
  aws ssm put-parameter --cli-input-json file:///dev/stdin >/dev/null
unset value

rotation_days="$(aws ssm list-tags-for-resource --resource-type Parameter --resource-id "${param}" \
  --query "TagList[?Key=='RotationDays'].Value | [0]" --output text)"
next_due="$(python3 -c "import datetime as d; print((d.date.today()+d.timedelta(days=int('${rotation_days}'))).isoformat())")"

echo "Stored ${param}. Next rotation due ${next_due} (every ${rotation_days} days)."
echo "Next: confirm the next job run succeeds, then revoke the old key in the provider's dashboard."
