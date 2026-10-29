"""Unit tests for the on-demand demo data service.

Uses an in-memory SQLite database with only the ``demo_activity_records`` table
created (it uses generic column types, so no PostgreSQL-specific dialect is
needed). Bedrock invocations are always mocked — no network calls. Every
generated record is modeled as a real Bedrock invocation (the service has no
purely-fabricated path); only the persisted timestamp is staged.
"""

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import DemoActivityRecord
from app.services import demo_service


@pytest.fixture()
def db():
    """In-memory SQLite session with just the demo table."""
    engine = create_engine("sqlite://")
    DemoActivityRecord.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _ok_client(input_tokens: int = 42, output_tokens: int = 17, text: str = "A safe response."):
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }
    return client


# ---------------------------------------------------------------------------
# generate_demo_data
# ---------------------------------------------------------------------------

class TestGenerateDemoData:
    def test_all_records_are_real_invocations_with_pii_and_fake_content(self, db):
        client = _ok_client()
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            summary = demo_service.generate_demo_data(db, count=25)

        assert summary["requested"] == 25
        assert summary["succeeded"] == 25
        assert summary["failed"] == []
        assert summary["batchId"]

        rows = db.query(DemoActivityRecord).all()
        assert len(rows) == 25

        for row in rows:
            assert row.pii_types, "expected pii_types to be populated"
            assert all(t in demo_service._PII_TYPES for t in row.pii_types)
            # Every record is a genuine invocation now — no fabricated path.
            assert row.is_real_invocation is True
            assert row.batch_id == summary["batchId"]
            assert row.input_tokens == 42
            assert row.output_tokens == 17
            assert row.response_text == "A safe response."

        # At least one of the standard fake-PII test values is present somewhere.
        all_prompts = " ".join(r.prompt_text for r in rows)
        assert (
            "123-45-6789" in all_prompts
            or "4111 1111 1111 1111" in all_prompts
            or "jane.doe@example.com" in all_prompts
            or "AKIAIOSFODNN7EXAMPLE" in all_prompts
        )

    def test_pii_types_match_embedded_fragments(self, db):
        client = _ok_client()
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            demo_service.generate_demo_data(db, count=15)
        for row in db.query(DemoActivityRecord).all():
            # Every declared pii_type must actually appear in the prompt text.
            for t in row.pii_types:
                assert demo_service._FAKE_PII_FRAGMENTS[t] in row.prompt_text

    def test_invocation_failure_is_tolerated_and_reported(self, db):
        """A model that raises (not enabled / access denied) is recorded as failed."""
        mock_client = MagicMock()
        mock_client.converse.side_effect = Exception("AccessDeniedException: model not enabled")

        with patch.object(demo_service, "_bedrock_runtime_client", return_value=mock_client):
            summary = demo_service.generate_demo_data(db, count=3)

        assert summary["requested"] == 3
        assert summary["succeeded"] == 0
        assert len(summary["failed"]) == 3
        for failure in summary["failed"]:
            assert "model" in failure and "error" in failure

        # No rows persisted for failed invocations.
        assert db.query(DemoActivityRecord).count() == 0

    def test_partial_failures_still_persist_successes(self, db):
        """Some invocations fail, others succeed — only successes are persisted."""
        mock_client = MagicMock()
        call_count = {"n": 0}

        def _side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                raise Exception("ThrottlingException")
            return {
                "output": {"message": {"content": [{"text": "ok"}]}},
                "usage": {"inputTokens": 10, "outputTokens": 5},
            }

        mock_client.converse.side_effect = _side_effect

        with patch.object(demo_service, "_bedrock_runtime_client", return_value=mock_client):
            summary = demo_service.generate_demo_data(db, count=10)

        assert summary["requested"] == 10
        assert summary["succeeded"] + len(summary["failed"]) == 10
        assert db.query(DemoActivityRecord).count() == summary["succeeded"]

    def test_zero_count_returns_empty_summary_without_client_call(self, db):
        with patch.object(demo_service, "_bedrock_runtime_client") as mock_ctor:
            summary = demo_service.generate_demo_data(db, count=0)

        assert summary == {
            "requested": 0, "succeeded": 0, "failed": [], "batchId": summary["batchId"],
            "guardrailIntervenedCount": 0, "applicationProfilesUsed": [],
        }
        mock_ctor.assert_not_called()

    def test_default_omits_application_profiles(self, db):
        """with_applications defaults to False: no control-plane client is
        touched and every record's inferenceProfileArn stays None."""
        client = _ok_client()
        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(demo_service, "_bedrock_control_plane_client") as mock_control,
        ):
            summary = demo_service.generate_demo_data(db, count=5)

        assert summary["applicationProfilesUsed"] == []
        mock_control.assert_not_called()
        for row in db.query(DemoActivityRecord).all():
            assert row.inference_profile_arn is None

    def test_with_applications_routes_some_jobs_through_tagged_profiles(self, db):
        """When with_applications=True, the control-plane client is used to
        get-or-create the demo app profiles, and at least some succeeded
        records carry a non-null inference_profile_arn + resolve to one of
        the known demo Application names."""
        client = _ok_client()
        control_client = MagicMock()
        control_client.list_inference_profiles.return_value = {"inferenceProfileSummaries": []}
        control_client.create_inference_profile.side_effect = [
            {"inferenceProfileArn": f"arn:aws:bedrock:us-west-2:111111111111:application-inference-profile/{i}"}
            for i in range(len(demo_service._DEMO_APP_PROFILES))
        ]

        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(demo_service, "_bedrock_control_plane_client", return_value=control_client),
            patch.object(
                demo_service,
                "_demo_profile_source_arn",
                return_value="arn:aws:bedrock:us-west-2:111111111111:inference-profile/us.amazon.nova-lite-v1:0",
            ),
        ):
            summary = demo_service.generate_demo_data(db, count=40, with_applications=True)

        assert summary["succeeded"] == 40
        control_client.list_inference_profiles.assert_called()
        assert control_client.create_inference_profile.call_count == len(
            demo_service._DEMO_APP_PROFILES
        )

        known_apps = {spec["application"] for spec in demo_service._DEMO_APP_PROFILES}
        rows = db.query(DemoActivityRecord).all()
        tagged_rows = [r for r in rows if r.inference_profile_arn is not None]
        # With 40 jobs and a 60% assignment fraction, expect at least one.
        assert len(tagged_rows) > 0
        assert set(summary["applicationProfilesUsed"]).issubset(known_apps)
        assert len(summary["applicationProfilesUsed"]) > 0
        for row in tagged_rows:
            assert row.inference_profile_arn.startswith(
                "arn:aws:bedrock:us-west-2:111111111111:application-inference-profile/"
            )

    def test_with_applications_reuses_existing_profiles_by_name(self, db):
        """A profile already present in list_inference_profiles (matched by
        name) is reused rather than recreated."""
        client = _ok_client()
        control_client = MagicMock()
        existing_name = demo_service._DEMO_APP_PROFILES[0]["name"]
        control_client.list_inference_profiles.return_value = {
            "inferenceProfileSummaries": [
                {
                    "inferenceProfileName": existing_name,
                    "inferenceProfileArn": "arn:aws:bedrock:us-west-2:111111111111:application-inference-profile/existing",
                }
            ]
        }
        control_client.create_inference_profile.side_effect = [
            {"inferenceProfileArn": f"arn:aws:bedrock:us-west-2:111111111111:application-inference-profile/new{i}"}
            for i in range(len(demo_service._DEMO_APP_PROFILES) - 1)
        ]

        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(demo_service, "_bedrock_control_plane_client", return_value=control_client),
            patch.object(
                demo_service,
                "_demo_profile_source_arn",
                return_value="arn:aws:bedrock:us-west-2:111111111111:inference-profile/us.amazon.nova-lite-v1:0",
            ),
        ):
            demo_service.generate_demo_data(db, count=10, with_applications=True)

        # Only the profiles NOT already present are created.
        assert control_client.create_inference_profile.call_count == (
            len(demo_service._DEMO_APP_PROFILES) - 1
        )

    def test_with_applications_profile_setup_failure_degrades_gracefully(self, db):
        """If the control-plane client can't be built at all (e.g. missing
        IAM permission surfaces as a client construction error), generation
        still succeeds with bare-model-id jobs."""
        client = _ok_client()
        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(
                demo_service, "_bedrock_control_plane_client", side_effect=Exception("AccessDenied")
            ),
        ):
            summary = demo_service.generate_demo_data(db, count=5, with_applications=True)

        assert summary["succeeded"] == 5
        assert summary["applicationProfilesUsed"] == []
        for row in db.query(DemoActivityRecord).all():
            assert row.inference_profile_arn is None

    def test_with_applications_create_failure_skips_that_profile_only(self, db):
        """One profile's create_inference_profile call fails; the others are
        still created/used and generation is unaffected."""
        client = _ok_client()
        control_client = MagicMock()
        control_client.list_inference_profiles.return_value = {"inferenceProfileSummaries": []}

        def _create_side_effect(**kwargs):
            if kwargs["inferenceProfileName"] == demo_service._DEMO_APP_PROFILES[0]["name"]:
                raise Exception("ValidationException: model not enabled")
            return {
                "inferenceProfileArn": f"arn:aws:bedrock:us-west-2:111111111111:application-inference-profile/{kwargs['inferenceProfileName']}"
            }

        control_client.create_inference_profile.side_effect = _create_side_effect

        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(demo_service, "_bedrock_control_plane_client", return_value=control_client),
            patch.object(
                demo_service,
                "_demo_profile_source_arn",
                return_value="arn:aws:bedrock:us-west-2:111111111111:inference-profile/us.amazon.nova-lite-v1:0",
            ),
        ):
            summary = demo_service.generate_demo_data(db, count=40, with_applications=True)

        assert summary["succeeded"] == 40
        failed_app = demo_service._DEMO_APP_PROFILES[0]["application"]
        assert failed_app not in summary["applicationProfilesUsed"]

    def test_get_or_create_demo_app_profiles_tags_with_lowercase_key_value(self, db):
        """Bedrock's tag-resource shape uses lowercase {key, value}, not
        {Key, Value} — regression guard against the real API's parameter
        validation (confirmed against the live API during development)."""
        control_client = MagicMock()
        control_client.list_inference_profiles.return_value = {"inferenceProfileSummaries": []}
        control_client.create_inference_profile.return_value = {
            "inferenceProfileArn": "arn:aws:bedrock:us-west-2:111111111111:application-inference-profile/x"
        }

        with patch.object(
            demo_service,
            "_demo_profile_source_arn",
            return_value="arn:aws:bedrock:us-west-2:111111111111:inference-profile/us.amazon.nova-lite-v1:0",
        ):
            demo_service._get_or_create_demo_app_profiles(control_client)

        for call in control_client.create_inference_profile.call_args_list:
            for tag in call.kwargs["tags"]:
                assert set(tag.keys()) == {"key", "value"}
            # description must satisfy CreateInferenceProfile's regex
            # constraint (no em dash or other disallowed punctuation).
            assert re.match(r"^([0-9a-zA-Z:.][ _-]?)+$", call.kwargs["description"])
            # copyFrom must be a full ARN, not a bare model id.
            assert call.kwargs["modelSource"]["copyFrom"].startswith("arn:aws:bedrock:")


    def test_client_construction_failure_reports_as_failed_batch(self, db):
        with patch.object(demo_service, "_bedrock_runtime_client", side_effect=Exception("no creds")):
            summary = demo_service.generate_demo_data(db, count=5)

        assert summary["requested"] == 5
        assert summary["succeeded"] == 0
        assert len(summary["failed"]) == 1
        assert db.query(DemoActivityRecord).count() == 0

    def test_timestamps_spread_across_last_14_days(self, db):
        """The persisted timestamp is staged across ~14 days even though every
        invocation actually happens at generation time."""
        client = _ok_client()
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            demo_service.generate_demo_data(db, count=30)

        now = datetime.now(timezone.utc)
        rows = db.query(DemoActivityRecord).all()
        distinct_dates = set()
        for row in rows:
            ts = datetime.fromisoformat(row.timestamp)
            assert now - timedelta(days=15) <= ts <= now + timedelta(minutes=1)
            distinct_dates.add(ts.date())
        # With 30 records spread over 14 days, expect more than a single day.
        assert len(distinct_dates) > 1


# ---------------------------------------------------------------------------
# clear_demo_data
# ---------------------------------------------------------------------------

class TestClearDemoData:
    def test_clear_empties_table_and_returns_count(self, db):
        client = _ok_client()
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            demo_service.generate_demo_data(db, count=10)
        result = demo_service.clear_demo_data(db)
        assert result["deleted"] == 10
        assert db.query(DemoActivityRecord).count() == 0

    def test_clear_on_empty_returns_zero(self, db):
        assert demo_service.clear_demo_data(db) == {"deleted": 0}


# ---------------------------------------------------------------------------
# get_demo_status
# ---------------------------------------------------------------------------

class TestGetDemoStatus:
    def test_status_counts(self, db):
        client = _ok_client(input_tokens=5, output_tokens=5)
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            summary = demo_service.generate_demo_data(db, count=8)

        status = demo_service.get_demo_status(db)
        assert status["recordCount"] == 8
        assert status["realCount"] == 8  # every record is real now
        assert status["lastGeneratedAt"] is not None
        assert status["lastBatchId"] == summary["batchId"]

    def test_status_empty(self, db):
        status = demo_service.get_demo_status(db)
        assert status == {
            "recordCount": 0,
            "realCount": 0,
            "lastGeneratedAt": None,
            "lastBatchId": None,
        }


# ---------------------------------------------------------------------------
# get_demo_records
# ---------------------------------------------------------------------------

class TestGetDemoRecords:
    def test_returns_activity_shape_dicts_within_window(self, db):
        client = _ok_client()
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            demo_service.generate_demo_data(db, count=20)

        start = datetime.now(timezone.utc) - timedelta(days=30)
        end = datetime.now(timezone.utc) + timedelta(days=1)
        records = demo_service.get_demo_records(db, start, end)

        assert records, "expected demo records within a wide window"
        expected_keys = {
            "id", "timestamp", "userIdentity", "userName", "modelId",
            "sourceApplication", "sourceIp", "inputTokens", "outputTokens",
            "cacheReadTokens", "cacheWriteTokens", "totalTokens", "costEstimate",
            "promptText", "responseText", "inferenceProfileArn",
        }
        for r in records:
            assert expected_keys.issubset(r.keys())
            assert r["costEstimate"] is None
            assert r["totalTokens"] == (
                r["inputTokens"] + r["outputTokens"]
                + r["cacheReadTokens"] + r["cacheWriteTokens"]
            )
            # Generated without with_applications -> never tagged.
            assert r["inferenceProfileArn"] is None

    def test_inference_profile_arn_surfaced_when_present(self, db):
        """A record whose underlying job was routed through a tagged app
        profile carries that ARN in the activity-record shape, so the
        existing enrich_records_with_dimensions pipeline can resolve it."""
        client = _ok_client()
        control_client = MagicMock()
        control_client.list_inference_profiles.return_value = {"inferenceProfileSummaries": []}
        control_client.create_inference_profile.side_effect = [
            {"inferenceProfileArn": f"arn:aws:bedrock:us-west-2:111111111111:application-inference-profile/{i}"}
            for i in range(len(demo_service._DEMO_APP_PROFILES))
        ]

        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(demo_service, "_bedrock_control_plane_client", return_value=control_client),
            patch.object(
                demo_service,
                "_demo_profile_source_arn",
                return_value="arn:aws:bedrock:us-west-2:111111111111:inference-profile/us.amazon.nova-lite-v1:0",
            ),
        ):
            demo_service.generate_demo_data(db, count=40, with_applications=True)

        start = datetime.now(timezone.utc) - timedelta(days=30)
        end = datetime.now(timezone.utc) + timedelta(days=1)
        records = demo_service.get_demo_records(db, start, end)
        tagged = [r for r in records if r["inferenceProfileArn"] is not None]
        assert len(tagged) > 0
        for r in tagged:
            assert r["inferenceProfileArn"].startswith(
                "arn:aws:bedrock:us-west-2:111111111111:application-inference-profile/"
            )

    def test_window_excludes_out_of_range_records(self, db):
        # Generate records spread over the last 14 days, then request a window
        # far in the past that should contain none of them.
        client = _ok_client()
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            demo_service.generate_demo_data(db, count=20)

        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        end = datetime(2000, 1, 2, tzinfo=timezone.utc)
        records = demo_service.get_demo_records(db, start, end)
        assert records == []

    def test_guardrail_config_attached_when_available(self, db):
        client = _ok_client()
        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(
                demo_service,
                "get_latest_published_guardrail",
                return_value=("gr-abc123", "1"),
            ),
        ):
            demo_service.generate_demo_data(db, count=3)

        for call in client.converse.call_args_list:
            assert call.kwargs["guardrailConfig"] == {
                "guardrailIdentifier": "gr-abc123",
                "guardrailVersion": "1",
                "trace": "enabled",
            }

    def test_no_guardrail_config_when_none_published(self, db):
        client = _ok_client()
        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(demo_service, "get_latest_published_guardrail", return_value=None),
        ):
            demo_service.generate_demo_data(db, count=3)

        for call in client.converse.call_args_list:
            assert "guardrailConfig" not in call.kwargs

    def test_guardrail_intervened_count_reported_in_summary(self, db):
        client = MagicMock()
        client.converse.return_value = {
            "output": {"message": {"content": [{"text": "blocked"}]}},
            "usage": {"inputTokens": 5, "outputTokens": 5},
            "stopReason": "guardrail_intervened",
        }
        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(
                demo_service,
                "get_latest_published_guardrail",
                return_value=("gr-abc123", "1"),
            ),
        ):
            summary = demo_service.generate_demo_data(db, count=4)

        assert summary["guardrailIntervenedCount"] == 4

    def test_guardrail_lookup_failure_does_not_block_generation(self, db):
        """A DB error looking up the guardrail degrades to no guardrail rather
        than failing the whole batch."""
        client = _ok_client()
        with (
            patch.object(demo_service, "_bedrock_runtime_client", return_value=client),
            patch.object(
                demo_service,
                "get_latest_published_guardrail",
                side_effect=Exception("db unavailable"),
            ),
        ):
            summary = demo_service.generate_demo_data(db, count=3)

        assert summary["succeeded"] == 3
        for call in client.converse.call_args_list:
            assert "guardrailConfig" not in call.kwargs

    def test_records_carry_invoked_at_for_suppression_matching(self, db):
        client = _ok_client()
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            demo_service.generate_demo_data(db, count=5)

        start = datetime.now(timezone.utc) - timedelta(days=30)
        end = datetime.now(timezone.utc) + timedelta(days=1)
        records = demo_service.get_demo_records(db, start, end)

        for r in records:
            assert r["_invokedAt"] is not None
            # Real invocation happened "now" — independent of the staged
            # display timestamp, which may be up to 14 days in the past.
            # SQLite (used only in this test) doesn't preserve tzinfo on
            # round-trip, so normalize to UTC before comparing.
            invoked = datetime.fromisoformat(r["_invokedAt"])
            if invoked.tzinfo is None:
                invoked = invoked.replace(tzinfo=timezone.utc)
            assert datetime.now(timezone.utc) - invoked < timedelta(minutes=1)


# ---------------------------------------------------------------------------
# suppress_real_duplicates_of_demo_invocations
# ---------------------------------------------------------------------------

class TestSuppressRealDuplicatesOfDemoInvocations:
    def _demo_record(self, invoked_at: str, model_id: str = "amazon.nova-lite-v1:0"):
        return {
            "id": "demo-1",
            "modelId": model_id,
            "_invokedAt": invoked_at,
        }

    def _real_record(self, record_id: str, timestamp: str, model_id: str = "amazon.nova-lite-v1:0"):
        return {"id": record_id, "modelId": model_id, "timestamp": timestamp}

    def test_real_duplicate_within_tolerance_is_dropped(self):
        demo = [self._demo_record("2024-01-15T12:00:00+00:00")]
        real = [self._real_record("real-1", "2024-01-15T12:00:30+00:00")]

        kept = demo_service.suppress_real_duplicates_of_demo_invocations(demo, real)
        assert kept == []

    def test_real_record_outside_tolerance_is_kept(self):
        demo = [self._demo_record("2024-01-15T12:00:00+00:00")]
        real = [self._real_record("real-1", "2024-01-15T12:10:00+00:00")]  # 10 min away

        kept = demo_service.suppress_real_duplicates_of_demo_invocations(demo, real)
        assert len(kept) == 1
        assert kept[0]["id"] == "real-1"

    def test_real_record_with_different_model_is_kept(self):
        demo = [self._demo_record("2024-01-15T12:00:00+00:00", model_id="amazon.nova-lite-v1:0")]
        real = [self._real_record(
            "real-1", "2024-01-15T12:00:05+00:00", model_id="amazon.nova-micro-v1:0"
        )]

        kept = demo_service.suppress_real_duplicates_of_demo_invocations(demo, real)
        assert len(kept) == 1

    def test_unrelated_real_records_are_kept(self):
        demo = [self._demo_record("2024-01-15T12:00:00+00:00")]
        real = [self._real_record("real-alice", "2024-01-10T08:00:00+00:00")]

        kept = demo_service.suppress_real_duplicates_of_demo_invocations(demo, real)
        assert len(kept) == 1
        assert kept[0]["id"] == "real-alice"

    def test_demo_record_without_invoked_at_never_suppresses(self):
        """Legacy demo rows (generated before invoked_at existed) never match."""
        demo = [self._demo_record(None)]
        real = [self._real_record("real-1", "2024-01-15T12:00:05+00:00")]

        kept = demo_service.suppress_real_duplicates_of_demo_invocations(demo, real)
        assert len(kept) == 1

    def test_empty_inputs_are_noops(self):
        assert demo_service.suppress_real_duplicates_of_demo_invocations([], [{"id": "x"}]) == [
            {"id": "x"}
        ]
        assert demo_service.suppress_real_duplicates_of_demo_invocations(
            [self._demo_record("2024-01-15T12:00:00+00:00")], []
        ) == []

    def test_matching_is_one_to_one_not_many_to_one(self):
        """One demo record only consumes ONE real record, even if several fall
        within tolerance — avoids one demo call silently absorbing multiple
        unrelated real invocations."""
        demo = [self._demo_record("2024-01-15T12:00:00+00:00")]
        real = [
            self._real_record("real-1", "2024-01-15T12:00:05+00:00"),
            self._real_record("real-2", "2024-01-15T12:00:10+00:00"),
        ]

        kept = demo_service.suppress_real_duplicates_of_demo_invocations(demo, real)
        # Exactly one real record is consumed per demo record — the other,
        # unrelated real invocation must survive.
        assert len(kept) == 1
        assert kept[0]["id"] == "real-2"

    def test_two_demo_records_each_consume_their_own_real_record(self):
        demo = [
            self._demo_record("2024-01-15T12:00:00+00:00"),
            self._demo_record("2024-01-15T13:00:00+00:00"),
        ]
        real = [
            self._real_record("real-1", "2024-01-15T12:00:05+00:00"),
            self._real_record("real-2", "2024-01-15T13:00:05+00:00"),
        ]

        kept = demo_service.suppress_real_duplicates_of_demo_invocations(demo, real)
        assert kept == []


# ---------------------------------------------------------------------------
# demo_data_present
# ---------------------------------------------------------------------------

class TestDemoDataPresent:
    def test_present_after_generate(self, db):
        present, count = demo_service.demo_data_present(db)
        assert present is False and count == 0

        client = _ok_client()
        with patch.object(demo_service, "_bedrock_runtime_client", return_value=client):
            demo_service.generate_demo_data(db, count=5)
        present, count = demo_service.demo_data_present(db)
        assert present is True and count == 5
