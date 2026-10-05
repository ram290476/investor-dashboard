"""Read provider API keys from SSM Parameter Store inside job Lambdas.

Keys are cached for 5 minutes, so a rotated key is picked up within one cache
period without redeploying. The job role needs ssm:GetParameter on
/<project>/api-keys/* and kms:Decrypt on the data key (Terraform outputs
`api_key_path` and `data_key_arn`).

    from api_keys import api_key
    fred_key = api_key("fred")
"""

from __future__ import annotations

import os

from aws_lambda_powertools.utilities import parameters

PROJECT = os.getenv("POWERTOOLS_SERVICE_NAME", "invdash")
CACHE_SECONDS = 300


class KeyNotSetError(RuntimeError):
    """The parameter still holds Terraform's placeholder; run scripts/rotate-key.sh."""


def api_key(name: str) -> str:
    value = parameters.get_parameter(f"/{PROJECT}/api-keys/{name}", decrypt=True, max_age=CACHE_SECONDS)
    if not value or value == "REPLACE_ME":
        raise KeyNotSetError(f"API key '{name}' has not been stored yet (scripts/rotate-key.sh {name})")
    return value
