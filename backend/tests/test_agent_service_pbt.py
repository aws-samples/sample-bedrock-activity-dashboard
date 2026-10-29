"""Property-based tests for AgentService."""

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.agent_service import BedrockConverseError


class TestConverseAPIErrorPropagation:
    """Property 22: Converse API Error Propagation.

    BedrockConverseError must preserve error code and message.
    """
    # Feature: bedrock-activity-dashboard, Property 22: Converse API Error Propagation

    @settings(max_examples=150, deadline=None)
    @given(
        error_code=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"))),
        message=st.text(min_size=1, max_size=200),
    )
    def test_error_preserves_code_and_message(self, error_code, message):
        err = BedrockConverseError(error_code, message)
        assert err.error_code == error_code
        assert err.message == message
