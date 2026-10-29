"""Unit tests for GuardrailCreationService.

All Bedrock control-plane calls (create_guardrail/get_guardrail/
update_guardrail) are mocked — no real AWS calls in tests.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.v2.models import V2AgentGuardrailRecommendation
from app.v2.services.guardrail_creation_service import (
    GuardrailCreationService,
    PII_TYPE_TO_BEDROCK_ENTITY,
)


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def service():
    svc = GuardrailCreationService()
    svc._client = MagicMock()  # bypass lazy boto3 construction
    return svc


def _finding(pii_type: str = "ssn", action: str = "BLOCK") -> V2AgentGuardrailRecommendation:
    f = MagicMock(spec=V2AgentGuardrailRecommendation)
    f.recommendation = {"pii_type": pii_type, "type": "PII", "action": action, "description": "x"}
    return f


class TestApplyRecommendationCreatesFirstGuardrail:
    def test_creates_guardrail_when_none_exists_yet(self, service, db):
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        service.client.create_guardrail.return_value = {
            "guardrailId": "abc123",
            "guardrailArn": "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123",
            "version": "DRAFT",
        }

        outcome = service.apply_recommendation(_finding("ssn", "BLOCK"), db)

        assert outcome["ok"] is True
        assert outcome["guardrail_id"] == "abc123"
        assert outcome["guardrail_version"] == "DRAFT"
        service.client.create_guardrail.assert_called_once()
        call_kwargs = service.client.create_guardrail.call_args.kwargs
        pii_config = call_kwargs["sensitiveInformationPolicyConfig"]["piiEntitiesConfig"]
        assert pii_config == [{"type": "US_SOCIAL_SECURITY_NUMBER", "action": "BLOCK"}]

    def test_unknown_pii_type_returns_error_without_calling_aws(self, service, db):
        outcome = service.apply_recommendation(_finding("unknown_type"), db)

        assert outcome["ok"] is False
        assert "No Bedrock PII entity mapping" in outcome["error"]
        service.client.create_guardrail.assert_not_called()

    def test_invalid_action_defaults_to_anonymize(self, service, db):
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        service.client.create_guardrail.return_value = {
            "guardrailId": "abc", "guardrailArn": "arn:x", "version": "DRAFT",
        }

        service.apply_recommendation(_finding("email", "NOT_A_REAL_ACTION"), db)

        call_kwargs = service.client.create_guardrail.call_args.kwargs
        assert call_kwargs["sensitiveInformationPolicyConfig"]["piiEntitiesConfig"][0]["action"] == "ANONYMIZE"

    def test_create_guardrail_client_error_is_caught(self, service, db):
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        service.client.create_guardrail.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad request"}},
            "CreateGuardrail",
        )

        outcome = service.apply_recommendation(_finding("ssn"), db)

        assert outcome["ok"] is False
        assert "ValidationException" in outcome["error"]


class TestApplyRecommendationUpdatesExistingGuardrail:
    def _mock_existing_row(self, arn: str):
        row = MagicMock()
        row.guardrail_arn = arn
        return row

    def test_updates_existing_guardrail_merging_new_entity(self, service, db):
        existing_arn = "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123"
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            self._mock_existing_row(existing_arn)
        )
        service.client.get_guardrail.return_value = {
            "name": "bedrock-dashboard-guardrail",
            "description": "Managed by the Bedrock Activity Dashboard Guardrail Recommender agent.",
            "blockedInputMessaging": "blocked in",
            "blockedOutputsMessaging": "blocked out",
            "sensitiveInformationPolicy": {
                "piiEntities": [{"type": "EMAIL", "action": "ANONYMIZE"}],
            },
        }
        service.client.update_guardrail.return_value = {
            "guardrailId": "abc123",
            "guardrailArn": existing_arn,
            "version": "DRAFT",
        }

        outcome = service.apply_recommendation(_finding("ssn", "BLOCK"), db)

        assert outcome["ok"] is True
        call_kwargs = service.client.update_guardrail.call_args.kwargs
        pii_config = call_kwargs["sensitiveInformationPolicyConfig"]["piiEntitiesConfig"]
        types = {e["type"] for e in pii_config}
        # Both the pre-existing EMAIL rule and the new SSN rule must be present.
        assert types == {"EMAIL", "US_SOCIAL_SECURITY_NUMBER"}

    def test_updating_same_entity_type_replaces_not_duplicates(self, service, db):
        existing_arn = "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123"
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            self._mock_existing_row(existing_arn)
        )
        service.client.get_guardrail.return_value = {
            "name": "bedrock-dashboard-guardrail",
            "sensitiveInformationPolicy": {
                "piiEntities": [{"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZE"}],
            },
        }
        service.client.update_guardrail.return_value = {
            "guardrailId": "abc123", "guardrailArn": existing_arn, "version": "DRAFT",
        }

        # Same pii_type (ssn) re-approved with a stricter action (BLOCK).
        service.apply_recommendation(_finding("ssn", "BLOCK"), db)

        call_kwargs = service.client.update_guardrail.call_args.kwargs
        pii_config = call_kwargs["sensitiveInformationPolicyConfig"]["piiEntitiesConfig"]
        assert len(pii_config) == 1
        assert pii_config[0] == {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "BLOCK"}

    def test_preserves_existing_regexes_config(self, service, db):
        existing_arn = "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123"
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            self._mock_existing_row(existing_arn)
        )
        service.client.get_guardrail.return_value = {
            "name": "bedrock-dashboard-guardrail",
            "sensitiveInformationPolicy": {
                "piiEntities": [],
                "regexes": [{"name": "custom", "pattern": "FOO-\\d+", "action": "BLOCK"}],
            },
        }
        service.client.update_guardrail.return_value = {
            "guardrailId": "abc123", "guardrailArn": existing_arn, "version": "DRAFT",
        }

        service.apply_recommendation(_finding("email"), db)

        call_kwargs = service.client.update_guardrail.call_args.kwargs
        assert call_kwargs["sensitiveInformationPolicyConfig"]["regexesConfig"] == [
            {"name": "custom", "pattern": "FOO-\\d+", "action": "BLOCK"}
        ]

    def test_get_guardrail_client_error_is_caught(self, service, db):
        existing_arn = "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123"
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            self._mock_existing_row(existing_arn)
        )
        service.client.get_guardrail.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}},
            "GetGuardrail",
        )

        outcome = service.apply_recommendation(_finding("ssn"), db)

        assert outcome["ok"] is False
        assert "ResourceNotFoundException" in outcome["error"]


class TestPiiTypeMapping:
    def test_all_guardrail_recommender_templates_have_a_mapping(self):
        """agentcore-agents/guardrail-recommender's GUARDRAIL_TEMPLATES keys
        must all resolve to a real Bedrock entity — otherwise approval would
        silently no-op for a PII type the recommender actually produces."""
        recommender_pii_types = {"email", "phone", "ssn", "credit_card", "aws_access_key"}
        assert recommender_pii_types.issubset(PII_TYPE_TO_BEDROCK_ENTITY.keys())


class TestPublishesVersionAfterApply:
    """After a successful create/update, apply_recommendation also publishes
    a numbered version (CreateGuardrailVersion) so enforcement callers
    (Agent Chat, Demo Data) pin to a stable snapshot rather than DRAFT."""

    def test_publishes_version_after_create(self, service, db):
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        service.client.create_guardrail.return_value = {
            "guardrailId": "abc123",
            "guardrailArn": "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123",
            "version": "DRAFT",
        }
        service.client.create_guardrail_version.return_value = {
            "guardrailId": "abc123", "version": "1",
        }

        outcome = service.apply_recommendation(_finding("ssn", "BLOCK"), db)

        service.client.create_guardrail_version.assert_called_once_with(
            guardrailIdentifier="abc123",
            description="Published after a Guardrail Recommender approval.",
        )
        assert outcome["guardrail_published_version"] == "1"

    def test_publishes_version_after_update(self, service, db):
        existing_arn = "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123"
        row = MagicMock()
        row.guardrail_arn = existing_arn
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = row
        service.client.get_guardrail.return_value = {
            "name": "bedrock-dashboard-guardrail",
            "sensitiveInformationPolicy": {"piiEntities": []},
        }
        service.client.update_guardrail.return_value = {
            "guardrailId": "abc123", "guardrailArn": existing_arn, "version": "DRAFT",
        }
        service.client.create_guardrail_version.return_value = {
            "guardrailId": "abc123", "version": "2",
        }

        outcome = service.apply_recommendation(_finding("email"), db)

        service.client.create_guardrail_version.assert_called_once()
        assert outcome["guardrail_published_version"] == "2"

    def test_publish_failure_does_not_fail_the_approval(self, service, db):
        """The config change already succeeded — a publish failure is logged
        but the approval outcome is still ok, just without a published version."""
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        service.client.create_guardrail.return_value = {
            "guardrailId": "abc123",
            "guardrailArn": "arn:aws:bedrock:us-west-2:123456789012:guardrail/abc123",
            "version": "DRAFT",
        }
        service.client.create_guardrail_version.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
            "CreateGuardrailVersion",
        )

        outcome = service.apply_recommendation(_finding("ssn"), db)

        assert outcome["ok"] is True
        assert outcome["guardrail_id"] == "abc123"
        assert "guardrail_published_version" not in outcome


class TestGetLatestPublishedGuardrail:
    def test_returns_none_when_nothing_published(self, db):
        from app.v2.services.guardrail_creation_service import get_latest_published_guardrail

        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        assert get_latest_published_guardrail(db) is None

    def test_returns_latest_published_id_and_version(self, db):
        from app.v2.services.guardrail_creation_service import get_latest_published_guardrail

        row = MagicMock()
        row.guardrail_id = "abc123"
        row.guardrail_published_version = "3"
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = row

        result = get_latest_published_guardrail(db)

        assert result == ("abc123", "3")
