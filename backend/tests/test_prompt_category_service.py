"""Unit tests for prompt_category_service.

classify_prompt / resolve_category are pure functions (no I/O, no DB), so
these tests exercise them directly against representative prompt text and
record dicts — same style as test_profile_tag_service.py. build_spend_breakdown
is also pure and tested against hand-built record lists.
"""

import pytest

from app.services.prompt_category_service import (
    GENERAL_QA,
    UNCATEGORIZED,
    build_spend_breakdown,
    classify_prompt,
    resolve_category,
)


# ---------------------------------------------------------------------------
# classify_prompt
# ---------------------------------------------------------------------------

class TestClassifyPrompt:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("SELECT * FROM users WHERE id = 1", "SQL / Database"),
            ("Write a function to parse this JSON and fix the bug", "Coding"),
            ("Load this CSV into a dataframe and compute the correlation", "Data Analysis"),
            ("Summarize this document, give me the key points", "Summarization"),
            ("Translate this paragraph into Spanish", "Translation"),
            ("Write a short story about a dragon", "Creative Writing"),
            ("Customer complaint about order #12345, needs a refund", "Customer Support"),
            ("Use tool_use to call the weather API with toolConfig set", "Agentic / Tool Use"),
            ("What is the capital of France?", GENERAL_QA),
            ("How do I reset my password?", GENERAL_QA),
        ],
    )
    def test_classifies_representative_prompts(self, text, expected):
        assert classify_prompt(text) == expected

    def test_blank_text_returns_none(self):
        assert classify_prompt("") is None
        assert classify_prompt("   ") is None
        assert classify_prompt(None) is None

    def test_long_no_keyword_match_returns_none(self):
        # Long, genuinely generic text with no domain vocabulary and more
        # than _GENERAL_QA_FALLBACK_MAX_WORDS words — left unclassified
        # rather than defaulted to General Q&A, so a real unrecognized
        # passage doesn't get mislabeled as a Q&A exchange.
        text = (
            "asdf qwer zxcv lorem ipsum dolor sit amet consectetur adipiscing "
            "elit sed do eiusmod tempor incididunt ut labore"
        )
        assert len(text.split()) > 15
        assert classify_prompt(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "ok thanks",
            "yes",
            "got it, appreciate it",
            "continue",
            "looks good to me",
            "The weather today is nice but I am not sure.",
            "My favorite color is blue and I like hiking on weekends.",
            "Please proceed with the next step as discussed.",
            "42",
            "ping",
            "hello",
        ],
    )
    def test_short_zero_signal_prompts_fall_back_to_general_qa(self, text):
        # Short conversational/filler text with no domain-specific keyword
        # match defaults to the General Q&A catch-all rather than falling
        # through to Uncategorized — it's still ordinary conversational
        # content, not truly uncategorizable.
        assert classify_prompt(text) == GENERAL_QA

    def test_sql_beats_general_qa_on_tie_break(self):
        # "what is" (General Q&A) + a SQL marker — SQL is more specific and
        # must win per _CATEGORY_ORDER even if scores were to tie.
        text = "what is wrong with this SELECT * FROM orders WHERE id = 5 query"
        assert classify_prompt(text) == "SQL / Database"

    def test_highest_scoring_category_wins_over_single_match(self):
        # Multiple coding signals should outscore a single incidental
        # General Q&A phrase.
        text = (
            "How do I write a function, import the right module, and fix "
            "this bug? Here is my stack trace and the code block: ```python\nx=1\n```"
        )
        assert classify_prompt(text) == "Coding"

    def test_case_insensitive_matching(self):
        assert classify_prompt("SELECT * FROM Users") == "SQL / Database"
        assert classify_prompt("select * from users") == "SQL / Database"


# ---------------------------------------------------------------------------
# resolve_category
# ---------------------------------------------------------------------------

_NO_MATCH_TEXT = (
    "asdf qwer zxcv lorem ipsum dolor sit amet consectetur adipiscing "
    "elit sed do eiusmod tempor incididunt ut labore"
)


class TestResolveCategory:
    def test_content_type_wins_over_application_tag(self):
        record = {
            "promptText": "SELECT * FROM claims WHERE status = 'open'",
            "application": "ClaimsProcessor",
        }
        assert resolve_category(record) == "SQL / Database"

    def test_falls_back_to_application_tag_when_no_content_match(self):
        record = {
            "promptText": _NO_MATCH_TEXT,
            "application": "ClaimsProcessor",
        }
        assert resolve_category(record) == "ClaimsProcessor"

    def test_falls_back_to_uncategorized_when_no_content_and_no_application(self):
        record = {"promptText": _NO_MATCH_TEXT, "application": "unknown"}
        assert resolve_category(record) == UNCATEGORIZED

    def test_uncategorized_when_no_prompt_text_and_no_application(self):
        record = {}
        assert resolve_category(record) == UNCATEGORIZED

    def test_application_tag_case_handling_for_unknown_sentinel(self):
        # "unknown" (any case) is the dashboard-wide sentinel for an
        # unresolved dimension — must not be treated as a real application.
        record = {"promptText": _NO_MATCH_TEXT, "application": "Unknown"}
        assert resolve_category(record) == UNCATEGORIZED

    def test_blank_application_falls_back_to_uncategorized(self):
        record = {"promptText": _NO_MATCH_TEXT, "application": ""}
        assert resolve_category(record) == UNCATEGORIZED

    def test_missing_application_key_falls_back_to_uncategorized(self):
        record = {"promptText": _NO_MATCH_TEXT}
        assert resolve_category(record) == UNCATEGORIZED

    def test_short_zero_signal_prompt_resolves_to_general_qa_even_with_application_tag(self):
        # Short conversational filler now classifies as General Q&A via the
        # fallback — content type still wins over the Application tag per
        # the confirmed priority order, it's just that "content type" now
        # includes this fallback case.
        record = {"promptText": "ok thanks", "application": "ClaimsProcessor"}
        assert resolve_category(record) == GENERAL_QA


# ---------------------------------------------------------------------------
# build_spend_breakdown
# ---------------------------------------------------------------------------

def _record(prompt_text, model_id, cost, application="unknown"):
    return {
        "promptText": prompt_text,
        "modelId": model_id,
        "costEstimate": cost,
        "application": application,
    }


class TestBuildSpendBreakdown:
    def test_basis_is_always_estimated(self):
        result = build_spend_breakdown([])
        assert result["basis"] == "estimated"

    def test_empty_records_yields_empty_breakdown(self):
        result = build_spend_breakdown([])
        assert result["byCategory"] == []
        assert result["otherCategoryCost"] == 0
        assert result["otherCategoryInvocationCount"] == 0
        assert result["byModel"] == []
        assert result["otherModelCost"] == 0
        assert result["otherModelInvocationCount"] == 0

    def test_groups_by_category_and_sums_cost(self):
        records = [
            _record("SELECT * FROM a", "model-a", 1.0),
            _record("SELECT * FROM b", "model-a", 2.0),
            _record("write a function to sort a list", "model-b", 0.5),
        ]
        result = build_spend_breakdown(records)
        by_category = {c["category"]: c for c in result["byCategory"]}
        assert by_category["SQL / Database"]["cost"] == pytest.approx(3.0)
        assert by_category["SQL / Database"]["invocationCount"] == 2
        assert by_category["Coding"]["cost"] == pytest.approx(0.5)
        assert by_category["Coding"]["invocationCount"] == 1

    def test_categories_sorted_descending_by_cost(self):
        records = [
            _record("write a function", "model-a", 1.0),
            _record("SELECT * FROM a", "model-a", 5.0),
        ]
        result = build_spend_breakdown(records)
        assert result["byCategory"][0]["category"] == "SQL / Database"
        assert result["byCategory"][1]["category"] == "Coding"

    def test_overflow_categories_collapse_into_other(self):
        # 3 distinct categories, top_categories=1 -> 1 kept, 2 collapsed.
        records = [
            _record("SELECT * FROM a", "model-a", 10.0),
            _record("write a function", "model-a", 5.0),
            _record("translate this into spanish", "model-a", 3.0),
        ]
        result = build_spend_breakdown(records, top_categories=1)
        assert len(result["byCategory"]) == 1
        assert result["byCategory"][0]["category"] == "SQL / Database"
        assert result["otherCategoryCost"] == pytest.approx(8.0)
        assert result["otherCategoryInvocationCount"] == 2

    def test_per_category_model_breakdown_and_overflow(self):
        records = [
            _record("SELECT * FROM a", "model-a", 5.0),
            _record("SELECT * FROM b", "model-b", 3.0),
            _record("SELECT * FROM c", "model-c", 1.0),
        ]
        result = build_spend_breakdown(records, top_models_per_category=2)
        sql_category = next(c for c in result["byCategory"] if c["category"] == "SQL / Database")
        assert len(sql_category["byModel"]) == 2
        assert sql_category["byModel"][0]["modelId"] == "model-a"
        assert sql_category["byModel"][1]["modelId"] == "model-b"
        assert sql_category["otherModelCost"] == pytest.approx(1.0)
        assert sql_category["otherModelInvocationCount"] == 1

    def test_top_level_model_breakdown_is_independent_of_category_truncation(self):
        # Even with top_categories=1 (collapsing most categories), the
        # top-level byModel must still reflect ALL records, not just the
        # kept category's records.
        records = [
            _record("SELECT * FROM a", "model-a", 10.0),
            _record("write a function", "model-b", 5.0),
        ]
        result = build_spend_breakdown(records, top_categories=1, top_models=10)
        model_ids = {m["modelId"] for m in result["byModel"]}
        assert model_ids == {"model-a", "model-b"}
        total_model_cost = sum(m["cost"] for m in result["byModel"])
        assert total_model_cost == pytest.approx(15.0)

    def test_sum_of_category_costs_plus_other_equals_total(self):
        records = [
            _record("SELECT * FROM a", "model-a", 10.0),
            _record("write a function", "model-b", 5.0),
            _record("translate into spanish", "model-c", 3.0),
            _record("write a poem", "model-d", 2.0),
        ]
        result = build_spend_breakdown(records, top_categories=2)
        total = sum(c["cost"] for c in result["byCategory"]) + result["otherCategoryCost"]
        assert total == pytest.approx(20.0)

    def test_null_cost_estimate_treated_as_zero(self):
        records = [_record("SELECT * FROM a", "model-a", None)]
        result = build_spend_breakdown(records)
        assert result["byCategory"][0]["cost"] == 0.0

    def test_application_tag_used_as_category_when_no_content_match(self):
        records = [
            _record(_NO_MATCH_TEXT, "model-a", 4.0, application="ClaimsProcessor"),
        ]
        result = build_spend_breakdown(records)
        assert result["byCategory"][0]["category"] == "ClaimsProcessor"
        assert result["byCategory"][0]["cost"] == pytest.approx(4.0)

    def test_uncategorized_is_a_normal_category_not_special_cased(self):
        records = [_record(_NO_MATCH_TEXT, "model-a", 1.0, application="unknown")]
        result = build_spend_breakdown(records)
        assert result["byCategory"][0]["category"] == UNCATEGORIZED
