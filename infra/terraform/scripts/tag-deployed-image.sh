#!/usr/bin/env bash
# Tag the job image a successful apply just deployed so the ECR lifecycle
# policy can keep that digest. An IMMUTABLE repository still accepts a new
# tag on an existing manifest (aws ecr put-image).
#
# Refuses to run unless APPLY_SUCCEEDED=1. deploy.yml sets that only after
# `terraform apply` exits 0, so a failed apply never marks a newer image that
# is not live. The new tag is deployed-<source-tag> on the same digest.
#
# Usage: APPLY_SUCCEEDED=1 tag-deployed-image.sh <repository-url> <source-tag>
set -euo pipefail

if [[ "${APPLY_SUCCEEDED:-}" != "1" ]]; then
  echo "::error::refusing to tag the live image; terraform apply has not succeeded." >&2
  exit 1
fi

if [[ $# -ne 2 || -z "${1}" || -z "${2}" ]]; then
  echo "usage: APPLY_SUCCEEDED=1 tag-deployed-image.sh <repository-url> <source-tag>" >&2
  exit 1
fi

repository_url="${1}"
source_tag="${2}"
repo_name="${repository_url#*/}"
deployed_tag="deployed-${source_tag}"

if [[ "${repo_name}" == "${repository_url}" || -z "${repo_name}" ]]; then
  echo "::error::repository URL must be registry/name, got '${repository_url}'." >&2
  exit 1
fi

manifest="$(
  aws ecr batch-get-image \
    --repository-name "${repo_name}" \
    --image-ids "imageTag=${source_tag}" \
    --output json |
    jq -er '.images[0].imageManifest'
)"

put_err="$(mktemp)"
trap 'rm -f "${put_err}"' EXIT

set +e
aws ecr put-image \
  --repository-name "${repo_name}" \
  --image-tag "${deployed_tag}" \
  --image-manifest "${manifest}" \
  >/dev/null 2>"${put_err}"
status=$?
set -e

if [[ "${status}" -ne 0 ]]; then
  if grep -q 'ImageAlreadyExistsException' "${put_err}"; then
    echo "tag ${deployed_tag} already points at this digest"
    exit 0
  fi
  cat "${put_err}" >&2
  exit "${status}"
fi

echo "tagged ${repo_name}:${deployed_tag}"
