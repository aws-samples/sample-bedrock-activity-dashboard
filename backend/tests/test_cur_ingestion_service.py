"""Unit tests for CurIngestionService query construction (schema tolerance).

The CUR activated-tag columns (resource_tags_user_team / _application /
_environment) only exist in the Glue table once cost-allocation tags are
activated. _build_query must probe the table and substitute empty-string
literals for missing tag columns while keeping the output column ORDER
unchanged (parse_result_rows depends on positions).
"""

import re
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.services.cur_ingestion_service import CurIngestionService

ALL_TAG_COLUMNS = [
    "resource_tags_user_team",
    "resource_tags_user_application",
    "resource_tags_user_environment",
]

BASE_COLUMNS = [
    "line_item_iam_principal",
    "line_item_resource_id",
    "line_item_usage_start_date",
    "line_item_line_item_type",
    "line_item_unblended_cost",
    "line_item_product_code",
]

# Output aliases in the exact positional order parse_result_rows expects.
EXPECTED_ALIAS_ORDER = [
    "iam_principal",
    "team_tag",
    "resource_id",
    "usage_date",
    "model_id",
    "application",
    "environment",
    "billed_cost",
]


def _service() -> CurIngestionService:
    return CurIngestionService(
        settings=Settings(
            athena_database="cur_db",
            athena_table="data",
            athena_output_s3="s3://results/prefix/",
        )
    )


def _mock_glue(column_names: list[str]):
    """Patch boto3.client to return a Glue client serving the given columns."""
    glue = MagicMock()
    glue.get_table.return_value = {
        "Table": {
            "StorageDescriptor": {"Columns": [{"Name": c} for c in column_names]},
            "PartitionKeys": [{"Name": "billing_period"}],
        }
    }
    return patch("boto3.client", return_value=glue), glue


def _alias_order(query: str) -> list[str]:
    """Extract the projected aliases, in order, from the SELECT clause."""
    select_clause = query.split("FROM")[0]
    return re.findall(r"AS\s+(\w+)", select_clause)


class TestBuildQueryTagTolerance:
    def test_all_tag_columns_present_projects_real_columns(self):
        patcher, glue = _mock_glue(BASE_COLUMNS + ALL_TAG_COLUMNS)
        with patcher:
            query = _service()._build_query()

        for col in ALL_TAG_COLUMNS:
            assert col in query
        assert "'' AS" not in query
        assert _alias_order(query) == EXPECTED_ALIAS_ORDER
        # Present tag columns must be grouped.
        group_by = query.split("GROUP BY")[1]
        for col in ALL_TAG_COLUMNS:
            assert col in group_by

    def test_missing_tag_columns_substitute_empty_literals(self):
        patcher, glue = _mock_glue(BASE_COLUMNS)  # no tag columns
        with patcher:
            query = _service()._build_query()

        assert "'' AS team_tag" in query
        assert "'' AS application" in query
        assert "'' AS environment" in query
        for col in ALL_TAG_COLUMNS:
            assert col not in query
        # Output column order unchanged so parse_result_rows keeps working.
        assert _alias_order(query) == EXPECTED_ALIAS_ORDER
        # Constants must not appear in GROUP BY.
        group_by = query.split("GROUP BY")[1]
        assert "''" not in group_by

    def test_partial_tag_columns(self):
        patcher, glue = _mock_glue(BASE_COLUMNS + ["resource_tags_user_team"])
        with patcher:
            query = _service()._build_query()

        assert "resource_tags_user_team    " in query or "resource_tags_user_team AS" in query
        assert "'' AS application" in query
        assert "'' AS environment" in query
        assert _alias_order(query) == EXPECTED_ALIAS_ORDER

    def test_probe_failure_degrades_to_tagless_projection(self):
        glue = MagicMock()
        glue.get_table.side_effect = RuntimeError("access denied")
        with patch("boto3.client", return_value=glue):
            query = _service()._build_query()

        assert "'' AS team_tag" in query
        assert "'' AS application" in query
        assert "'' AS environment" in query
        assert _alias_order(query) == EXPECTED_ALIAS_ORDER

    def test_probe_is_cached_per_instance(self):
        patcher, glue = _mock_glue(BASE_COLUMNS + ALL_TAG_COLUMNS)
        with patcher:
            service = _service()
            service._build_query()
            service._build_query()

        assert glue.get_table.call_count == 1


class TestModelAttribution:
    """model_id must derive from the Bedrock resource ARN, never the line-item type.

    line_item_line_item_type holds CUR line-item types ("Usage", "Tax", ...), not a
    model. For Bedrock line items, line_item_resource_id carries the foundation-model
    or inference-profile ARN whose tail normalize_model_id strips to the model key.
    """

    def test_model_id_projects_resource_id_and_line_item_type_absent(self):
        patcher, _ = _mock_glue(BASE_COLUMNS + ALL_TAG_COLUMNS)
        with patcher:
            query = _service()._build_query()

        # Projection position 4 (model_id) must come from the resource id.
        assert re.search(r"line_item_resource_id\s+AS model_id", query)
        # The line-item type column must appear NOWHERE in the query.
        assert "line_item_line_item_type" not in query
        assert _alias_order(query) == EXPECTED_ALIAS_ORDER

    def test_parse_normalizes_foundation_model_arn_to_model_key(self):
        from app.services.cur_ingestion_service import parse_result_rows

        arn = "arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-pro-v1:0"
        rows = [
            {"Data": [{"VarCharValue": c} for c in EXPECTED_ALIAS_ORDER]},  # header
            {
                "Data": [
                    {"VarCharValue": "arn:aws:iam::123456789012:role/app-role"},
                    {"VarCharValue": "team-alpha"},
                    {"VarCharValue": arn},  # raw resource_id (profile-tag caching)
                    {"VarCharValue": "2024-05-31 00:00:00.000"},
                    {"VarCharValue": arn},  # model_id source (same resource ARN)
                    {"VarCharValue": ""},
                    {"VarCharValue": ""},
                    {"VarCharValue": "1.23"},
                ]
            },
        ]
        parsed = parse_result_rows(rows)
        assert len(parsed) == 1
        # Column 4 is normalized to the bare model key matching activity modelId.
        assert parsed[0]["model_id"] == "amazon.nova-pro-v1:0"
        # Column 2 stays the RAW resource ARN.
        assert parsed[0]["resource_id"] == arn
