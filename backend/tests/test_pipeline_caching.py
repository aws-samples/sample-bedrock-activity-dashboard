"""Tests for raw-ingestion caching, stable pagination, and S3 newest-first + truncation.

These exercise the HIGH-severity backend pipeline fixes in isolation (mocking the
AWS clients / internal fetch) so they run without the full-app TestClient, which
cannot boot in this sandbox due to an outdated botocore lacking the
'bedrock-agentcore' service model.
"""

import gzip
import io
import json
import random
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services.cloudtrail_service import (
    CloudTrailService,
    _activity_cache_key,
    clear_activity_cache,
)
from app.services.filter_service import ActivityFilters, FilterService


UTC = timezone.utc


def _raw(user_identity: str, rid: str, ts: str) -> dict:
    """Build a raw (unfiltered) activity record."""
    return {
        "id": rid,
        "timestamp": ts,
        "userIdentity": user_identity,
        "userName": user_identity.rsplit("/", 1)[-1],
        "modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "sourceApplication": "Kiro",
        "sourceIp": "10.0.0.1",
        "inputTokens": 1,
        "outputTokens": 1,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 2,
        "costEstimate": None,
    }


def _cw_event(rid: str, user_arn: str, ts: str) -> dict:
    """A CloudWatch Logs ModelInvocationLog event as filter_log_events returns it."""
    log = {
        "schemaType": "ModelInvocationLog",
        "timestamp": ts,
        "modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "requestId": rid,
        "identity": {"arn": user_arn},
        "input": {"inputTokenCount": 10},
        "output": {"outputTokenCount": 5},
    }
    return {"message": json.dumps(log), "timestamp": 0}


# ---------------------------------------------------------------------------
# FIX #3: caching
# ---------------------------------------------------------------------------


@patch("app.services.cloudtrail_service.boto3.Session")
def test_cache_hit_avoids_second_aws_client_call(mock_session):
    """A repeat call for the same window is served from cache (no 2nd AWS call)."""
    clear_activity_cache()
    svc = CloudTrailService()

    # Window fully inside 48h so only CloudWatch is queried (S3 phase skipped).
    start = datetime(2024, 1, 15, 10, tzinfo=UTC)
    end = datetime(2024, 1, 15, 12, tzinfo=UTC)

    svc.logs_client.filter_log_events = MagicMock(
        return_value={
            "events": [_cw_event("id-1", "arn:aws:iam::1:user/alice", "2024-01-15T11:00:00+00:00")],
            "nextToken": None,
        }
    )
    # Ensure S3/CloudTrail are never needed for this window.
    svc._query_s3_logs = MagicMock(return_value=([], False))

    r1 = svc.get_activity_records(start, end)
    r2 = svc.get_activity_records(start, end)

    assert len(r1) == 1 and len(r2) == 1
    # The expensive AWS client call happened exactly once across both requests.
    assert svc.logs_client.filter_log_events.call_count == 1


@patch("app.services.cloudtrail_service.boto3.Session")
def test_cache_key_is_user_agnostic_and_scoping_still_differs(mock_session):
    """One shared cache entry serves two different callers, yet per-request
    scoping still yields each caller's own disjoint subset."""
    clear_activity_cache()
    svc = CloudTrailService()

    ts = "2024-01-15T11:00:00+00:00"
    raw = [
        _raw("alice@example.com", "id-a", ts),
        _raw("bob@example.com", "id-b", ts),
    ]
    # Mock the expensive ingestion; assert it runs only once (cache reused).
    # _fetch_activity_records returns (records, truncated, ingestion_error).
    svc._fetch_activity_records = MagicMock(return_value=(list(raw), False, False))

    start = datetime(2024, 1, 15, 10, tzinfo=UTC)
    end = datetime(2024, 1, 15, 12, tzinfo=UTC)

    # Two separate requests (as two different users would trigger).
    rec_a, trunc_a = svc.get_activity_records_with_truncation(start, end)
    rec_b, trunc_b = svc.get_activity_records_with_truncation(start, end)

    # Ingestion happened once — the second request hit the cache.
    assert svc._fetch_activity_records.call_count == 1
    assert trunc_a is False and trunc_b is False

    # Both callers received the SAME raw, unfiltered set (cache is user-agnostic).
    assert {r["id"] for r in rec_a} == {"id-a", "id-b"}
    assert {r["id"] for r in rec_b} == {"id-a", "id-b"}

    # Per-request scoping (as _scope_to_user would set) yields disjoint subsets
    # from the same cached raw records — no cross-user leakage.
    filters_a = ActivityFilters(start_date=start, end_date=end, user_identities=["alice@example.com"])
    filters_b = ActivityFilters(start_date=start, end_date=end, user_identities=["bob@example.com"])
    scoped_a = FilterService.apply_filters(rec_a, filters_a)
    scoped_b = FilterService.apply_filters(rec_b, filters_b)

    assert {r["id"] for r in scoped_a} == {"id-a"}
    assert {r["id"] for r in scoped_b} == {"id-b"}


def test_cache_key_ignores_identity_only_depends_on_rounded_window():
    """The cache key is derived SOLELY from the coarse (start,end) hour bucket."""
    # Two requests whose exact times differ within the same hour buckets collapse
    # onto the same key.
    s1 = datetime(2024, 1, 15, 10, 5, 30, tzinfo=UTC)
    e1 = datetime(2024, 1, 15, 12, 45, 0, tzinfo=UTC)
    s2 = datetime(2024, 1, 15, 10, 59, 0, tzinfo=UTC)
    e2 = datetime(2024, 1, 15, 12, 1, 0, tzinfo=UTC)

    regions = ("us-west-2",)
    _, _, key1 = _activity_cache_key(s1, e1, regions)
    _, _, key2 = _activity_cache_key(s2, e2, regions)
    assert key1 == key2  # start floored to 10:00, end ceiled to 13:00 for both

    # A different window produces a different key.
    _, _, key3 = _activity_cache_key(
        datetime(2024, 1, 16, 10, tzinfo=UTC), datetime(2024, 1, 16, 12, tzinfo=UTC), regions
    )
    assert key3 != key1

    # A different region SET produces a different key even for the same window.
    _, _, key4 = _activity_cache_key(s1, e1, ("us-west-2", "us-east-1"))
    assert key4 != key1


# ---------------------------------------------------------------------------
# FIX #4: stable pagination
# ---------------------------------------------------------------------------


def _stable_order(records):
    """Replicate the (timestamp, id) DESC ordering used by the prompts route."""
    s = list(records)
    s.sort(key=lambda r: (r.get("timestamp", ""), r.get("id", "")), reverse=True)
    return [r["id"] for r in s]


def test_stable_pagination_ordering_with_tied_timestamps():
    """Records with identical timestamps get a deterministic, input-order-independent
    total order, so page boundaries are stable across re-fetches."""
    ts = "2024-01-15T10:00:00+00:00"
    newer = "2024-01-16T00:00:00+00:00"
    records = [
        {"id": "c", "timestamp": ts},
        {"id": "a", "timestamp": ts},
        {"id": "b", "timestamp": ts},
        {"id": "z", "timestamp": newer},
    ]

    order1 = _stable_order(records)
    # Newest timestamp first; among tied timestamps, id descending.
    assert order1 == ["z", "c", "b", "a"]

    # Re-fetch in a different arrival order -> identical ordering.
    shuffled = list(records)
    random.seed(1234)
    random.shuffle(shuffled)
    order2 = _stable_order(shuffled)
    assert order1 == order2

    # Page boundaries (page_size=2) are identical across the two fetches.
    assert order1[:2] == order2[:2]
    assert order1[2:] == order2[2:]


# ---------------------------------------------------------------------------
# FIX #5: S3 newest-first + truncation flag
# ---------------------------------------------------------------------------


def _fake_s3(day_to_keys, key_to_log):
    """Build a mock S3 client whose paginator/get_object serve per-day gzipped logs."""
    def paginate(Bucket=None, Prefix=None):
        for day, keys in day_to_keys.items():
            if day in Prefix:
                return [{"Contents": [{"Key": k} for k in keys]}]
        return [{"Contents": []}]

    paginator = MagicMock()
    paginator.paginate.side_effect = paginate

    def get_object(Bucket=None, Key=None):
        raw = gzip.compress(json.dumps(key_to_log[Key]).encode())
        return {"Body": io.BytesIO(raw)}

    s3 = MagicMock()
    s3.get_paginator.return_value = paginator
    s3.get_object.side_effect = get_object
    return s3


def _s3_log(rid: str, ts: str) -> dict:
    return {
        "schemaType": "ModelInvocationLog",
        "timestamp": ts,
        "modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "requestId": rid,
        "identity": {"arn": "arn:aws:iam::1:user/alice"},
        "input": {"inputTokenCount": 1},
        "output": {"outputTokenCount": 1},
    }


@patch("app.services.cloudtrail_service.boto3.Session")
def test_s3_newest_first_and_truncation_flag_when_cap_hit(mock_session):
    """When the S3 cap is hit, the NEWEST days are retained and truncated=True."""
    svc = CloudTrailService()
    svc._MAX_S3_RECORDS = 2  # small cap to force truncation

    day_to_keys = {
        "2024/01/12": ["k12.json.gz"],
        "2024/01/11": ["k11.json.gz"],
        "2024/01/10": ["k10.json.gz"],
    }
    key_to_log = {
        "k12.json.gz": _s3_log("r12", "2024-01-12T08:00:00+00:00"),
        "k11.json.gz": _s3_log("r11", "2024-01-11T08:00:00+00:00"),
        "k10.json.gz": _s3_log("r10", "2024-01-10T08:00:00+00:00"),
    }
    svc.s3_client = _fake_s3(day_to_keys, key_to_log)

    start = datetime(2024, 1, 10, 0, tzinfo=UTC)
    end = datetime(2024, 1, 13, 0, tzinfo=UTC)

    records, truncated = svc._query_s3_logs("us-west-2", start, end)

    assert truncated is True
    ids = [r["id"] for r in records]
    # Cap = 2, scanning newest->oldest keeps the two most recent days (12, 11),
    # NOT the oldest (10); and the newest appears first.
    assert ids == ["r12", "r11"]
    assert "r10" not in ids


@patch("app.services.cloudtrail_service.boto3.Session")
def test_s3_not_truncated_when_under_cap(mock_session):
    """Under the cap, all records return newest-first and truncated=False."""
    svc = CloudTrailService()
    svc._MAX_S3_RECORDS = 100

    day_to_keys = {
        "2024/01/12": ["k12.json.gz"],
        "2024/01/11": ["k11.json.gz"],
        "2024/01/10": ["k10.json.gz"],
    }
    key_to_log = {
        "k12.json.gz": _s3_log("r12", "2024-01-12T08:00:00+00:00"),
        "k11.json.gz": _s3_log("r11", "2024-01-11T08:00:00+00:00"),
        "k10.json.gz": _s3_log("r10", "2024-01-10T08:00:00+00:00"),
    }
    svc.s3_client = _fake_s3(day_to_keys, key_to_log)

    start = datetime(2024, 1, 10, 0, tzinfo=UTC)
    end = datetime(2024, 1, 13, 0, tzinfo=UTC)

    records, truncated = svc._query_s3_logs("us-west-2", start, end)

    assert truncated is False
    assert [r["id"] for r in records] == ["r12", "r11", "r10"]  # newest-first


@patch("app.services.cloudtrail_service.boto3.Session")
def test_cloudwatch_truncation_flag_when_cap_hit(mock_session):
    """_query_cloudwatch_logs reports truncated=True when the CW cap is reached."""
    svc = CloudTrailService()
    svc._MAX_CW_RECORDS = 2

    rec = _raw("arn:aws:iam::1:user/alice", "cw-1", "2024-01-15T11:00:00+00:00")
    # Phase 1 alone returns >= cap records.
    svc._fetch_cw_page = MagicMock(return_value=[dict(rec), dict(rec)])

    start = datetime(2024, 1, 15, 10, tzinfo=UTC)
    end = datetime(2024, 1, 15, 12, tzinfo=UTC)

    records, truncated = svc._query_cloudwatch_logs("us-west-2", start, end)
    assert truncated is True
    assert len(records) >= svc._MAX_CW_RECORDS


@patch("app.services.cloudtrail_service.boto3.Session")
def test_truncation_propagates_through_fetch(mock_session):
    """A source-level truncation surfaces as the top-level truncated flag."""
    clear_activity_cache()
    svc = CloudTrailService()

    svc._query_cloudwatch_logs = MagicMock(return_value=([], False))
    svc._query_s3_logs = MagicMock(
        return_value=([_raw("arn:aws:iam::1:user/a", "s1", "2024-01-01T00:00:00+00:00")], True)
    )

    # Window wide enough that the S3 (historical) phase runs.
    start = datetime(2024, 1, 1, 0, tzinfo=UTC)
    end = datetime(2024, 1, 10, 0, tzinfo=UTC)

    records, truncated = svc.get_activity_records_with_truncation(start, end)
    assert truncated is True
    assert any(r["id"] == "s1" for r in records)
