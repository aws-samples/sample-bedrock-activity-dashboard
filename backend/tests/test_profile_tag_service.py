"""Unit tests for ProfileTagService._map_tags (pure, no DB).

Covers the extended tag taxonomy (Application/Environment/Team plus
AgentName/CostCenter/UseCase/Owner/Project) resolved from a Bedrock
``ListTagsForResource`` response. The upsert/COALESCE-on-conflict path in
``_upsert`` uses ``sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update``,
which requires a real Postgres dialect and isn't exercised by this suite's
SQLite fixtures (matching the existing lack of DB-level tests for this
service before this change).
"""

from app.services.profile_tag_service import ProfileTagService, ProfileTags


class TestMapTags:
    def test_maps_all_nine_dimensions(self):
        raw_tags = [
            {"key": "Application", "value": "PatientOutreachAgent"},
            {"key": "Environment", "value": "Production"},
            {"key": "Team", "value": "PatientEngagement"},
            {"key": "AgentName", "value": "PatientOutreachAgent"},
            {"key": "CostCenter", "value": "AI-Engineering"},
            {"key": "UseCase", "value": "PatientCommunication"},
            {"key": "Owner", "value": "ai-platform-team"},
            {"key": "Project", "value": "AgenticPlatform2026"},
            {"key": "Department", "value": "PatientEngagement"},
        ]
        tags = ProfileTagService._map_tags(raw_tags)
        assert tags == ProfileTags(
            application="PatientOutreachAgent",
            environment="Production",
            team="PatientEngagement",
            agent_name="PatientOutreachAgent",
            cost_center="AI-Engineering",
            use_case="PatientCommunication",
            owner="ai-platform-team",
            project="AgenticPlatform2026",
            department="PatientEngagement",
        )

    def test_key_matching_is_case_insensitive(self):
        raw_tags = [
            {"key": "COSTCENTER", "value": "Finance"},
            {"key": "agentname", "value": "BillingAgent"},
        ]
        tags = ProfileTagService._map_tags(raw_tags)
        assert tags.cost_center == "Finance"
        assert tags.agent_name == "BillingAgent"

    def test_unrecognized_keys_are_ignored(self):
        raw_tags = [
            {"key": "Application", "value": "MyApp"},
            {"key": "SomeOtherTag", "value": "ignored"},
        ]
        tags = ProfileTagService._map_tags(raw_tags)
        assert tags.application == "MyApp"
        assert tags.environment is None

    def test_empty_tag_list_yields_all_none(self):
        tags = ProfileTagService._map_tags([])
        assert tags == ProfileTags()

    def test_partial_taxonomy_leaves_rest_none(self):
        """Only the core 3 tags present (today's typical CUR/Bedrock tag set) —
        the extended 5 dimensions default to None (untagged), not an error."""
        raw_tags = [
            {"key": "Application", "value": "MyApp"},
            {"key": "Environment", "value": "prod"},
            {"key": "Team", "value": "PaymentServices"},
        ]
        tags = ProfileTagService._map_tags(raw_tags)
        assert tags.application == "MyApp"
        assert tags.environment == "prod"
        assert tags.team == "PaymentServices"
        assert tags.agent_name is None
        assert tags.cost_center is None
        assert tags.use_case is None
        assert tags.owner is None
        assert tags.project is None
        assert tags.department is None
