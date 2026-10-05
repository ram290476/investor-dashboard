"""Small S3 helpers for the data lake (raw/, curated/, serving/).

Writes go to the job's own key so concurrent jobs never overwrite each other;
SSE-KMS with the data key is the bucket default, so callers pass nothing extra.
"""

from __future__ import annotations

import io
import json
import os

import boto3
import polars as pl

LAKE_BUCKET = os.getenv("LAKE_BUCKET", "")
_s3 = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def write_parquet(df: pl.DataFrame, key: str, bucket: str | None = None) -> str:
    buf = io.BytesIO()
    df.write_parquet(buf, compression="zstd")
    s3().put_object(
        Bucket=bucket or LAKE_BUCKET, Key=key, Body=buf.getvalue(), ContentType="application/vnd.apache.parquet"
    )
    return key


def write_json(obj, key: str, bucket: str | None = None, cache_seconds: int = 60) -> str:
    s3().put_object(
        Bucket=bucket or LAKE_BUCKET,
        Key=key,
        Body=json.dumps(obj, separators=(",", ":"), default=str).encode(),
        ContentType="application/json",
        CacheControl=f"max-age={cache_seconds}",
    )
    return key


def read_parquet_prefix(prefix: str, bucket: str | None = None) -> pl.DataFrame:
    """Concatenate every Parquet object under a prefix (small curated tables only)."""
    bucket = bucket or LAKE_BUCKET
    frames = []
    for page in s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".parquet"):
                body = s3().get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                frames.append(pl.read_parquet(io.BytesIO(body)))
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def read_json(key: str, bucket: str | None = None):
    try:
        body = s3().get_object(Bucket=bucket or LAKE_BUCKET, Key=key)["Body"].read()
    except s3().exceptions.NoSuchKey:
        return None
    return json.loads(body)
