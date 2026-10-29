"""Unit tests for AggregationService."""

import pytest

from app.services.aggregation_service import AggregationService


def _make_record(
    user_identity: str = "arn:aws:iam::123456789012:user/alice",
    user_name: str = "alice",
    model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    source_application: str = "Kiro",
    timestamp: str = "2024-01-15T10:30:00+00:00",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_estimate: float | None = None,
) -> dict:
    total = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
    return {
        "id": "test-id",
        "timestamp": timestamp,
        "userIdentity": user_identity,
        "userName": user_name,
        "modelId": model_id,
        "sourceApplication": source_application,
        "sourceIp": "10.0.0.1",
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read_tokens,
        "cacheWriteTokens": cache_write_tokens,
        "totalTokens": total,
        "costEstimate": cost_estimate,
    }


@pytest.fixture
def svc():
    return AggregationService()


@pytest.fixture
def sample_records():
    return [
        _make_record(
            user_identity="user-a", user_name="Alice",
            model_id="model-1", source_application="Kiro",
            timestamp="2024-01-15T10:00:00+00:00",
            input_tokens=100, output_tokens=50, cost_estimate=0.5,
        ),
        _make_record(
            user_identity="user-a", user_name="Alice",
            model_id="model-2", source_application="Q Developer",
            timestamp="2024-01-15T11:00:00+00:00",
            input_tokens=200, output_tokens=100, cost_estimate=1.0,
        ),
        _make_record(
            user_identity="user-b", user_name="Bob",
            model_id="model-1", source_application="Kiro",
            timestamp="2024-01-16T09:00:00+00:00",
            input_tokens=300, output_tokens=150, cost_estimate=1.5,
        ),
    ]


class TestAggregateByUser:
    def test_groups_by_user_identity(self, svc, sample_records):
        result = svc.aggregate_by_user(sample_records)
        assert len(result) == 2
        identities = {r["userIdentity"] for r in result}
        assert identities == {"user-a", "user-b"}

    def test_sums_tokens_per_user(self, svc, sample_records):
        result = svc.aggregate_by_user(sample_records)
        alice = next(r for r in result if r["userIdentity"] == "user-a")
        assert alice["inputTokens"] == 300
        assert alice["outputTokens"] == 150
        assert alice["totalTokens"] == 450
        assert alice["invocationCount"] == 2

    def test_sums_cost_per_user(self, svc, sample_records):
        result = svc.aggregate_by_user(sample_records)
        alice = next(r for r in result if r["userIdentity"] == "user-a")
        assert alice["costEstimate"] == pytest.approx(1.5)

    def test_sorted_descending_by_total_tokens(self, svc):
        records = [
            _make_record(user_identity="u-low", user_name="Low", input_tokens=10, output_tokens=5),
            _make_record(user_identity="u-high", user_name="High", input_tokens=500, output_tokens=200),
        ]
        result = svc.aggregate_by_user(records)
        assert result[0]["userIdentity"] == "u-high"
        assert result[1]["userIdentity"] == "u-low"

    def test_empty_records(self, svc):
        assert svc.aggregate_by_user([]) == []

    def test_null_cost_stays_null(self, svc):
        records = [_make_record(cost_estimate=None)]
        result = svc.aggregate_by_user(records)
        assert result[0]["costEstimate"] is None

    def test_mixed_null_and_non_null_cost(self, svc):
        records = [
            _make_record(user_identity="u1", cost_estimate=None),
            _make_record(user_identity="u1", cost_estimate=2.0),
        ]
        result = svc.aggregate_by_user(records)
        assert result[0]["costEstimate"] == pytest.approx(2.0)


class TestAggregateByModel:
    def test_groups_by_model_id(self, svc, sample_records):
        result = svc.aggregate_by_model(sample_records)
        assert len(result) == 2
        model_ids = {r["modelId"] for r in result}
        assert model_ids == {"model-1", "model-2"}

    def test_computes_percentage_of_total(self, svc, sample_records):
        result = svc.aggregate_by_model(sample_records)
        # model-1 has 2 invocations, model-2 has 1 → 66.67% and 33.33%
        model1 = next(r for r in result if r["modelId"] == "model-1")
        model2 = next(r for r in result if r["modelId"] == "model-2")
        assert model1["percentageOfTotal"] == pytest.approx(66.6667, rel=1e-2)
        assert model2["percentageOfTotal"] == pytest.approx(33.3333, rel=1e-2)

    def test_sorted_by_invocation_count_desc(self, svc, sample_records):
        result = svc.aggregate_by_model(sample_records)
        assert result[0]["modelId"] == "model-1"

    def test_empty_records(self, svc):
        assert svc.aggregate_by_model([]) == []


class TestAggregateBySource:
    def test_groups_by_source_application(self, svc, sample_records):
        result = svc.aggregate_by_source(sample_records)
        assert len(result) == 2
        sources = {r["sourceApplication"] for r in result}
        assert sources == {"Kiro", "Q Developer"}

    def test_sums_tokens_per_source(self, svc, sample_records):
        result = svc.aggregate_by_source(sample_records)
        kiro = next(r for r in result if r["sourceApplication"] == "Kiro")
        assert kiro["invocationCount"] == 2
        assert kiro["inputTokens"] == 400

    def test_empty_records(self, svc):
        assert svc.aggregate_by_source([]) == []


class TestAggregateByDepartment:
    def test_groups_by_department(self, svc):
        records = [
            {**_make_record(user_identity="u1"), "department": "Finance"},
            {**_make_record(user_identity="u2"), "department": "Finance"},
            {**_make_record(user_identity="u3"), "department": "Engineering"},
        ]
        result = svc.aggregate_by_department(records)
        assert len(result) == 2
        depts = {r["department"] for r in result}
        assert depts == {"Finance", "Engineering"}

    def test_defaults_missing_department_to_unknown(self, svc):
        records = [_make_record()]  # no department field at all
        result = svc.aggregate_by_department(records)
        assert result[0]["department"] == "unknown"

    def test_treats_blank_department_as_unknown(self, svc):
        records = [{**_make_record(), "department": ""}]
        result = svc.aggregate_by_department(records)
        assert result[0]["department"] == "unknown"

    def test_sums_tokens_and_cost_per_department(self, svc):
        records = [
            {**_make_record(input_tokens=100, output_tokens=50, cost_estimate=1.0), "department": "Finance"},
            {**_make_record(input_tokens=200, output_tokens=100, cost_estimate=2.0), "department": "Finance"},
        ]
        result = svc.aggregate_by_department(records)
        finance = next(r for r in result if r["department"] == "Finance")
        assert finance["inputTokens"] == 300
        assert finance["invocationCount"] == 2
        assert finance["costEstimate"] == pytest.approx(3.0)

    def test_sorted_descending_by_invocation_count(self, svc):
        records = [
            {**_make_record(), "department": "Low"},
            {**_make_record(), "department": "High"},
            {**_make_record(), "department": "High"},
        ]
        result = svc.aggregate_by_department(records)
        assert result[0]["department"] == "High"

    def test_empty_records(self, svc):
        assert svc.aggregate_by_department([]) == []


class TestAggregateByAgentName:
    def test_groups_by_agent_name(self, svc):
        records = [
            {**_make_record(user_identity="u1"), "agentName": "PatientOutreachAgent"},
            {**_make_record(user_identity="u2"), "agentName": "PaymentPlanAgent"},
        ]
        result = svc.aggregate_by_agent_name(records)
        assert len(result) == 2
        names = {r["agentName"] for r in result}
        assert names == {"PatientOutreachAgent", "PaymentPlanAgent"}

    def test_defaults_missing_agent_name_to_unknown(self, svc):
        records = [_make_record()]
        result = svc.aggregate_by_agent_name(records)
        assert result[0]["agentName"] == "unknown"

    def test_sums_invocation_count_per_agent(self, svc):
        records = [
            {**_make_record(), "agentName": "AgentA"},
            {**_make_record(), "agentName": "AgentA"},
            {**_make_record(), "agentName": "AgentB"},
        ]
        result = svc.aggregate_by_agent_name(records)
        agent_a = next(r for r in result if r["agentName"] == "AgentA")
        assert agent_a["invocationCount"] == 2

    def test_empty_records(self, svc):
        assert svc.aggregate_by_agent_name([]) == []


class TestAggregateByDate:
    def test_groups_by_date(self, svc, sample_records):
        result = svc.aggregate_by_date(sample_records)
        assert len(result) == 2
        dates = [r["date"] for r in result]
        assert dates == ["2024-01-15", "2024-01-16"]

    def test_sorted_ascending_by_date(self, svc, sample_records):
        result = svc.aggregate_by_date(sample_records)
        assert result[0]["date"] < result[1]["date"]

    def test_sums_tokens_per_date(self, svc, sample_records):
        result = svc.aggregate_by_date(sample_records)
        jan15 = next(r for r in result if r["date"] == "2024-01-15")
        assert jan15["invocationCount"] == 2
        assert jan15["inputTokens"] == 300

    def test_empty_records(self, svc):
        assert svc.aggregate_by_date([]) == []


class TestAggregateByTeam:
    def test_groups_by_team(self, svc, sample_records):
        team_map = {
            "Team Alpha": ["user-a"],
            "Team Beta": ["user-b"],
        }
        result = svc.aggregate_by_team(sample_records, team_map)
        assert len(result) == 2
        names = {r["teamName"] for r in result}
        assert names == {"Team Alpha", "Team Beta"}

    def test_sums_tokens_for_team_members(self, svc, sample_records):
        team_map = {"Team Alpha": ["user-a", "user-b"]}
        result = svc.aggregate_by_team(sample_records, team_map)
        assert result[0]["inputTokens"] == 600
        assert result[0]["invocationCount"] == 3

    def test_user_in_multiple_teams(self, svc, sample_records):
        team_map = {
            "Team A": ["user-a"],
            "Team B": ["user-a", "user-b"],
        }
        result = svc.aggregate_by_team(sample_records, team_map)
        team_b = next(r for r in result if r["teamName"] == "Team B")
        assert team_b["invocationCount"] == 3

    def test_team_with_no_matching_records(self, svc, sample_records):
        team_map = {"Empty Team": ["user-z"]}
        result = svc.aggregate_by_team(sample_records, team_map)
        assert result[0]["invocationCount"] == 0
        assert result[0]["memberCount"] == 1

    def test_empty_records(self, svc):
        result = svc.aggregate_by_team([], {"T": ["u1"]})
        assert len(result) == 1
        assert result[0]["invocationCount"] == 0

    def test_empty_team_map(self, svc, sample_records):
        assert svc.aggregate_by_team(sample_records, {}) == []


class TestComputeUserModelMatrix:
    def test_returns_one_entry_per_user_model_pair(self, svc, sample_records):
        result = svc.compute_user_model_matrix(sample_records)
        # user-a/model-1, user-a/model-2, user-b/model-1
        assert len(result) == 3

    def test_counts_invocations_per_pair(self, svc):
        records = [
            _make_record(user_identity="u1", model_id="m1"),
            _make_record(user_identity="u1", model_id="m1"),
            _make_record(user_identity="u1", model_id="m2"),
        ]
        result = svc.compute_user_model_matrix(records)
        u1_m1 = next(
            r for r in result
            if r["userIdentity"] == "u1" and r["modelId"] == "m1"
        )
        assert u1_m1["invocationCount"] == 2

    def test_sums_cost_per_pair(self, svc):
        records = [
            _make_record(user_identity="u1", model_id="m1", cost_estimate=1.0),
            _make_record(user_identity="u1", model_id="m1", cost_estimate=2.0),
        ]
        result = svc.compute_user_model_matrix(records)
        assert result[0]["costEstimate"] == pytest.approx(3.0)

    def test_null_cost_stays_null(self, svc):
        records = [_make_record(user_identity="u1", model_id="m1", cost_estimate=None)]
        result = svc.compute_user_model_matrix(records)
        assert result[0]["costEstimate"] is None

    def test_empty_records(self, svc):
        assert svc.compute_user_model_matrix([]) == []


class TestComputeOverview:
    def test_computes_totals(self, svc, sample_records):
        result = svc.compute_overview(sample_records)
        assert result["totalInvocations"] == 3
        assert result["totalInputTokens"] == 600
        assert result["totalOutputTokens"] == 300
        assert result["totalTokens"] == 900
        assert result["totalCostEstimate"] == pytest.approx(3.0)

    def test_counts_active_users_and_models(self, svc, sample_records):
        result = svc.compute_overview(sample_records)
        assert result["activeUsers"] == 2
        assert result["activeModels"] == 2

    def test_date_range_from_records(self, svc, sample_records):
        result = svc.compute_overview(sample_records)
        assert result["dateRange"]["start"] == "2024-01-15T10:00:00+00:00"
        assert result["dateRange"]["end"] == "2024-01-16T09:00:00+00:00"

    def test_empty_records(self, svc):
        result = svc.compute_overview([])
        assert result["totalInvocations"] == 0
        assert result["totalTokens"] == 0
        assert result["totalCostEstimate"] is None
        assert result["activeUsers"] == 0
        assert result["activeModels"] == 0
        assert result["dateRange"] == {"start": "", "end": ""}

    def test_all_null_costs(self, svc):
        records = [_make_record(cost_estimate=None), _make_record(cost_estimate=None)]
        result = svc.compute_overview(records)
        assert result["totalCostEstimate"] is None


class TestComputePercentages:
    def test_basic_percentages(self):
        result = AggregationService.compute_percentages([50, 30, 20])
        assert result == [50.0, 30.0, 20.0]

    def test_percentages_sum_to_100(self):
        result = AggregationService.compute_percentages([1, 2, 3])
        assert sum(result) == pytest.approx(100.0)

    def test_single_value(self):
        result = AggregationService.compute_percentages([42])
        assert result == [100.0]

    def test_all_zeros(self):
        result = AggregationService.compute_percentages([0, 0, 0])
        assert result == [0.0, 0.0, 0.0]

    def test_empty_list(self):
        result = AggregationService.compute_percentages([])
        assert result == []

    def test_equal_values(self):
        result = AggregationService.compute_percentages([10, 10, 10, 10])
        assert all(v == pytest.approx(25.0) for v in result)
