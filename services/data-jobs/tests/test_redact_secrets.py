import httpx
import pytest

import observability
from observability import redact_secrets, source_run


def test_redact_secrets_scrubs_query_credentials():
    text = (
        "Client error '403 Forbidden' for url "
        "'https://finnhub.io/api/v1/company-news?symbol=FNDX&from=2026-10-15&token=abc123SECRET' and "
        "https://api.massive.com/v2/reference/news?ticker=X&apiKey=zzz999&limit=20"
    )
    out = redact_secrets(text)
    assert "abc123SECRET" not in out and "zzz999" not in out
    assert "token=[REDACTED]" in out and "apiKey=[REDACTED]" in out
    assert "symbol=FNDX" in out and "limit=20" in out


def test_source_run_error_text_has_no_token(monkeypatch):
    request = httpx.Request("GET", "https://finnhub.io/api/v1/company-news?symbol=FNDX&token=abc123SECRET")
    response = httpx.Response(403, request=request)
    with source_run("DS-41") as record:
        response.raise_for_status()
    assert record["outcome"] == "failure"
    assert "abc123SECRET" not in record["error"]
    assert "token=[REDACTED]" in record["error"]


def test_job_failed_log_traceback_is_redacted(monkeypatch):
    seen = {}
    monkeypatch.setattr(observability.logger, "error", lambda msg, extra=None, **kw: seen.update(extra or {}))
    try:
        raise RuntimeError("boom for url https://finnhub.io/x?token=abc123SECRET")
    except RuntimeError:
        observability._log_job_failed("failure")
    assert "abc123SECRET" not in seen["exception"]
    assert "token=[REDACTED]" in seen["exception"]


@pytest.mark.parametrize("value", ["", "no secrets here"])
def test_redact_secrets_passthrough(value):
    assert redact_secrets(value) == value
