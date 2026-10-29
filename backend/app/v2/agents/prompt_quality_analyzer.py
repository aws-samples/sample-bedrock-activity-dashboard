"""Prompt Quality Analyzer Agent — detects anti-patterns in Bedrock prompts.

Scans for vague instructions, missing context, overly long system prompts
(>4000 tokens), and repeated boilerplate. Computes per-user quality scores.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


logger = logging.getLogger(__name__)

try:
    from strands import Agent
    from strands.tools import tool as strands_tool
except ImportError:
    strands_tool = lambda fn: fn  # noqa: E731
    Agent = None

SYSTEM_PROMPT = (
    "You are a prompt quality analysis agent for the Bedrock Activity Dashboard. "
    "Your job is to scan Bedrock prompts for anti-patterns — vague instructions, "
    "missing context, overly long system prompts, and repeated boilerplate. "
    "Use scan_for_anti_patterns to detect issues, then compute_quality_score "
    "to generate per-user quality scores."
)

# Anti-pattern detection thresholds
OVERLY_LONG_TOKEN_THRESHOLD = 4000
VAGUE_INDICATORS = [
    re.compile(r"\bdo\s+(?:something|stuff|things)\b", re.IGNORECASE),
    re.compile(r"\bhelp\s+me\b", re.IGNORECASE),
    re.compile(r"\bjust\s+(?:do|make)\b", re.IGNORECASE),
    re.compile(r"^(?:hi|hello|hey)\s*$", re.IGNORECASE),
]
# Approximate tokens as word_count * 1.3
TOKEN_ESTIMATE_FACTOR = 1.3


@strands_tool
def scan_for_anti_patterns(prompts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan a list of prompts for quality anti-patterns.

    Each prompt dict should have: user_identity, text, record_id.
    Returns flagged prompts with anti_pattern_type and excerpt.
    """
    findings: list[dict[str, Any]] = []

    # Track seen prompts for boilerplate detection
    seen_texts: dict[str, int] = defaultdict(int)
    for p in prompts:
        text = p.get("text", "")
        # Normalize for boilerplate comparison
        normalized = text.strip().lower()[:200]
        seen_texts[normalized] += 1

    for p in prompts:
        text = p.get("text", "")
        user = p.get("user_identity", "unknown")
        record_id = p.get("record_id", "")

        if not text.strip():
            continue

        word_count = len(text.split())
        estimated_tokens = int(word_count * TOKEN_ESTIMATE_FACTOR)

        # Check: overly long system prompt
        if estimated_tokens > OVERLY_LONG_TOKEN_THRESHOLD:
            findings.append({
                "user_identity": user,
                "record_id": record_id,
                "anti_pattern_type": "overly_long",
                "improvement_suggestion": (
                    f"Prompt is ~{estimated_tokens} tokens. "
                    "Consider condensing to under 4000 tokens for better performance."
                ),
            })
            continue

        # Check: vague instructions
        for pattern in VAGUE_INDICATORS:
            if pattern.search(text):
                findings.append({
                    "user_identity": user,
                    "record_id": record_id,
                    "anti_pattern_type": "vague",
                    "improvement_suggestion": "Add specific context, constraints, and expected output format.",
                })
                break

        # Check: missing context (very short prompts)
        if word_count < 5 and estimated_tokens < 10:
            findings.append({
                "user_identity": user,
                "record_id": record_id,
                "anti_pattern_type": "missing_context",
                "improvement_suggestion": "Provide more context about the task, audience, and desired output.",
            })
            continue

        # Check: repeated boilerplate
        normalized = text.strip().lower()[:200]
        if seen_texts.get(normalized, 0) > 2:
            findings.append({
                "user_identity": user,
                "record_id": record_id,
                "anti_pattern_type": "boilerplate",
                "improvement_suggestion": "This prompt appears repeatedly. Consider creating a template or system prompt.",
            })

    return findings


@strands_tool
def compute_quality_score(user_identity: str) -> dict[str, Any]:
    """Compute a quality score for a specific user based on recent prompts.

    Score = round(100 * (1 - flagged/total)). Returns 100 if no prompts found.
    """
    from app.services.cloudtrail_service import CloudTrailService

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=1)

    ct = CloudTrailService()
    try:
        records = ct.get_activity_records(start, end)
    except Exception as exc:
        logger.warning("Failed to fetch records for quality scoring: %s", exc)
        return {"user_identity": user_identity, "quality_score": 100, "total_prompts": 0, "flagged_prompts": 0}

    user_prompts = [
        {"text": r.get("promptText", ""), "user_identity": user_identity, "record_id": r.get("id", "")}
        for r in records
        if r.get("userIdentity") == user_identity and r.get("promptText")
    ]

    total = len(user_prompts)
    if total == 0:
        return {"user_identity": user_identity, "quality_score": 100, "total_prompts": 0, "flagged_prompts": 0}

    flagged = scan_for_anti_patterns(user_prompts)
    flagged_count = len(flagged)
    score = round(100 * (1 - flagged_count / total))

    return {
        "user_identity": user_identity,
        "quality_score": max(0, min(100, score)),
        "total_prompts": total,
        "flagged_prompts": flagged_count,
    }


def create_agent():
    """Create and return a configured Prompt Quality Analyzer Strands Agent."""
    if Agent is None:
        return None
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[scan_for_anti_patterns, compute_quality_score],
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
