#!/usr/bin/env bash
# Classify a `terraform state list` snapshot for the production bootstrap decision.
#
# Usage: decide-bootstrap.sh STATE_LIST_FILE
# Prints exactly one word to stdout:
#   skip       The jobs ECR repository is already in state. Do not bootstrap.
#   refuse     A job Lambda is in state and the ECR repository is not.
#   bootstrap  Neither is in state. A first apply with an empty image is allowed.
# Exit 1, with no token, when the file is missing or unreadable. Callers must
# abort the deploy in that case. An empty file is a real empty state and prints
# bootstrap; a failed `terraform state list` must not be turned into that file.
#
# Lines are read from the file and compared in bash. Do not pipe the list into
# `grep -q` under `set -o pipefail`: grep -q exits at the first hit, the writer
# gets SIGPIPE, and the pipeline status looks like a miss. That sent Deploy
# 37496345092 and Deploy 38137774464 (commit f1f5af6) down the empty-image path.
set -euo pipefail

state_file="${1:-}"
if [[ -z "${state_file}" || ! -f "${state_file}" || ! -r "${state_file}" ]]; then
  echo "::error::Bootstrap guard needs a readable terraform state list file; aborting deploy." >&2
  exit 1
fi

ecr="module.data_lake.aws_ecr_repository.jobs"
job_prefix="module.jobs.aws_lambda_function.job["

has_ecr=0
has_jobs=0
while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line}" ]] && continue
  if [[ "${line}" == "${ecr}" ]]; then
    has_ecr=1
  fi
  # The prefix is quoted, so '[' is literal. A role or policy address does not match.
  case "${line}" in
    "${job_prefix}"*) has_jobs=1 ;;
  esac
done < "${state_file}"

if [[ "${has_ecr}" -eq 1 ]]; then
  printf '%s\n' skip
elif [[ "${has_jobs}" -eq 1 ]]; then
  printf '%s\n' refuse
else
  printf '%s\n' bootstrap
fi
