"""Tests for AI Agent chat per-user scoping, history cap, and context sourcing.

These tests are deliberately isolated (no full-app TestClient) because the app
cannot boot in the sandbox due to a pre-existing botocore 'bedrock-agentcore'
limitation unrelated to this change. They exercise the route handler logic, the
_load_history cap, and build_usage_context's data sourcing directly.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import CurrentUser
from app.database import Base
from app.models import ChatMessage
from app.routes.agent import ChatFilters, ChatRequest, agent_chat, agent_chat_stream
from app.services.agent_service import AgentService, _MAX_HISTORY_MESSAGES


# ---------------------------------------------------------------------------
# In-memory SQLite for the history-cap test
# ---------------------------------------------------------------------------

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=_engine)
    session = _TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=_engine)


def _admin() -> CurrentUser:
    return CurrentUser(
        username="admin", email="admin@example.com", groups=["admin"],
        is_admin=True, can_view_all=True, identity="admin@example.com",
    )


def _regular_user() -> CurrentUser:
    return CurrentUser(
        username="bob", email="bob@example.com", groups=[],
        is_admin=False, can_view_all=False, identity="bob@example.com",
    )


# ---------------------------------------------------------------------------
# #1 Route scoping — non-admins are forced to their own identity
# ---------------------------------------------------------------------------

class TestChatRouteScoping:
    @patch("app.routes.agent.AgentService")
    def test_non_admin_chat_is_scoped_to_own_identity(self, MockAgentService):
        mock_agent = MagicMock()
        mock_agent.chat.return_value = {
            "sessionId": "s1", "message": "ok", "timestamp": "t",
        }
        MockAgentService.return_value = mock_agent

        # Caller tries to snoop on someone else's data via the filter.
        body = ChatRequest(
            message="hi",
            filters=ChatFilters(user_identities=["victim@example.com"]),
        )
        asyncio.run(agent_chat(body, db=MagicMock(), current_user=_regular_user()))

        filters_passed = mock_agent.chat.call_args.args[2]
        assert filters_passed["user_identities"] == ["bob@example.com"]

    @patch("app.routes.agent.AgentService")
    def test_admin_chat_filters_unchanged(self, MockAgentService):
        mock_agent = MagicMock()
        mock_agent.chat.return_value = {
            "sessionId": "s1", "message": "ok", "timestamp": "t",
        }
        MockAgentService.return_value = mock_agent

        body = ChatRequest(
            message="hi",
            filters=ChatFilters(user_identities=["someone@example.com"]),
        )
        asyncio.run(agent_chat(body, db=MagicMock(), current_user=_admin()))

        filters_passed = mock_agent.chat.call_args.args[2]
        # Admin retains whatever client-supplied user filter was set.
        assert filters_passed["user_identities"] == ["someone@example.com"]

    @patch("app.routes.agent.AgentService")
    def test_non_admin_stream_is_scoped_to_own_identity(self, MockAgentService):
        mock_agent = MagicMock()
        mock_agent.chat_stream.return_value = iter(["data: hi\n\n", "data: [DONE:s1]\n\n"])
        MockAgentService.return_value = mock_agent

        body = ChatRequest(
            message="hi",
            filters=ChatFilters(user_identities=["victim@example.com"]),
        )

        async def _run():
            resp = await agent_chat_stream(body, db=MagicMock(), current_user=_regular_user())
            async for _ in resp.body_iterator:
                pass

        asyncio.run(_run())

        filters_passed = mock_agent.chat_stream.call_args.args[2]
        assert filters_passed["user_identities"] == ["bob@example.com"]

    @patch("app.routes.agent.AgentService")
    def test_admin_stream_filters_unchanged(self, MockAgentService):
        mock_agent = MagicMock()
        mock_agent.chat_stream.return_value = iter(["data: hi\n\n"])
        MockAgentService.return_value = mock_agent

        body = ChatRequest(
            message="hi",
            filters=ChatFilters(user_identities=["someone@example.com"]),
        )

        async def _run():
            resp = await agent_chat_stream(body, db=MagicMock(), current_user=_admin())
            async for _ in resp.body_iterator:
                pass

        asyncio.run(_run())

        filters_passed = mock_agent.chat_stream.call_args.args[2]
        assert filters_passed["user_identities"] == ["someone@example.com"]


# ---------------------------------------------------------------------------
# #6 History cap — only the most recent N messages replay to the model
# ---------------------------------------------------------------------------

class TestLoadHistoryCap:
    def test_load_history_caps_to_most_recent_in_chronological_order(self, db_session):
        session_id = uuid.uuid4()
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)

        total = _MAX_HISTORY_MESSAGES + 10  # 30 messages, > cap of 20
        for i in range(total):
            db_session.add(
                ChatMessage(
                    session_id=session_id,
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"msg-{i:03d}",
                    created_at=base + timedelta(minutes=i),
                )
            )
        db_session.commit()

        history = AgentService._load_history(session_id, db_session)

        # Only the last N are returned.
        assert len(history) == _MAX_HISTORY_MESSAGES
        contents = [m.content for m in history]
        # Chronological ascending: oldest of the kept window first, newest last.
        expected = [f"msg-{i:03d}" for i in range(total - _MAX_HISTORY_MESSAGES, total)]
        assert contents == expected
        assert contents[0] == "msg-010"
        assert contents[-1] == "msg-029"

    def test_load_history_under_cap_returns_all_chronological(self, db_session):
        session_id = uuid.uuid4()
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            db_session.add(
                ChatMessage(
                    session_id=session_id,
                    role="user",
                    content=f"m-{i}",
                    created_at=base + timedelta(minutes=i),
                )
            )
        db_session.commit()

        history = AgentService._load_history(session_id, db_session)
        assert [m.content for m in history] == ["m-0", "m-1", "m-2", "m-3", "m-4"]


# ---------------------------------------------------------------------------
# #3 build_usage_context sources merged demo + chat records
# ---------------------------------------------------------------------------

@patch("app.services.agent_service.boto3.Session")
class TestBuildUsageContextSourcing:
    @patch("app.services.agent_service.CloudTrailService")
    def test_context_reflects_merged_demo_and_chat_records(self, mock_ct_cls, mock_session):
        # ct_records includes a merged persisted-demo record; chat_records is an
        # Agent Chat record. With NO user filter (admin/unscoped), chat records
        # are included — mirroring the activity route.
        ct_records = [
            {
                "id": "real-1", "timestamp": "2024-01-15T10:00:00+00:00",
                "userIdentity": "arn:aws:iam::123:user/alice", "userName": "alice",
                "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
                "sourceApplication": "Kiro", "sourceIp": "10.0.0.1",
                "inputTokens": 500, "outputTokens": 200, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 700, "costEstimate": 1.0,
            },
            {
                "id": "demo-1", "timestamp": "2024-01-16T10:00:00+00:00",
                "userIdentity": "arn:aws:iam::123:user/demo", "userName": "demo-user",
                "modelId": "amazon.nova-pro-v1:0",
                "sourceApplication": "DemoApp", "sourceIp": "10.0.0.9",
                "inputTokens": 1000, "outputTokens": 0, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 1000, "costEstimate": 2.0,
            },
        ]
        chat_records = [
            {
                "id": "chat-1", "timestamp": "2024-01-17T10:00:00+00:00",
                "userIdentity": "agent-chat", "userName": "Agent Chat",
                "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
                "sourceApplication": "Agent Chat", "sourceIp": "",
                "inputTokens": 300, "outputTokens": 0, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 300, "costEstimate": 0.5,
            },
        ]

        db = MagicMock()
        svc = AgentService()
        filters = {
            "start_date": "2024-01-01T00:00:00+00:00",
            "end_date": "2024-01-31T23:59:59+00:00",
        }

        with patch(
            "app.routes.activity.source_activity_records_with_meta",
            return_value=(ct_records, chat_records, False, False),
        ):
            context = svc.build_usage_context(filters, db)

        # Merged totals: 700 + 1000 + 300 = 2000 tokens across 3 invocations.
        assert "Total Invocations: 3" in context
        assert "Total Tokens: 2000" in context
        # All three sources are represented.
        assert "demo-user" in context
        assert "Agent Chat" in context
        assert "alice" in context
        # Source-application section present with the merged apps.
        assert "Source Application Usage" in context
        assert "DemoApp" in context

    @patch("app.services.agent_service.CloudTrailService")
    def test_scoped_user_excludes_chat_records(self, mock_ct_cls, mock_session):
        # When a user filter is set (as #1 forces for non-admins), agent-chat
        # records are excluded because their identity won't match a human user.
        ct_records = [
            {
                "id": "real-1", "timestamp": "2024-01-15T10:00:00+00:00",
                "userIdentity": "bob@example.com", "userName": "bob",
                "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
                "sourceApplication": "Kiro", "sourceIp": "10.0.0.1",
                "inputTokens": 100, "outputTokens": 0, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 100, "costEstimate": 0.1,
            },
        ]
        chat_records = [
            {
                "id": "chat-1", "timestamp": "2024-01-17T10:00:00+00:00",
                "userIdentity": "agent-chat", "userName": "Agent Chat",
                "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
                "sourceApplication": "Agent Chat", "sourceIp": "",
                "inputTokens": 300, "outputTokens": 0, "cacheReadTokens": 0,
                "cacheWriteTokens": 0, "totalTokens": 300, "costEstimate": 0.5,
            },
        ]

        db = MagicMock()
        svc = AgentService()
        filters = {
            "start_date": "2024-01-01T00:00:00+00:00",
            "end_date": "2024-01-31T23:59:59+00:00",
            "user_identities": ["bob@example.com"],
        }

        with patch(
            "app.routes.activity.source_activity_records_with_meta",
            return_value=(ct_records, chat_records, False, False),
        ):
            context = svc.build_usage_context(filters, db)

        # Only bob's single 100-token record remains; agent-chat is excluded.
        assert "Total Invocations: 1" in context
        assert "Total Tokens: 100" in context
        assert "Agent Chat" not in context
