"""Prompt category classification for the Overview tab's spend-breakdown donuts.

Deterministic keyword/pattern classification — no LLM call, no added Bedrock
cost or latency. Same style as the existing deterministic agents
(``prompt_quality_analyzer.py``'s anti-pattern scan,
``model_cost_optimizer.py``'s simple-task detection): plain regex/substring
matching over prompt text, pure and unit-testable in isolation.

Category resolution order (content type always wins over the Application
tag — an application can originate many different kinds of prompts, so the
content of the prompt is the more meaningful grouping):

    1. Keyword content-classification on ``promptText``, if it scores a match
       -> "Coding", "SQL / Database", "General Q&A", etc.
    2. Else, if the record has a resolved Application tag (not "unknown")
       -> the tag value, e.g. "ClaimsProcessor", "SupportBot"
    3. Else (no prompt text, no application tag)
       -> "Uncategorized"
"""

import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
#
# Each category maps to a list of compiled, case-insensitive patterns. A
# prompt's score for a category is the number of DISTINCT patterns that
# match (not total occurrences), so one long repeated keyword can't out-rank
# a prompt that genuinely touches more of a category's vocabulary. The
# category with the highest score wins; ties are broken by _CATEGORY_ORDER
# (more specific categories listed first beat the General Q&A catch-all).

UNCATEGORIZED = "Uncategorized"
GENERAL_QA = "General Q&A"

_SQL_DATABASE = "SQL / Database"
_CODING = "Coding"
_DATA_ANALYSIS = "Data Analysis"
_SUMMARIZATION = "Summarization"
_TRANSLATION = "Translation"
_CREATIVE_WRITING = "Creative Writing"
_CUSTOMER_SUPPORT = "Customer Support"
_AGENTIC_TOOL_USE = "Agentic / Tool Use"

# Order matters only for tie-breaking (see _CATEGORY_ORDER below); the dict
# itself is unordered for matching purposes.
CATEGORY_KEYWORDS: dict[str, list[re.Pattern]] = {
    _SQL_DATABASE: [
        re.compile(r"\bselect\b.*\bfrom\b", re.IGNORECASE | re.DOTALL),
        re.compile(r"\binsert\s+into\b", re.IGNORECASE),
        re.compile(r"\bupdate\b.*\bset\b", re.IGNORECASE | re.DOTALL),
        re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
        re.compile(r"\bjoin\b", re.IGNORECASE),
        re.compile(r"\bwhere\s+clause\b", re.IGNORECASE),
        re.compile(r"\bsql\s+quer", re.IGNORECASE),
        re.compile(r"\bqueries?\b", re.IGNORECASE),
        re.compile(r"\bdatabase\s+schema\b", re.IGNORECASE),
        re.compile(r"\btable\s+schema\b", re.IGNORECASE),
        re.compile(r"\bgroup\s+by\b", re.IGNORECASE),
    ],
    _CODING: [
        re.compile(r"```"),
        re.compile(r"\bfunction\s*\(", re.IGNORECASE),
        re.compile(r"\bwrite\s+a\s+function\b", re.IGNORECASE),
        re.compile(r"\bclass\s+\w+", re.IGNORECASE),
        re.compile(r"\bdef\s+\w+\s*\(", re.IGNORECASE),
        re.compile(r"\bimport\s+\w+", re.IGNORECASE),
        re.compile(r"\bfix\s+(?:this\s+|a\s+|the\s+)?bug\b", re.IGNORECASE),
        re.compile(r"\brefactor\b", re.IGNORECASE),
        re.compile(r"\bstack\s*trace\b", re.IGNORECASE),
        re.compile(r"\bcompile(?:r|\s+error)\b", re.IGNORECASE),
        re.compile(r"\bunit\s+test", re.IGNORECASE),
    ],
    _DATA_ANALYSIS: [
        re.compile(r"\bcsv\b", re.IGNORECASE),
        re.compile(r"\bdataframe\b", re.IGNORECASE),
        re.compile(r"\baggregate\b", re.IGNORECASE),
        re.compile(r"\bpivot\s+table\b", re.IGNORECASE),
        re.compile(r"\bplot\s+(?:a|the)\s+chart\b", re.IGNORECASE),
        re.compile(r"\bstatistic(?:s|al)\b", re.IGNORECASE),
        re.compile(r"\bcorrelation\b", re.IGNORECASE),
        re.compile(r"\bvisuali[sz]e\s+(?:this\s+)?data\b", re.IGNORECASE),
    ],
    _SUMMARIZATION: [
        re.compile(r"\bsummari[sz]e\b", re.IGNORECASE),
        re.compile(r"\btl;?dr\b", re.IGNORECASE),
        re.compile(r"\bkey\s+points\b", re.IGNORECASE),
        re.compile(r"\bshorten\s+this\b", re.IGNORECASE),
        re.compile(r"\bcondense\b", re.IGNORECASE),
        re.compile(r"\bbrief\s+overview\b", re.IGNORECASE),
    ],
    _TRANSLATION: [
        re.compile(r"\btranslate\b", re.IGNORECASE),
        re.compile(r"\btranslation\s+(?:of|for)\b", re.IGNORECASE),
        re.compile(r"\bfrom\s+\w+\s+to\s+\w+\b.*\blanguage\b", re.IGNORECASE),
        re.compile(r"\binto\s+(?:spanish|french|german|japanese|chinese|portuguese|italian|korean)\b", re.IGNORECASE),
    ],
    _CREATIVE_WRITING: [
        re.compile(r"\bwrite\s+a\s+(?:short\s+)?story\b", re.IGNORECASE),
        re.compile(r"\bwrite\s+a\s+poem\b", re.IGNORECASE),
        re.compile(r"\bdraft\s+an?\s+email\b", re.IGNORECASE),
        re.compile(r"\bblog\s+post\b", re.IGNORECASE),
        re.compile(r"\bcreative\s+writing\b", re.IGNORECASE),
        re.compile(r"\bwrite\s+a\s+(?:tagline|slogan|caption)\b", re.IGNORECASE),
    ],
    _CUSTOMER_SUPPORT: [
        re.compile(r"\bsupport\s+ticket\b", re.IGNORECASE),
        re.compile(r"\bcustomer\s+complaint\b", re.IGNORECASE),
        re.compile(r"\brefund\b", re.IGNORECASE),
        re.compile(r"\border\s*#", re.IGNORECASE),
        re.compile(r"\bcustomer\s+follow-?up\b", re.IGNORECASE),
        re.compile(r"\bescalat(?:e|ion)\b", re.IGNORECASE),
    ],
    _AGENTIC_TOOL_USE: [
        re.compile(r"\btool_use\b", re.IGNORECASE),
        re.compile(r"\btool\s*config\b", re.IGNORECASE),
        re.compile(r"\bfunction\s+call(?:ing)?\b", re.IGNORECASE),
        re.compile(r"\binvoke\s+the\s+tool\b", re.IGNORECASE),
        re.compile(r"\bagentic\s+workflow\b", re.IGNORECASE),
    ],
    GENERAL_QA: [
        re.compile(r"^(?:what|who|when|where|why|how)\b", re.IGNORECASE),
        re.compile(r"\bexplain\b", re.IGNORECASE),
        re.compile(r"\bcan\s+you\s+tell\s+me\b", re.IGNORECASE),
        # Short conversational acknowledgments / filler — "ok thanks", "yes",
        # "got it", "continue", "looks good to me", "ping", etc. These carry
        # no domain-specific vocabulary but are still ordinary conversational
        # turns, not truly uncategorizable content.
        re.compile(r"^(?:ok|okay)\b", re.IGNORECASE),
        re.compile(r"^(?:yes|yeah|yep|no|nope)\b", re.IGNORECASE),
        re.compile(r"\bthanks?\s+you\b|\bthank\s+you\b|^thanks\b", re.IGNORECASE),
        re.compile(r"\bgot\s+it\b", re.IGNORECASE),
        re.compile(r"\b(?:sounds|looks)\s+good\b", re.IGNORECASE),
        re.compile(r"\bappreciate\s+it\b", re.IGNORECASE),
        re.compile(r"^continue\b", re.IGNORECASE),
        re.compile(r"^(?:hi|hello|hey)\b", re.IGNORECASE),
        re.compile(r"^ping$", re.IGNORECASE),
        re.compile(r"\bplease\s+proceed\b", re.IGNORECASE),
        re.compile(r"\bnext\s+step\b", re.IGNORECASE),
    ],
}

# A prompt that scores no category match at all is treated as General Q&A
# (rather than falling through to the Application tag / Uncategorized) when
# it's short — short, low-signal text ("The weather is nice today", "My
# favorite color is blue") is ordinary conversational content, not truly
# uncategorizable. Longer zero-signal text is left unclassified so a genuine
# zero-signal failure (e.g. unrecognized domain content) still falls through
# to the Application tag / "Uncategorized", rather than every long passage
# with no keyword hit being mislabeled as a question-answer exchange.
_GENERAL_QA_FALLBACK_MAX_WORDS = 15

# Tie-break order: earlier entries win over later ones when scores are equal.
# Specific categories beat the General Q&A catch-all.
_CATEGORY_ORDER: list[str] = [
    _SQL_DATABASE,
    _CODING,
    _DATA_ANALYSIS,
    _AGENTIC_TOOL_USE,
    _TRANSLATION,
    _SUMMARIZATION,
    _CREATIVE_WRITING,
    _CUSTOMER_SUPPORT,
    GENERAL_QA,
]


def classify_prompt(text: Optional[str]) -> Optional[str]:
    """Classify prompt text into a content-type category.

    Returns the highest-scoring category name, or ``None`` when the text is
    blank or no keyword pattern matches anything (callers then fall through
    to the Application-tag / "Uncategorized" steps of the resolution order
    — see :func:`resolve_category`).

    Scoring: for each category, the score is the count of DISTINCT patterns
    that match at least once (not total match occurrences), so a single
    repeated keyword can't out-rank a prompt that genuinely touches more of
    a category's vocabulary. The highest-scoring category wins; ties are
    broken by ``_CATEGORY_ORDER`` (more specific categories beat the General
    Q&A catch-all).
    """
    if not text or not text.strip():
        return None

    scores: dict[str, int] = {}
    for category, patterns in CATEGORY_KEYWORDS.items():
        score = sum(1 for pattern in patterns if pattern.search(text))
        if score > 0:
            scores[category] = score

    if not scores:
        # No keyword/pattern matched anything. Short text (acknowledgments,
        # filler, one-off remarks like "42" or a generic sentence with no
        # domain vocabulary) is still ordinary conversational content, so it
        # defaults to the General Q&A catch-all rather than falling through
        # to the Application tag / "Uncategorized". Longer zero-signal text
        # is left unclassified (returns None) so a genuinely unrecognized
        # passage of real content doesn't get mislabeled as a Q&A exchange.
        if len(text.split()) <= _GENERAL_QA_FALLBACK_MAX_WORDS:
            return GENERAL_QA
        return None

    best_score = max(scores.values())
    best_categories = [c for c, s in scores.items() if s == best_score]
    if len(best_categories) == 1:
        return best_categories[0]

    # Tie-break: earliest in _CATEGORY_ORDER wins.
    for category in _CATEGORY_ORDER:
        if category in best_categories:
            return category
    # Defensive fallback (should be unreachable since every taxonomy key is
    # listed in _CATEGORY_ORDER): pick deterministically rather than relying
    # on dict iteration order.
    return sorted(best_categories)[0]


def resolve_category(record: dict[str, Any]) -> str:
    """Resolve the final category for an activity record (full 3-step order).

    1. Content-type classification on ``promptText`` (wins when it matches).
    2. The record's resolved ``application`` tag, when present and not the
       "unknown" sentinel used elsewhere in this app for an untagged/
       unresolved dimension.
    3. ``"Uncategorized"`` otherwise.
    """
    content_category = classify_prompt(record.get("promptText"))
    if content_category is not None:
        return content_category

    application = record.get("application")
    if application and application.strip().lower() != "unknown":
        return application

    return UNCATEGORIZED


# ---------------------------------------------------------------------------
# Aggregation for the Overview tab's spend-breakdown donuts
# ---------------------------------------------------------------------------
#
# Powers GET /api/activity/categories. Deliberately kept here (rather than in
# AggregationService) since it depends on resolve_category(), which is this
# module's concern. Pure functions, no I/O.

_DEFAULT_TOP_CATEGORIES = 6
_DEFAULT_TOP_MODELS_PER_CATEGORY = 4
_DEFAULT_TOP_MODELS = 6

_OTHER_CATEGORY_LABEL = "Other"
_OTHER_MODEL_LABEL = "Other"


def _record_cost(record: dict[str, Any]) -> float:
    """Return a record's cost estimate, treating missing/None as 0.0."""
    value = record.get("costEstimate")
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _model_breakdown(
    records: list[dict[str, Any]], top_n: int
) -> tuple[list[dict[str, Any]], float, int]:
    """Group ``records`` by ``modelId``, returning (top rows, other cost, other count).

    Rows are sorted by cost descending; everything beyond ``top_n`` is
    collapsed into the returned other-cost/other-count pair rather than a
    row in the list (callers decide whether/how to render an "Other" slice).
    """
    totals: dict[str, dict[str, Any]] = {}
    for record in records:
        model_id = record.get("modelId") or "unknown"
        bucket = totals.setdefault(model_id, {"modelId": model_id, "cost": 0.0, "invocationCount": 0})
        bucket["cost"] += _record_cost(record)
        bucket["invocationCount"] += 1

    rows = sorted(totals.values(), key=lambda r: r["cost"], reverse=True)
    top_rows = rows[:top_n]
    overflow = rows[top_n:]
    other_cost = sum(r["cost"] for r in overflow)
    other_count = sum(r["invocationCount"] for r in overflow)
    return top_rows, other_cost, other_count


def build_spend_breakdown(
    records: list[dict[str, Any]],
    top_categories: int = _DEFAULT_TOP_CATEGORIES,
    top_models_per_category: int = _DEFAULT_TOP_MODELS_PER_CATEGORY,
    top_models: int = _DEFAULT_TOP_MODELS,
) -> dict[str, Any]:
    """Build the full response shape for GET /api/activity/categories.

    Each record resolves to exactly one category via :func:`resolve_category`
    and is counted in exactly one place in both ``byCategory`` and the
    top-level ``byModel`` — the two breakdowns are independent views of the
    SAME record set, not nested derivations of each other.
    """
    by_category_records: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        category = resolve_category(record)
        by_category_records.setdefault(category, []).append(record)

    category_totals = [
        {
            "category": category,
            "cost": sum(_record_cost(r) for r in cat_records),
            "invocationCount": len(cat_records),
        }
        for category, cat_records in by_category_records.items()
    ]
    category_totals.sort(key=lambda c: c["cost"], reverse=True)

    top_category_rows = category_totals[:top_categories]
    overflow_categories = category_totals[top_categories:]
    other_category_cost = sum(c["cost"] for c in overflow_categories)
    other_category_count = sum(c["invocationCount"] for c in overflow_categories)

    by_category: list[dict[str, Any]] = []
    for cat_row in top_category_rows:
        cat_records = by_category_records[cat_row["category"]]
        model_rows, other_model_cost, other_model_count = _model_breakdown(
            cat_records, top_models_per_category
        )
        by_category.append({
            "category": cat_row["category"],
            "cost": cat_row["cost"],
            "invocationCount": cat_row["invocationCount"],
            "byModel": model_rows,
            "otherModelCost": other_model_cost,
            "otherModelInvocationCount": other_model_count,
        })

    top_model_rows, other_model_cost, other_model_count = _model_breakdown(records, top_models)

    return {
        "basis": "estimated",
        "byCategory": by_category,
        "otherCategoryCost": other_category_cost,
        "otherCategoryInvocationCount": other_category_count,
        "byModel": top_model_rows,
        "otherModelCost": other_model_cost,
        "otherModelInvocationCount": other_model_count,
    }
