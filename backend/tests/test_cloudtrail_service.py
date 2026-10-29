"""Unit tests for CloudTrailService."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.services.cloudtrail_service import (
    CloudTrailError,
    CloudTrailService,
    _BEDROCK_EVENT_SOURCE,
)


def _make_ct_event(
    event_name: str = "InvokeModel",
    user_arn: str = "arn:aws:iam::123456789012:user/johndoe",
    model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    user_agent: str = "kiro/1.0",
    source_ip: str = "10.0.0.1",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    event_time: str = "2024-01-15T10:30:00Z",
    event_id: str = "abc-123",
) -> dict:
    """Build a CloudTrail event dict for testing."""
    usage = {"inputTokens": input_tokens, "outputTokens": output_tokens}
    if cache_read_tokens is not None:
        usage["cacheReadInputTokens"] = cache_read_tokens
    if cache_write_tokens is not None:
        usage["cacheWriteInputTokens"] = cache_write_tokens

    ct_event = {
        "eventName": event_name,
        "eventTime": event_time,
        "eventID": event_id,
        "eventSource": _BEDROCK_EVENT_SOURCE,
        "userIdentity": {"arn": user_arn},
        "requestParameters": {"modelId": model_id},
        "responseElements": {"usage": usage},
        "sourceIPAddress": source_ip,
        "userAgent": user_agent,
    }
    return {"CloudTrailEvent": json.dumps(ct_event), "EventTime": event_time}


@patch("app.services.cloudtrail_service.boto3.Session")
class TestParseEvent:
    """Tests for _parse_event and field extraction."""

    def test_parses_valid_invoke_model_event(self, mock_session):
        svc = CloudTrailService()
        event = _make_ct_event()
        result = svc._parse_event(event)

        assert result is not None
        assert result["id"] == "abc-123"
        assert result["userIdentity"] == "arn:aws:iam::123456789012:user/johndoe"
        assert result["userName"] == "johndoe"
        assert result["modelId"] == "anthropic.claude-sonnet-4-20250514-v1:0"
        assert result["sourceApplication"] == "Kiro"
        assert result["sourceIp"] == "10.0.0.1"
        assert result["inputTokens"] == 100
        assert result["outputTokens"] == 50
        assert result["cacheReadTokens"] == 0
        assert result["cacheWriteTokens"] == 0
        assert result["totalTokens"] == 150
        assert result["costEstimate"] is None

    def test_parses_streaming_event(self, mock_session):
        svc = CloudTrailService()
        event = _make_ct_event(event_name="InvokeModelWithResponseStream")
        result = svc._parse_event(event)
        assert result is not None

    def test_skips_non_bedrock_event(self, mock_session):
        svc = CloudTrailService()
        event = _make_ct_event(event_name="DescribeTrails")
        result = svc._parse_event(event)
        assert result is None

    def test_cache_tokens_default_to_zero(self, mock_session):
        svc = CloudTrailService()
        event = _make_ct_event()  # no cache tokens
        result = svc._parse_event(event)
        assert result["cacheReadTokens"] == 0
        assert result["cacheWriteTokens"] == 0

    def test_cache_tokens_extracted_when_present(self, mock_session):
        svc = CloudTrailService()
        event = _make_ct_event(cache_read_tokens=20, cache_write_tokens=10)
        result = svc._parse_event(event)
        assert result["cacheReadTokens"] == 20
        assert result["cacheWriteTokens"] == 10
        assert result["totalTokens"] == 100 + 50 + 20 + 10

    def test_handles_invalid_json(self, mock_session):
        svc = CloudTrailService()
        event = {"CloudTrailEvent": "not-json"}
        result = svc._parse_event(event)
        assert result is None

    def test_handles_missing_cloudtrail_event_field(self, mock_session):
        svc = CloudTrailService()
        event = {}
        result = svc._parse_event(event)
        # Should parse the default "{}" and skip since eventName won't match
        assert result is None


@patch("app.services.cloudtrail_service.boto3.Session")
class TestIdentifySourceApplication:
    """Tests for source application detection from user-agent."""

    def test_kiro_detected(self, mock_session):
        assert CloudTrailService._identify_source_application("kiro/1.0") == "Kiro"

    def test_kiro_case_insensitive(self, mock_session):
        assert CloudTrailService._identify_source_application("KIRO-Agent/2.0") == "Kiro"

    def test_q_developer_detected(self, mock_session):
        assert CloudTrailService._identify_source_application("aws-q-developer/3.1") == "Q Developer"

    def test_unknown_user_agent(self, mock_session):
        assert CloudTrailService._identify_source_application("some-other-tool/1.0") == "Unknown"

    def test_empty_user_agent(self, mock_session):
        assert CloudTrailService._identify_source_application("") == "Unknown"

    def test_none_user_agent(self, mock_session):
        assert CloudTrailService._identify_source_application(None) == "Unknown"


@patch("app.services.cloudtrail_service.boto3.Session")
class TestRetryLogic:
    """Tests for exponential backoff retry on CloudTrail API errors."""

    @patch("app.services.cloudtrail_service.time.sleep")
    def test_retries_on_client_error_then_succeeds(self, mock_sleep, mock_session):
        svc = CloudTrailService()
        error_response = {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}
        svc.client.lookup_events = MagicMock(
            side_effect=[
                ClientError(error_response, "LookupEvents"),
                {"Events": [], "NextToken": None},
            ]
        )
        result = svc._call_with_retry(
            "us-west-2", {"LookupAttributes": [], "StartTime": None, "EndTime": None}
        )
        assert result == {"Events": [], "NextToken": None}
        assert mock_sleep.call_count == 1

    @patch("app.services.cloudtrail_service.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep, mock_session):
        svc = CloudTrailService()
        error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}}
        svc.client.lookup_events = MagicMock(
            side_effect=ClientError(error_response, "LookupEvents")
        )
        with pytest.raises(CloudTrailError) as exc_info:
            svc._call_with_retry(
                "us-west-2", {"LookupAttributes": [], "StartTime": None, "EndTime": None}
            )
        assert exc_info.value.error_code == "AccessDeniedException"
        assert "Not authorized" in exc_info.value.message
        # 3 attempts, 2 sleeps (no sleep after last attempt)
        assert mock_sleep.call_count == 2

    @patch("app.services.cloudtrail_service.time.sleep")
    def test_exponential_backoff_timing(self, mock_sleep, mock_session):
        svc = CloudTrailService()
        error_response = {"Error": {"Code": "ThrottlingException", "Message": "Slow down"}}
        svc.client.lookup_events = MagicMock(
            side_effect=ClientError(error_response, "LookupEvents")
        )
        with pytest.raises(CloudTrailError):
            svc._call_with_retry("us-west-2", {"LookupAttributes": []})
        # Backoff: 1s, 2s (no sleep after 3rd attempt)
        calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert calls == [1.0, 2.0]


@patch("app.services.cloudtrail_service.boto3.Session")
class TestGetActivityRecords:
    """Tests for the full get_activity_records flow with pagination."""

    def test_returns_parsed_records(self, mock_session):
        svc = CloudTrailService()
        # CloudTrail is only a fallback in the hybrid pipeline; stub the
        # CloudWatch + S3 phases to empty so the fallback path is exercised.
        svc._query_cloudwatch_logs = MagicMock(return_value=([], False))
        svc._query_s3_logs = MagicMock(return_value=([], False))
        svc.client.lookup_events = MagicMock(
            return_value={"Events": [_make_ct_event()], "NextToken": None}
        )
        records = svc.get_activity_records(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc),
        )
        assert len(records) == 1
        assert records[0]["userName"] == "johndoe"

    def test_handles_pagination(self, mock_session):
        svc = CloudTrailService()
        svc._query_cloudwatch_logs = MagicMock(return_value=([], False))
        svc._query_s3_logs = MagicMock(return_value=([], False))
        svc.client.lookup_events = MagicMock(
            side_effect=[
                {"Events": [_make_ct_event(event_id="e1")], "NextToken": "tok1"},
                {"Events": [_make_ct_event(event_id="e2")], "NextToken": None},
            ]
        )
        records = svc.get_activity_records(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc),
        )
        assert len(records) == 2
        assert svc.client.lookup_events.call_count == 2

    def test_returns_empty_list_when_no_events(self, mock_session):
        svc = CloudTrailService()
        svc._query_cloudwatch_logs = MagicMock(return_value=([], False))
        svc._query_s3_logs = MagicMock(return_value=([], False))
        svc.client.lookup_events = MagicMock(
            return_value={"Events": [], "NextToken": None}
        )
        records = svc.get_activity_records(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc),
        )
        assert records == []


@patch("app.services.cloudtrail_service.boto3.Session")
class TestExtractUserName:
    """Tests for user name extraction from ARN."""

    def test_iam_user_arn(self, mock_session):
        assert CloudTrailService._extract_user_name(
            "arn:aws:iam::123456789012:user/johndoe"
        ) == "johndoe"

    def test_assumed_role_arn(self, mock_session):
        assert CloudTrailService._extract_user_name(
            "arn:aws:sts::123456789012:assumed-role/MyRole/session-name"
        ) == "session-name"

    def test_plain_string(self, mock_session):
        assert CloudTrailService._extract_user_name("someuser") == "someuser"


@patch("app.services.cloudtrail_service.boto3.Session")
class TestIngestionErrorFlag:
    """Tests for the hard-failure ``ingestion_error`` flag from _fetch_activity_records."""

    def test_flag_true_when_all_attempted_sources_error(self, mock_session):
        svc = CloudTrailService()
        # Recent (<48h) window so only CloudWatch + CloudTrail fallback are attempted.
        start = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)

        svc._query_cloudwatch_logs = MagicMock(side_effect=RuntimeError("cw down"))
        svc._query_cloudtrail = MagicMock(side_effect=RuntimeError("ct down"))

        records, truncated, ingestion_error = svc._fetch_activity_records(start, end, ["us-west-2"])

        assert records == []
        assert ingestion_error is True

    def test_flag_false_when_records_returned(self, mock_session):
        svc = CloudTrailService()
        start = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)

        rec = {"id": "r1", "timestamp": "2024-01-10T12:00:00+00:00"}
        svc._query_cloudwatch_logs = MagicMock(return_value=([rec], False))
        # CloudTrail should not even be attempted since CW returned records.
        svc._query_cloudtrail = MagicMock(side_effect=AssertionError("should not run"))

        records, truncated, ingestion_error = svc._fetch_activity_records(start, end, ["us-west-2"])

        assert len(records) == 1
        assert ingestion_error is False

    def test_flag_false_on_genuine_empty(self, mock_session):
        svc = CloudTrailService()
        start = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)

        # Every source succeeds but there is simply no activity.
        svc._query_cloudwatch_logs = MagicMock(return_value=([], False))
        svc._query_cloudtrail = MagicMock(return_value=[])

        records, truncated, ingestion_error = svc._fetch_activity_records(start, end, ["us-west-2"])

        assert records == []
        assert ingestion_error is False

    def test_flag_false_on_partial_failure(self, mock_session):
        svc = CloudTrailService()
        # Wide (>48h) window so S3 is also attempted.
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, tzinfo=timezone.utc)

        rec = {"id": "s1", "timestamp": "2024-01-05T12:00:00+00:00"}
        svc._query_cloudwatch_logs = MagicMock(side_effect=RuntimeError("cw down"))
        svc._query_s3_logs = MagicMock(return_value=([rec], False))
        svc._query_cloudtrail = MagicMock(side_effect=AssertionError("should not run"))

        records, truncated, ingestion_error = svc._fetch_activity_records(start, end, ["us-west-2"])

        assert len(records) == 1
        assert ingestion_error is False


# ---------------------------------------------------------------------------
# CloudWatch / S3 ModelInvocationLog parsing: usage-shape tolerance + stable id
# ---------------------------------------------------------------------------


def _make_cw_event(
    *,
    usage: dict | None = None,
    input_token_count: int = 0,
    output_token_count: int = 0,
    model_id: str = "amazon.nova-pro-v1:0",
    user_arn: str = "arn:aws:iam::123456789012:user/janedoe",
    timestamp: str = "2024-02-01T09:00:00Z",
    request_id: str | None = "req-abc",
) -> dict:
    """Build a CloudWatch/S3 ModelInvocationLog event for testing.

    ``usage`` is placed under ``output.outputBodyJson.usage`` (matching the real
    log shape). When ``request_id`` is None the field is omitted so the stable
    content-hash fallback is exercised.
    """
    log: dict = {
        "schemaType": "ModelInvocationLog",
        "modelId": model_id,
        "timestamp": timestamp,
        "identity": {"arn": user_arn},
        "input": {"inputTokenCount": input_token_count, "inputBodyJson": {}},
        "output": {"outputTokenCount": output_token_count, "outputBodyJson": {}},
    }
    if request_id is not None:
        log["requestId"] = request_id
    if usage is not None:
        log["output"]["outputBodyJson"]["usage"] = usage
    return {"message": json.dumps(log), "timestamp": 0}


@patch("app.services.cloudtrail_service.boto3.Session")
class TestParseCloudWatchUsageVariants:
    """Usage-token extraction is tolerant of multiple model-family key shapes."""

    def test_anthropic_style_usage(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(
            usage={
                "input_tokens": 120,
                "output_tokens": 34,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
            }
        )
        result = svc._parse_cloudwatch_event(event)
        assert result["inputTokens"] == 120
        assert result["outputTokens"] == 34
        assert result["cacheReadTokens"] == 10
        assert result["cacheWriteTokens"] == 5

    def test_converse_api_style_usage(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(
            usage={
                "inputTokens": 200,
                "outputTokens": 80,
                "cacheReadInputTokens": 40,
                "cacheWriteInputTokens": 12,
            }
        )
        result = svc._parse_cloudwatch_event(event)
        assert result["inputTokens"] == 200
        assert result["outputTokens"] == 80
        assert result["cacheReadTokens"] == 40
        assert result["cacheWriteTokens"] == 12

    def test_nova_titan_camel_count_usage(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(
            usage={"inputTokenCount": 300, "outputTokenCount": 90}
        )
        result = svc._parse_cloudwatch_event(event)
        assert result["inputTokens"] == 300
        assert result["outputTokens"] == 90
        assert result["cacheReadTokens"] == 0
        assert result["cacheWriteTokens"] == 0

    def test_llama_snake_count_usage(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(
            usage={"input_token_count": 150, "output_token_count": 45}
        )
        result = svc._parse_cloudwatch_event(event)
        assert result["inputTokens"] == 150
        assert result["outputTokens"] == 45

    def test_falls_back_to_section_counts_when_usage_absent(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(input_token_count=77, output_token_count=11, usage=None)
        result = svc._parse_cloudwatch_event(event)
        assert result["inputTokens"] == 77
        assert result["outputTokens"] == 11

    def test_anthropic_path_still_works_end_to_end(self, mock_session):
        """Regression: the original Anthropic extraction is unchanged."""
        svc = CloudTrailService()
        event = _make_cw_event(
            usage={
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 4,
            }
        )
        result = svc._parse_cloudwatch_event(event)
        assert result["totalTokens"] == 1 + 2 + 3 + 4


@patch("app.services.cloudtrail_service.boto3.Session")
class TestStableRecordId:
    """Records missing requestId get a stable, content-derived, dedup-safe id."""

    def test_real_request_id_preferred_when_present(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(request_id="req-xyz")
        result = svc._parse_cloudwatch_event(event)
        assert result["id"] == "req-xyz"

    def test_same_event_yields_same_id(self, mock_session):
        svc = CloudTrailService()
        e1 = _make_cw_event(request_id=None, usage={"input_tokens": 10, "output_tokens": 5})
        e2 = _make_cw_event(request_id=None, usage={"input_tokens": 10, "output_tokens": 5})
        r1 = svc._parse_cloudwatch_event(e1)
        r2 = svc._parse_cloudwatch_event(e2)
        assert r1["id"] == r2["id"]

    def test_fallback_id_is_deterministic_not_random(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(request_id=None)
        first = svc._parse_cloudwatch_event(event)["id"]
        # Parsing an identical event again must not produce a random uuid.
        second = svc._parse_cloudwatch_event(_make_cw_event(request_id=None))["id"]
        assert first == second
        # sha256 hexdigest is 64 hex chars (not a uuid4 with dashes).
        assert len(first) == 64
        assert "-" not in first

    def test_different_content_yields_different_id(self, mock_session):
        svc = CloudTrailService()
        r1 = svc._parse_cloudwatch_event(
            _make_cw_event(request_id=None, usage={"input_tokens": 10, "output_tokens": 5})
        )
        r2 = svc._parse_cloudwatch_event(
            _make_cw_event(request_id=None, usage={"input_tokens": 99, "output_tokens": 5})
        )
        assert r1["id"] != r2["id"]

    def test_dedup_across_cloudwatch_and_s3(self, mock_session):
        """Same invocation seen via CloudWatch AND S3 (no requestId) dedups to one."""
        svc = CloudTrailService()
        cw_event = _make_cw_event(request_id=None, usage={"input_tokens": 10, "output_tokens": 5})
        cw_record = svc._parse_cloudwatch_event(cw_event)
        # S3 path parses the same underlying log via _parse_s3_log_entry.
        s3_log = json.loads(cw_event["message"])
        s3_record = svc._parse_s3_log_entry(s3_log)

        assert cw_record["id"] == s3_record["id"]

        # Drive both through the dedup phase of _fetch_activity_records. Use a
        # >48h window so the S3 phase (phase 2) is actually attempted.
        start = datetime(2024, 2, 1, tzinfo=timezone.utc)
        end = datetime(2024, 2, 5, tzinfo=timezone.utc)
        svc._query_cloudwatch_logs = MagicMock(return_value=([cw_record], False))
        svc._query_s3_logs = MagicMock(return_value=([s3_record], False))
        svc._query_cloudtrail = MagicMock(return_value=[])

        records, _, _ = svc._fetch_activity_records(start, end, ["us-west-2"])
        assert len(records) == 1


# ---------------------------------------------------------------------------
# Cross-region inference prefix normalization (matrix/model de-fragmentation)
# ---------------------------------------------------------------------------


class TestStripRegionPrefix:
    """The shared _strip_region_prefix helper strips only cross-region prefixes."""

    def test_strips_us_prefix(self):
        from app.services.cloudtrail_service import _strip_region_prefix

        assert _strip_region_prefix("us.amazon.nova-pro-v1:0") == "amazon.nova-pro-v1:0"

    def test_strips_eu_ap_global_prefixes(self):
        from app.services.cloudtrail_service import _strip_region_prefix

        assert _strip_region_prefix("eu.anthropic.claude-3-5-sonnet-v1:0") == (
            "anthropic.claude-3-5-sonnet-v1:0"
        )
        assert _strip_region_prefix("ap.amazon.nova-lite-v1:0") == "amazon.nova-lite-v1:0"
        assert _strip_region_prefix("global.meta.llama3-3-70b-instruct-v1:0") == (
            "meta.llama3-3-70b-instruct-v1:0"
        )

    def test_leaves_non_prefixed_unchanged(self):
        from app.services.cloudtrail_service import _strip_region_prefix

        assert _strip_region_prefix("amazon.nova-pro-v1:0") == "amazon.nova-pro-v1:0"
        # A provider segment that merely starts with 'a'/'e'/etc must not match.
        assert _strip_region_prefix("anthropic.claude-3-haiku") == "anthropic.claude-3-haiku"

    def test_handles_empty(self):
        from app.services.cloudtrail_service import _strip_region_prefix

        assert _strip_region_prefix("") == ""


@patch("app.services.cloudtrail_service.boto3.Session")
class TestModelIdNormalization:
    """Real prefixed model IDs collapse onto the bare form used by pricing/demo."""

    def test_cloudwatch_strips_region_prefix(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(model_id="us.amazon.nova-pro-v1:0")
        result = svc._parse_cloudwatch_event(event)
        assert result["modelId"] == "amazon.nova-pro-v1:0"

    def test_cloudwatch_strips_prefix_after_inference_profile_arn(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(
            model_id=(
                "arn:aws:bedrock:us-west-2:123456789012:inference-profile/"
                "us.amazon.nova-pro-v1:0"
            )
        )
        result = svc._parse_cloudwatch_event(event)
        assert result["modelId"] == "amazon.nova-pro-v1:0"

    def test_cloudwatch_leaves_bare_model_id_unchanged(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(model_id="amazon.nova-pro-v1:0")
        result = svc._parse_cloudwatch_event(event)
        assert result["modelId"] == "amazon.nova-pro-v1:0"

    def test_cloudtrail_strips_region_prefix(self, mock_session):
        svc = CloudTrailService()
        event = _make_ct_event(model_id="us.amazon.nova-pro-v1:0")
        result = svc._parse_event(event)
        assert result["modelId"] == "amazon.nova-pro-v1:0"

    def test_cloudtrail_leaves_bare_model_id_unchanged(self, mock_session):
        svc = CloudTrailService()
        event = _make_ct_event(model_id="anthropic.claude-sonnet-4-20250514-v1:0")
        result = svc._parse_event(event)
        assert result["modelId"] == "anthropic.claude-sonnet-4-20250514-v1:0"


# ---------------------------------------------------------------------------
# Inference-profile ARN capture (Req 4.1) — additive, lossless for modelId
# ---------------------------------------------------------------------------


class TestExtractInferenceProfileArn:
    """The _extract_inference_profile_arn helper is total and precise."""

    def test_matches_plain_inference_profile_arn(self):
        from app.services.cloudtrail_service import _extract_inference_profile_arn

        arn = "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.amazon.nova-pro-v1:0"
        assert _extract_inference_profile_arn(arn) == arn

    def test_matches_application_inference_profile_arn(self):
        from app.services.cloudtrail_service import _extract_inference_profile_arn

        arn = "arn:aws:bedrock:us-west-2:123456789012:application-inference-profile/abcd1234"
        assert _extract_inference_profile_arn(arn) == arn

    def test_bare_model_id_returns_none(self):
        from app.services.cloudtrail_service import _extract_inference_profile_arn

        assert _extract_inference_profile_arn("amazon.nova-pro-v1:0") is None

    def test_cross_region_prefixed_model_id_returns_none(self):
        from app.services.cloudtrail_service import _extract_inference_profile_arn

        assert _extract_inference_profile_arn("us.amazon.nova-pro-v1:0") is None

    def test_non_profile_bedrock_arn_returns_none(self):
        from app.services.cloudtrail_service import _extract_inference_profile_arn

        # A foundation-model ARN is not an inference-profile ARN.
        fm = "arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-pro-v1:0"
        assert _extract_inference_profile_arn(fm) is None

    def test_empty_or_non_string_returns_none(self):
        from app.services.cloudtrail_service import _extract_inference_profile_arn

        assert _extract_inference_profile_arn("") is None
        assert _extract_inference_profile_arn(None) is None


@patch("app.services.cloudtrail_service.boto3.Session")
class TestInferenceProfileArnCapture:
    """inferenceProfileArn is non-null exactly when the raw ref is a profile ARN.

    The bare modelId used for pricing/aggregation must be unchanged (Property 14).
    """

    def test_cloudwatch_captures_profile_arn_and_keeps_bare_model_id(self, mock_session):
        svc = CloudTrailService()
        arn = "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.amazon.nova-pro-v1:0"
        result = svc._parse_cloudwatch_event(_make_cw_event(model_id=arn))
        assert result["inferenceProfileArn"] == arn
        # Bare modelId is unchanged vs. today's normalization.
        assert result["modelId"] == "amazon.nova-pro-v1:0"

    def test_cloudwatch_application_profile_arn(self, mock_session):
        svc = CloudTrailService()
        arn = "arn:aws:bedrock:us-west-2:123456789012:application-inference-profile/xyz789"
        result = svc._parse_cloudwatch_event(_make_cw_event(model_id=arn))
        assert result["inferenceProfileArn"] == arn
        # No cross-region prefix on the tail, so bare id is the tail itself.
        assert result["modelId"] == "xyz789"

    def test_cloudwatch_bare_model_id_has_null_arn(self, mock_session):
        svc = CloudTrailService()
        result = svc._parse_cloudwatch_event(_make_cw_event(model_id="amazon.nova-pro-v1:0"))
        assert result["inferenceProfileArn"] is None
        assert result["modelId"] == "amazon.nova-pro-v1:0"

    def test_cloudwatch_cross_region_prefixed_id_has_null_arn(self, mock_session):
        svc = CloudTrailService()
        result = svc._parse_cloudwatch_event(_make_cw_event(model_id="us.amazon.nova-pro-v1:0"))
        assert result["inferenceProfileArn"] is None
        assert result["modelId"] == "amazon.nova-pro-v1:0"

    def test_cloudtrail_captures_profile_arn_and_keeps_bare_model_id(self, mock_session):
        svc = CloudTrailService()
        arn = "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.amazon.nova-pro-v1:0"
        result = svc._parse_event(_make_ct_event(model_id=arn))
        assert result["inferenceProfileArn"] == arn
        assert result["modelId"] == "amazon.nova-pro-v1:0"

    def test_cloudtrail_bare_model_id_has_null_arn(self, mock_session):
        svc = CloudTrailService()
        result = svc._parse_event(
            _make_ct_event(model_id="anthropic.claude-sonnet-4-20250514-v1:0")
        )
        assert result["inferenceProfileArn"] is None
        assert result["modelId"] == "anthropic.claude-sonnet-4-20250514-v1:0"


# ---------------------------------------------------------------------------
# Multi-region ingestion fan-out (F1)
# ---------------------------------------------------------------------------


@patch("app.services.cloudtrail_service.boto3.Session")
class TestMultiRegionFanOut:
    """_fetch_activity_records fans out across configured regions and merges."""

    def test_single_region_calls_pipeline_once(self, mock_session):
        svc = CloudTrailService()
        start = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, 13, tzinfo=timezone.utc)

        svc._fetch_region_activity_records = MagicMock(
            return_value=([{"id": "r1", "region": "us-west-2"}], False, False)
        )
        records, truncated, ingestion_error = svc._fetch_activity_records(start, end, ["us-west-2"])

        svc._fetch_region_activity_records.assert_called_once_with("us-west-2", start, end)
        assert len(records) == 1
        assert ingestion_error is False

    def test_two_regions_merge_records_from_both(self, mock_session):
        svc = CloudTrailService()
        start = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, 13, tzinfo=timezone.utc)

        def fake_fetch(region, s, e):
            return ([{"id": f"r-{region}", "region": region}], False, False)

        svc._fetch_region_activity_records = MagicMock(side_effect=fake_fetch)
        records, truncated, ingestion_error = svc._fetch_activity_records(
            start, end, ["us-west-2", "us-east-1"]
        )

        ids = {r["id"] for r in records}
        assert ids == {"r-us-west-2", "r-us-east-1"}
        assert ingestion_error is False

    def test_one_region_failing_does_not_lose_the_other(self, mock_session):
        svc = CloudTrailService()
        start = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, 13, tzinfo=timezone.utc)

        def fake_fetch(region, s, e):
            if region == "us-east-1":
                return ([], False, True)  # hard failure in this region
            return ([{"id": "r-good", "region": region}], False, False)

        svc._fetch_region_activity_records = MagicMock(side_effect=fake_fetch)
        records, truncated, ingestion_error = svc._fetch_activity_records(
            start, end, ["us-west-2", "us-east-1"]
        )

        assert len(records) == 1
        # Not every region failed, so overall ingestion_error is False.
        assert ingestion_error is False

    def test_ingestion_error_true_only_when_every_region_fails(self, mock_session):
        svc = CloudTrailService()
        start = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, 13, tzinfo=timezone.utc)

        svc._fetch_region_activity_records = MagicMock(return_value=([], False, True))
        records, truncated, ingestion_error = svc._fetch_activity_records(
            start, end, ["us-west-2", "us-east-1"]
        )

        assert records == []
        assert ingestion_error is True

    def test_dedup_across_regions_by_id(self, mock_session):
        """Same record id observed from two regions (shouldn't normally happen,
        but the dedup is id-based and must not double-count if it does)."""
        svc = CloudTrailService()
        start = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, 13, tzinfo=timezone.utc)

        svc._fetch_region_activity_records = MagicMock(
            return_value=([{"id": "shared-id", "region": "x"}], False, False)
        )
        records, _, _ = svc._fetch_activity_records(start, end, ["us-west-2", "us-east-1"])
        assert len(records) == 1


class TestConfiguredRegions:
    """configured_regions() parses CLOUDTRAIL_REGIONS with a sane fallback."""

    @patch("app.services.cloudtrail_service.boto3.Session")
    def test_falls_back_to_aws_region_when_unset(self, mock_session):
        with patch("app.services.cloudtrail_service.settings") as mock_settings:
            mock_settings.aws_region = "us-west-2"
            mock_settings.cloudtrail_regions = ""
            mock_settings.aws_access_key_id = None
            mock_settings.aws_secret_access_key = None
            mock_settings.aws_session_token = None
            mock_settings.bedrock_log_group = "/aws/bedrock/model-invocations"
            mock_settings.bedrock_s3_log_bucket = "test-bucket"
            mock_settings.bedrock_s3_log_key_prefix = "bedrock-logs/us-west-2"
            svc = CloudTrailService()
            assert svc.configured_regions() == ["us-west-2"]

    @patch("app.services.cloudtrail_service.boto3.Session")
    def test_parses_multi_region_list(self, mock_session):
        with patch("app.services.cloudtrail_service.settings") as mock_settings:
            mock_settings.aws_region = "us-west-2"
            mock_settings.cloudtrail_regions = "us-west-2, us-east-1"
            mock_settings.aws_access_key_id = None
            mock_settings.aws_secret_access_key = None
            mock_settings.aws_session_token = None
            mock_settings.bedrock_log_group = "/aws/bedrock/model-invocations"
            mock_settings.bedrock_s3_log_bucket = "test-bucket"
            mock_settings.bedrock_s3_log_key_prefix = "bedrock-logs/us-west-2"
            svc = CloudTrailService()
            assert svc.configured_regions() == ["us-west-2", "us-east-1"]


@patch("app.services.cloudtrail_service.boto3.Session")
class TestRecordRegionTagging:
    """Every parsed record carries the region it was fetched from."""

    def test_cloudwatch_event_tagged_with_passed_region(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event()
        result = svc._parse_cloudwatch_event(event, "us-east-1")
        assert result["region"] == "us-east-1"

    def test_cloudwatch_event_defaults_region_when_omitted(self, mock_session):
        from app.config import settings as app_settings

        svc = CloudTrailService()
        event = _make_cw_event()
        result = svc._parse_cloudwatch_event(event)
        assert result["region"] == app_settings.aws_region

    def test_cloudtrail_event_tagged_with_passed_region(self, mock_session):
        svc = CloudTrailService()
        event = _make_ct_event()
        result = svc._parse_event(event, "us-east-1")
        assert result["region"] == "us-east-1"

    def test_cross_region_inference_flag_true_for_prefixed_model(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(model_id="us.amazon.nova-pro-v1:0")
        result = svc._parse_cloudwatch_event(event, "us-west-2")
        assert result["usedCrossRegionInference"] is True

    def test_cross_region_inference_flag_false_for_bare_model(self, mock_session):
        svc = CloudTrailService()
        event = _make_cw_event(model_id="amazon.nova-pro-v1:0")
        result = svc._parse_cloudwatch_event(event, "us-west-2")
        assert result["usedCrossRegionInference"] is False
