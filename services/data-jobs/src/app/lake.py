"""Small S3 helpers for the data lake (raw/, curated/, serving/).

Writes go to the job's own key so concurrent jobs never overwrite each other, except
the shared yearly price partitions, which use conditional read-merge-write
(upsert_parquet). SSE-KMS with the data key is the bucket default, so callers pass
nothing extra.

Daily prices live in one Parquet object per ticker per year:

    curated/prices_daily/ticker=<T>/year=<YYYY>/prices.parquet

Older deployments wrote one object per ticker-day (date=<D>/daily.parquet, D4) and per
backfill batch (batch_start=<D>/batch_end=<D>/prices_daily.parquet). read_prices still
reads and dedupes those; scripts/compact_price_partitions.py folds them into the yearly
objects.
"""

from __future__ import annotations

import io
import json
import os

import boto3
import polars as pl

LAKE_BUCKET = os.getenv("LAKE_BUCKET", "")
PRICES_PREFIX = "curated/prices_daily/"
# daily_prices (D4, Alpaca) rows win over backfill (DS-05, Yahoo) rows for the same ticker and date.
PREFERRED_PRICE_SOURCE = "DS-02"
UPSERT_ATTEMPTS = 5
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


def price_partition_key(ticker: str, year: int) -> str:
    return f"{PRICES_PREFIX}ticker={ticker}/year={year}/prices.parquet"


def dedupe_prices(prices: pl.DataFrame) -> pl.DataFrame:
    """One row per (ticker, date).

    When rows overlap (backfill vs daily_prices, or legacy vs yearly objects), keep the
    daily_prices row (source_id DS-02); otherwise keep the last row in frame order.
    """
    if prices.is_empty() or not {"ticker", "date"} <= set(prices.columns):
        return prices
    preferred = (
        (pl.col("source_id") == PREFERRED_PRICE_SOURCE).fill_null(False)
        if "source_id" in prices.columns
        else pl.lit(False)
    )
    return (
        prices.with_row_index("_row")
        .with_columns(preferred.cast(pl.Int8).alias("_preferred"))
        .sort(["_preferred", "_row"])
        .unique(subset=["ticker", "date"], keep="last")
        .sort("_row")
        .drop("_row", "_preferred")
    )


def _sort_prices(df: pl.DataFrame) -> pl.DataFrame:
    return df.sort(["ticker", "date"]) if {"ticker", "date"} <= set(df.columns) else df


def _object_exists(client, bucket: str, key: str) -> bool:
    resp = client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
    return any(o["Key"] == key for o in resp.get("Contents", []))


def upsert_parquet(df: pl.DataFrame, key: str, bucket: str | None = None, *, incoming_wins: bool = True) -> int:
    """Merge price rows into one Parquet object: read, concat, dedupe on (ticker, date), write.

    The write is conditional (If-Match on the ETag read, or If-None-Match for a new object),
    so a concurrent writer to the same partition makes this retry instead of losing rows.
    DS-02 rows always beat other sources; among equals, incoming rows win unless
    incoming_wins=False (used by the migration, where existing yearly rows are newer).
    Returns the partition's row count.
    """
    bucket = bucket or LAKE_BUCKET
    client = s3()
    for _ in range(UPSERT_ATTEMPTS):
        existing, etag = pl.DataFrame(), None
        # List first: job roles hold s3:ListBucket only with an s3:prefix condition, so a GET on a
        # missing key returns 403 AccessDenied rather than NoSuchKey.
        if _object_exists(client, bucket, key):
            try:
                obj = client.get_object(Bucket=bucket, Key=key)
                existing, etag = pl.read_parquet(io.BytesIO(obj["Body"].read())), obj["ETag"]
            except client.exceptions.NoSuchKey:  # deleted between list and get
                pass
        parts = [p for p in ([existing, df] if incoming_wins else [df, existing]) if not p.is_empty()]
        merged = _sort_prices(dedupe_prices(pl.concat(parts, how="diagonal_relaxed"))) if parts else df
        buf = io.BytesIO()
        merged.write_parquet(buf, compression="zstd")
        cond = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=buf.getvalue(),
                ContentType="application/vnd.apache.parquet",
                **cond,
            )
            return merged.height
        except client.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] not in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise
    raise RuntimeError(f"{key} kept changing; gave up after {UPSERT_ATTEMPTS} attempts")


def upsert_prices(df: pl.DataFrame, bucket: str | None = None, *, incoming_wins: bool = True) -> list[str]:
    """Upsert daily price rows (ticker, date, ...) into their ticker/year partitions; returns the keys written."""
    if df.is_empty():
        return []
    if df.schema["date"] == pl.Utf8:
        df = df.with_columns(pl.col("date").str.to_date())
    keys = []
    for (ticker, year), part in (
        df.with_columns(pl.col("date").dt.year().alias("_year")).partition_by(["ticker", "_year"], as_dict=True).items()
    ):
        key = price_partition_key(str(ticker), int(year))
        upsert_parquet(part.drop("_year"), key, bucket, incoming_wins=incoming_wins)
        keys.append(key)
    return keys


def read_prices(ticker: str | None = None, bucket: str | None = None) -> pl.DataFrame:
    """Daily prices for one ticker (or all), deduped and sorted by (ticker, date).

    Reads yearly partitions plus any legacy per-day / per-batch objects not yet compacted.
    S3 lists keys in lexicographic order (batch_start= < date= < year=), so yearly rows come
    last and win ties within the same source.
    """
    prefix = f"{PRICES_PREFIX}ticker={ticker}/" if ticker else PRICES_PREFIX
    return _sort_prices(dedupe_prices(read_parquet_prefix(prefix, bucket)))
