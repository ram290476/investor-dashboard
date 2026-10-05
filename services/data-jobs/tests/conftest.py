"""Make app/ and every functions/<name>/ importable, matching the flat layout of the Lambda image."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in [ROOT / "app", *sorted((ROOT / "functions").iterdir())]:
    if p.is_dir():
        sys.path.insert(0, str(p))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("POWERTOOLS_TRACE_DISABLED", "true")
os.environ.setdefault("POWERTOOLS_METRICS_NAMESPACE", "InvestorDashboard")
