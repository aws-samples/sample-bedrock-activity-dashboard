"""Isolation tests for Agent Chat user-identity filtering in the activity route.

The full-app TestClient cannot start in this sandbox due to an outdated
botocore that lacks the 'bedrock-agentcore' service model. These tests exercise
``_fetch_and_filter_with_meta`` directly with only the CloudTrail/cost/demo/chat
sources mocked, so the REAL FilterService runs and the user-identity predicate
is genuinely validated.

Fixed behavior: Agent Chat records ("agent-chat") respect the same user filter
as CloudTrail records — excluded when filtering for a specific human user,
included when no user filter is set.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.routes import activity


def _params():
    return {
        "start_date": datetime(2024, 1, 10, tzinfo=timezone.utc),
        "end_date": datetime(2024, 1, 17, tzinfo=timezone.utc),
        "user_identities": [],
        "team_id": None,
        "source_application": None,
    }


def _human_record():
    return {
        "id": "ct-alice",
        "timestamp": "2024-01-15T08:00:00+00:00",
        "userIdentity": "arn:aws:iam::123456789012:user/alice",
        "userName": "alice",
        "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
        "sourceApplication": "Kiro",
        "sourceIp": "10.0.0.1",
        "inputTokens": 100,
        "outputTokens": 50,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 150,
        "costEstimate": 0.05,
    }


def _chat_record():
    return {
        "id": "chat-1",
        "timestamp": "2024-01-15T09:00:00+00:00",
        "userIdentity": "agent-chat",
        "userName": "Agent Chat User",
        "modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "sourceApplication": "Agent Chat",
        "sourceIp": "N/A",
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 0,
        "costEstimate": None,
    }


def test_chat_records_included_when_no_user_filter():
    """With no user filter set, Agent Chat records are still included."""
    with (
        patch.object(activity, "_cloudtrail_service") as mock_ct,
        patch.object(activity, "_cost_service") as mock_cost,
    ):
        mock_ct.get_activity_records_with_meta.return_value = ([_human_record()], False, False)
        mock_cost.apply_costs.side_effect = lambda records, db: records

        with (
            patch(
                "app.services.demo_service.get_demo_records", return_value=[]
            ),
            patch(
                "app.services.chat_message_service.get_chat_activity_records",
                return_value=[_chat_record()],
            ),
        ):
            records, _, _ = activity._fetch_and_filter_with_meta(_params(), MagicMock())

        ids = [r["id"] for r in records]
        assert "chat-1" in ids
        assert "ct-alice" in ids


def test_chat_records_excluded_when_filtering_for_specific_user():
    """Filtering for a specific human user excludes Agent Chat records."""
    params = _params()
    params["user_identities"] = ["alice"]
    with (
        patch.object(activity, "_cloudtrail_service") as mock_ct,
        patch.object(activity, "_cost_service") as mock_cost,
    ):
        mock_ct.get_activity_records_with_meta.return_value = ([_human_record()], False, False)
        mock_cost.apply_costs.side_effect = lambda records, db: records

        with (
            patch(
                "app.services.demo_service.get_demo_records", return_value=[]
            ),
            patch(
                "app.services.chat_message_service.get_chat_activity_records",
                return_value=[_chat_record()],
            ),
        ):
            records, _, _ = activity._fetch_and_filter_with_meta(params, MagicMock())

        ids = [r["id"] for r in records]
        assert "chat-1" not in ids  # agent-chat no longer leaks in
        assert "ct-alice" in ids


def _real_bedrock_call_for_chat_turn():
    """A real CloudWatch-sourced record for the SAME underlying invocation as
    ``_chat_record()``: same (region-stripped) model id, timestamp a few
    seconds earlier, real token counts and cost."""
    return {
        "id": "cw-real-1",
        "timestamp": "2024-01-15T08:59:58+00:00",
        "userIdentity": "arn:aws:iam::123456789012:role/ecs-task-role",
        "userName": "ecs-task-role",
        "modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "sourceApplication": "CloudWatch Logs",
        "sourceIp": "unknown",
        "inputTokens": 298,
        "outputTokens": 218,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 516,
        "costEstimate": 0.0015,
    }


def test_chat_placeholder_suppressed_when_real_invocation_already_ingested():
    """The Agent Chat placeholder for a turn is dropped once the SAME turn's
    real Bedrock invocation shows up via CloudWatch/CloudTrail — the dashboard
    should show the real (priced) record only, not both."""
    with (
        patch.object(activity, "_cloudtrail_service") as mock_ct,
        patch.object(activity, "_cost_service") as mock_cost,
        patch("app.services.chat_message_service.settings") as mock_settings,
    ):
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_ct.get_activity_records_with_meta.return_value = (
            [_human_record(), _real_bedrock_call_for_chat_turn()],
            False,
            False,
        )
        mock_cost.apply_costs.side_effect = lambda records, db: records

        with (
            patch("app.services.demo_service.get_demo_records", return_value=[]),
            patch(
                "app.services.chat_message_service.get_chat_activity_records",
                return_value=[_chat_record()],
            ),
        ):
            records, _, _ = activity._fetch_and_filter_with_meta(_params(), MagicMock())

        ids = [r["id"] for r in records]
        assert "chat-1" not in ids  # placeholder suppressed
        assert "cw-real-1" in ids  # real, priced record stays
        assert "ct-alice" in ids


def test_chat_placeholder_still_suppressed_when_region_filter_excludes_chat_home_region():
    """Regression test for the bug where a region filter starved dedup of the
    real invocation record.

    Agent Chat's real Bedrock call always runs in ONE fixed region
    (``settings.bedrock_region``) regardless of any display region filter.
    Filtering the activity view to a DIFFERENT region must still widen
    INGESTION to include the chat region, so the real record is fetched and
    the placeholder gets suppressed — even though the real record itself is
    then filtered back out of the final displayed set (it's in the excluded
    region).
    """
    params = _params()
    params["regions"] = ["us-east-1"]  # excludes the chat's home region below

    with (
        patch.object(activity, "_cloudtrail_service") as mock_ct,
        patch.object(activity, "_cost_service") as mock_cost,
        patch("app.services.chat_message_service.settings") as mock_settings,
    ):
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_settings.bedrock_region = "us-west-2"
        mock_settings.aws_region = "us-west-2"

        real_record = _real_bedrock_call_for_chat_turn()
        real_record["region"] = "us-west-2"

        def _get_activity_records_with_meta(start_date, end_date, regions=None):
            # Assert the fix: ingestion must be widened to include the chat
            # region even though the caller only asked to display us-east-1.
            assert regions is not None and "us-west-2" in regions
            return [real_record], False, False

        mock_ct.get_activity_records_with_meta.side_effect = _get_activity_records_with_meta
        mock_cost.apply_costs.side_effect = lambda records, db: records

        with (
            patch("app.services.demo_service.get_demo_records", return_value=[]),
            patch(
                "app.services.chat_message_service.get_chat_activity_records",
                return_value=[_chat_record()],
            ),
        ):
            records, _, _ = activity._fetch_and_filter_with_meta(params, MagicMock())

        ids = [r["id"] for r in records]
        # Placeholder is suppressed (real record was fetched and matched)...
        assert "chat-1" not in ids
        # ...but the real record itself is still excluded from the DISPLAYED
        # set because it's outside the caller's region filter.
        assert "cw-real-1" not in ids
