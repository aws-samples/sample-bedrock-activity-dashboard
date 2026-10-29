"""Property-based tests for CloudTrailService."""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.cloudtrail_service import CloudTrailService

svc = CloudTrailService()


class TestCloudTrailEventParsing:
    """Property 1: CloudTrail Event Parsing Completeness.

    For any valid CloudTrail Bedrock event, parsing produces a record with
    correct fields. Missing cache tokens default to 0.
    """
    # Feature: bedrock-activity-dashboard, Property 1: CloudTrail Event Parsing Completeness

    @settings(max_examples=150, deadline=None)
    @given(
        input_tokens=st.integers(min_value=0, max_value=50000),
        output_tokens=st.integers(min_value=0, max_value=50000),
        cache_read=st.integers(min_value=0, max_value=50000),
        cache_write=st.integers(min_value=0, max_value=50000),
        user_agent=st.sampled_from(["kiro/1.0", "q-developer/2.0", "custom-app/1.0", ""]),
        event_name=st.sampled_from(["InvokeModel", "InvokeModelWithResponseStream"]),
    )
    def test_parsed_record_has_all_fields(self, input_tokens, output_tokens, cache_read, cache_write, user_agent, event_name):
        ct_event = {
            "eventName": event_name,
            "eventTime": "2025-03-15T10:00:00Z",
            "eventID": "test-event-123",
            "userIdentity": {"arn": "arn:aws:iam::123456789:user/testuser"},
            "requestParameters": {"modelId": "anthropic.claude-sonnet-4-20250514-v1:0"},
            "sourceIPAddress": "10.0.0.1",
            "userAgent": user_agent,
            "responseElements": {
                "usage": {
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "cacheReadInputTokens": cache_read,
                    "cacheWriteInputTokens": cache_write,
                }
            },
        }
        event = {"CloudTrailEvent": json.dumps(ct_event)}
        result = svc._parse_event(event)

        assert result is not None
        assert result["inputTokens"] == input_tokens
        assert result["outputTokens"] == output_tokens
        assert result["cacheReadTokens"] == cache_read
        assert result["cacheWriteTokens"] == cache_write
        assert result["totalTokens"] == input_tokens + output_tokens + cache_read + cache_write
        assert result["userIdentity"] == "arn:aws:iam::123456789:user/testuser"
        assert result["modelId"] == "anthropic.claude-sonnet-4-20250514-v1:0"
        assert result["sourceIp"] == "10.0.0.1"

    @settings(max_examples=150, deadline=None)
    @given(
        user_agent=st.sampled_from(["kiro/1.0", "q-developer/2.0", "custom-app/1.0", ""]),
    )
    def test_source_application_identification(self, user_agent):
        result = CloudTrailService._identify_source_application(user_agent)
        if "kiro" in user_agent.lower():
            assert result == "Kiro"
        elif "q-developer" in user_agent.lower():
            assert result == "Q Developer"
        else:
            assert result == "Unknown"

    @settings(max_examples=150, deadline=None)
    @given(
        input_tokens=st.integers(min_value=0, max_value=50000),
        output_tokens=st.integers(min_value=0, max_value=50000),
    )
    def test_missing_cache_tokens_default_to_zero(self, input_tokens, output_tokens):
        ct_event = {
            "eventName": "InvokeModel",
            "eventTime": "2025-03-15T10:00:00Z",
            "eventID": "test-no-cache",
            "userIdentity": {"arn": "arn:aws:iam::123456789:user/testuser"},
            "requestParameters": {"modelId": "anthropic.claude-3-haiku-20240307-v1:0"},
            "sourceIPAddress": "10.0.0.1",
            "userAgent": "test",
            "responseElements": {
                "usage": {
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    # No cacheReadInputTokens or cacheWriteInputTokens
                }
            },
        }
        event = {"CloudTrailEvent": json.dumps(ct_event)}
        result = svc._parse_event(event)

        assert result is not None
        assert result["cacheReadTokens"] == 0
        assert result["cacheWriteTokens"] == 0


class TestCloudTrailErrorPropagation:
    """Property 16: CloudTrail Error Propagation.

    CloudTrailError must preserve error code and message.
    """
    # Feature: bedrock-activity-dashboard, Property 16: CloudTrail Error Propagation

    @settings(max_examples=150, deadline=None)
    @given(
        error_code=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"))),
        message=st.text(min_size=1, max_size=200),
    )
    def test_error_preserves_code_and_message(self, error_code, message):
        from app.services.cloudtrail_service import CloudTrailError
        err = CloudTrailError(error_code, message)
        assert err.error_code == error_code
        assert err.message == message
