"""Unit tests for the Model Cost Optimizer agent's distillation-candidate logic.

Covers the pure helper functions (``is_distillation_candidate`` /
``build_distillation_note``) and their integration into
``analyze_usage_patterns()``. The direct-execution bypass in
``AgentExecutionService._try_direct_execution`` duplicates this logic inline but
delegates the distillation check itself to ``mco.is_distillation_candidate`` /
``mco.build_distillation_note``, so testing the shared helpers here covers both
call paths for that specific behavior.
"""

from unittest.mock import MagicMock, patch

from app.v2.agents import model_cost_optimizer as mco


class TestIsDistillationCandidate:
    def test_true_when_all_thresholds_met(self):
        assert mco.is_distillation_candidate(
            total_invocations=100, simple_task_ratio=0.9, affected_user_count=1
        )

    def test_false_when_invocation_count_too_low(self):
        assert not mco.is_distillation_candidate(
            total_invocations=mco.DISTILLATION_MIN_INVOCATIONS - 1,
            simple_task_ratio=0.9,
            affected_user_count=1,
        )

    def test_false_when_simple_ratio_too_low(self):
        assert not mco.is_distillation_candidate(
            total_invocations=200,
            simple_task_ratio=mco.DISTILLATION_SIMPLE_RATIO_THRESHOLD - 0.01,
            affected_user_count=1,
        )

    def test_false_when_too_many_users(self):
        """High volume + high simple-ratio but many distinct callers looks like
        broad human usage, not a narrow automated task — not a candidate."""
        assert not mco.is_distillation_candidate(
            total_invocations=200,
            simple_task_ratio=0.95,
            affected_user_count=mco.DISTILLATION_MAX_USER_COUNT + 1,
        )

    def test_boundary_values_are_inclusive(self):
        assert mco.is_distillation_candidate(
            total_invocations=mco.DISTILLATION_MIN_INVOCATIONS,
            simple_task_ratio=mco.DISTILLATION_SIMPLE_RATIO_THRESHOLD,
            affected_user_count=mco.DISTILLATION_MAX_USER_COUNT,
        )


class TestBuildDistillationNote:
    def test_includes_model_short_name_and_stats(self):
        note = mco.build_distillation_note(
            "anthropic.claude-sonnet-4-20250514-v1:0",
            total_invocations=120,
            simple_task_ratio=0.85,
            affected_user_count=1,
        )
        assert "claude-sonnet-4" in note
        assert "120" in note
        assert "85%" in note
        assert "1 caller" in note
        assert "distillation" in note.lower()

    def test_pluralizes_caller_count(self):
        note = mco.build_distillation_note(
            "model.id", total_invocations=60, simple_task_ratio=0.8, affected_user_count=2
        )
        assert "2 callers" in note


def _record(user_identity="svc-account", input_tokens=50, prompt_text="hi"):
    return {
        "userIdentity": user_identity,
        "userName": "unknown",
        "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
        "inputTokens": input_tokens,
        "promptText": prompt_text,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }


class TestAnalyzeUsagePatternsDistillationIntegration:
    """Integration of the distillation check into analyze_usage_patterns()."""

    def _run(self, records, price_map=None):
        price_map = price_map or {
            "anthropic.claude-sonnet-4-20250514-v1:0": 0.003,
            "anthropic.claude-haiku-4-5-20251001-v1:0": 0.0008,
        }
        with (
            patch("app.services.cloudtrail_service.CloudTrailService") as mock_ct_cls,
            patch.object(mco, "_load_input_price_map", return_value=price_map),
        ):
            mock_ct = MagicMock()
            mock_ct.get_activity_records.return_value = records
            mock_ct_cls.return_value = mock_ct
            return mco.analyze_usage_patterns()

    def test_findings_never_contain_prompt_text(self):
        """AppSec F-2: per-user prompt text must not be copied into findings."""
        records = [
            _record(user_identity="alice", prompt_text="SECRET-PROMPT-ALICE") for _ in range(60)
        ]
        findings = self._run(records)
        assert findings
        assert "SECRET-PROMPT-ALICE" not in str(findings)

    def test_flags_distillation_candidate_for_narrow_high_volume_usage(self):
        # 60 simple invocations from a single caller -> meets all thresholds.
        records = [_record(user_identity="service-account-1") for _ in range(60)]
        findings = self._run(records)

        assert len(findings) == 1
        finding = findings[0]
        assert finding["is_distillation_candidate"] is True
        assert "distillation" in finding["usage_pattern_summary"].lower()

    def test_does_not_flag_broad_human_usage(self):
        # Same volume/ratio, but spread across many distinct users.
        records = [_record(user_identity=f"user-{i}") for i in range(60)]
        findings = self._run(records)

        assert len(findings) == 1
        finding = findings[0]
        assert finding["is_distillation_candidate"] is False
        assert "distillation" not in finding["usage_pattern_summary"].lower()

    def test_does_not_flag_low_volume_usage(self):
        # Narrow caller set, but not enough invocations to justify distillation.
        records = [_record(user_identity="service-account-1") for _ in range(10)]
        findings = self._run(records)

        assert len(findings) == 1
        finding = findings[0]
        assert finding["is_distillation_candidate"] is False

    def test_model_switch_and_distillation_can_both_apply(self):
        records = [_record(user_identity="service-account-1") for _ in range(60)]
        findings = self._run(records)

        finding = findings[0]
        # Model-switch recommendation still present alongside the distillation flag.
        assert finding["suggested_model"] == "anthropic.claude-haiku-4-5-20251001-v1:0"
        assert finding["is_distillation_candidate"] is True
