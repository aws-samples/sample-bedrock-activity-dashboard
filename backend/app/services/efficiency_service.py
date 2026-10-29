"""Efficiency metrics service: cache-hit ratio and cost-per-outcome.

Both headline computations (:meth:`EfficiencyService.cache_hit_ratio` and
:meth:`EfficiencyService.cost_per_outcome`) are pure ``staticmethod``s with no
I/O so they can be exercised directly by the property-based tests (Properties
6-8). The grouping/derivation helpers wrap them over activity-record lists,
mirroring the dict-returning aggregation contract already used by
:class:`app.services.aggregation_service.AggregationService`.

All division guards return ``None`` ("not applicable" / "not available")
rather than raising, so a zero denominator never blows up a request.
"""

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WorkloadOutcomeOverride

# Valid dimensions for cache-hit grouping.
_CACHE_HIT_DIMENSIONS = ("model", "application")

# Record field that carries the resolved workload/application dimension. Falls
# back to the pre-enrichment ``sourceApplication`` label when enrichment data is
# absent (renders "unknown").
_UNKNOWN = "unknown"


class EfficiencyService:
    """Computes prompt-cache efficiency and unit-economics metrics."""

    # ------------------------------------------------------------------
    # Cache-hit ratio (Requirement 5) -- pure formula + guarded denominator
    # ------------------------------------------------------------------

    @staticmethod
    def cache_hit_ratio(input_tokens: int, cache_read_tokens: int) -> float | None:
        """Return ``cacheRead / (input + cacheRead)`` for a slice.

        Returns ``None`` when the denominator ``input_tokens + cache_read_tokens``
        is zero, so the metric is reported as "not applicable" rather than
        dividing by zero (Req 5.4). For non-negative token counts with a
        positive denominator the result always lies in ``[0, 1]`` (Req 5.1).

        Args:
            input_tokens: Non-cache input tokens for the slice.
            cache_read_tokens: Tokens served from the prompt cache.

        Returns:
            The cache-hit ratio in ``[0, 1]``, or ``None`` when no tokens.
        """
        denominator = input_tokens + cache_read_tokens
        if denominator == 0:
            return None
        return cache_read_tokens / denominator

    def cache_hit_by_dimension(
        self, records: list[dict[str, Any]], dim: str
    ) -> list[dict[str, Any]]:
        """Group cache-hit ratio by ``model`` or ``application`` (Req 5.2).

        Args:
            records: Activity records.
            dim: Grouping dimension, one of ``"model"`` or ``"application"``.

        Returns:
            One dict per group with the group key, summed ``inputTokens`` /
            ``cacheReadTokens``, and ``cacheHitRatio`` (``None`` when the group
            has no tokens). Sorted descending by token volume.

        Raises:
            ValueError: If ``dim`` is not a supported dimension.
        """
        if dim not in _CACHE_HIT_DIMENSIONS:
            raise ValueError(
                f"Unsupported cache-hit dimension {dim!r}; "
                f"expected one of {_CACHE_HIT_DIMENSIONS}."
            )

        key_field = "modelId" if dim == "model" else None
        groups: dict[str, dict[str, int]] = defaultdict(
            lambda: {"inputTokens": 0, "cacheReadTokens": 0}
        )
        for r in records:
            if dim == "model":
                key = r.get(key_field) or _UNKNOWN
            else:
                key = self._record_workload(r)
            groups[key]["inputTokens"] += r.get("inputTokens", 0)
            groups[key]["cacheReadTokens"] += r.get("cacheReadTokens", 0)

        result = [
            {
                dim: key,
                "inputTokens": totals["inputTokens"],
                "cacheReadTokens": totals["cacheReadTokens"],
                "cacheHitRatio": self.cache_hit_ratio(
                    totals["inputTokens"], totals["cacheReadTokens"]
                ),
            }
            for key, totals in groups.items()
        ]
        result.sort(
            key=lambda x: x["inputTokens"] + x["cacheReadTokens"], reverse=True
        )
        return result

    def cache_hit_trend(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cache-hit ratio per calendar day over the window (Req 5.3).

        Args:
            records: Activity records carrying ISO-8601 ``timestamp`` values.

        Returns:
            One dict per day (``YYYY-MM-DD``) with summed tokens and
            ``cacheHitRatio`` (``None`` on a zero denominator), sorted ascending
            by date.
        """
        groups: dict[str, dict[str, int]] = defaultdict(
            lambda: {"inputTokens": 0, "cacheReadTokens": 0}
        )
        for r in records:
            ts = r.get("timestamp", "")
            date_key = ts[:10] if len(ts) >= 10 else _UNKNOWN
            groups[date_key]["inputTokens"] += r.get("inputTokens", 0)
            groups[date_key]["cacheReadTokens"] += r.get("cacheReadTokens", 0)

        result = [
            {
                "date": date_key,
                "inputTokens": totals["inputTokens"],
                "cacheReadTokens": totals["cacheReadTokens"],
                "cacheHitRatio": self.cache_hit_ratio(
                    totals["inputTokens"], totals["cacheReadTokens"]
                ),
            }
            for date_key, totals in groups.items()
        ]
        result.sort(key=lambda x: x["date"])
        return result

    # ------------------------------------------------------------------
    # Cost-per-outcome (Requirement 6) -- pure formula + guarded denominator
    # ------------------------------------------------------------------

    @staticmethod
    def cost_per_outcome(cost: float, outcome_count: int | None) -> float | None:
        """Return ``cost / outcome_count`` for a workload (Req 6.1).

        Returns ``None`` ("not available") when ``outcome_count`` is ``None``
        (unknown) or zero, so the metric never divides by zero (Req 6.5).

        The ``cost`` numerator is supplied by the caller and is expected to be
        the **reconciled** slice cost when billed (CUR) data is available for the
        workload, and the token-based **estimate** otherwise; either way the
        surfaced figure is labeled an estimate (see
        :meth:`cost_per_outcome_row` and Req 6.4/6.6). This method itself is
        basis-agnostic -- it only performs the guarded division.

        Args:
            cost: The (reconciled or estimated) cost for the workload.
            outcome_count: The number of business outcomes, or ``None``.

        Returns:
            Cost per outcome, or ``None`` when the count is zero/unknown.
        """
        if outcome_count is None or outcome_count == 0:
            return None
        return cost / outcome_count

    def outcome_count(
        self,
        records: list[dict[str, Any]],
        workload: str,
        db: Session,
    ) -> int | None:
        """Resolve the outcome count for a workload (Req 6.2, 6.3).

        Resolution order:

        1. **Admin override** -- if a :class:`WorkloadOutcomeOverride` row exists
           for ``workload`` whose ``[period_start, period_end]`` overlaps the
           window spanned by the workload's records, its ``outcome_count`` is
           used verbatim.
        2. **Derived** -- otherwise the count is derived from activity data as
           the number of distinct agent-chat sessions plus the number of
           distinct workflow executions for the workload's records.

        Args:
            records: Activity records (any scope; filtered to the workload here).
            workload: The Application/workload key to count outcomes for.
            db: Database session used only for the admin-override lookup.

        Returns:
            The outcome count, or ``None`` when it cannot be determined
            (no matching records and no override), so callers guard the
            division in :meth:`cost_per_outcome`.
        """
        workload_records = [
            r for r in records if self._record_workload(r) == (workload or _UNKNOWN)
        ]

        override = self._lookup_override(workload, workload_records, db)
        if override is not None:
            return override

        if not workload_records:
            return None

        return self._derive_outcome_count(workload_records)

    def cost_per_outcome_row(
        self,
        records: list[dict[str, Any]],
        workload: str,
        db: Session,
        *,
        cost: float,
        basis: str,
    ) -> dict[str, Any]:
        """Build a labeled cost-per-outcome result for a workload (Req 6.4, 6.6).

        Args:
            records: Activity records used to derive the outcome count.
            workload: The Application/workload key.
            db: Database session for the admin-override lookup.
            cost: The workload's cost numerator. Callers pass the **reconciled**
                slice cost when billed (CUR) data is available for the workload,
                and the token-based **estimate** otherwise.
            basis: Which numerator was passed -- ``"reconciled"`` when anchored to
                billed data, ``"estimated"`` otherwise. Recorded on the result so
                the UI can show the estimated-only marker.

        Returns:
            A dict with ``workload``, ``outcomeCount``, ``costPerOutcome``
            (``None`` when the count is zero/unknown), ``basis``, and
            ``estimated`` -- always ``True`` because per-prompt/per-outcome cost
            is necessarily an estimate (Req 6.4).
        """
        count = self.outcome_count(records, workload, db)
        return {
            "workload": workload,
            "outcomeCount": count,
            "costPerOutcome": self.cost_per_outcome(cost, count),
            "basis": basis,
            "estimated": True,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _record_workload(record: dict[str, Any]) -> str:
        """Return the workload key for a record.

        Prefers the enriched ``application`` dimension; falls back to the
        pre-enrichment ``sourceApplication`` label, and finally ``"unknown"``
        so untagged usage still groups consistently (Req 4.5/4.6).
        """
        application = record.get("application")
        if application and application != _UNKNOWN:
            return application
        source = record.get("sourceApplication")
        if source:
            return source
        return _UNKNOWN

    def _lookup_override(
        self,
        workload: str,
        workload_records: list[dict[str, Any]],
        db: Session,
    ) -> int | None:
        """Return an admin-supplied outcome count for the workload/period, if any.

        Matches a :class:`WorkloadOutcomeOverride` for ``workload`` whose period
        overlaps the date window spanned by ``workload_records``. When no records
        exist, any override for the workload is accepted.
        """
        window = self._record_window(workload_records)
        stmt = select(WorkloadOutcomeOverride).where(
            WorkloadOutcomeOverride.application == workload
        )
        overrides = db.execute(stmt).scalars().all()
        if not overrides:
            return None

        if window is None:
            # No records to bound the window; use the override as-is.
            return overrides[0].outcome_count

        window_start, window_end = window
        for override in overrides:
            # Overlap test: [period_start, period_end] intersects the window.
            if override.period_start <= window_end and override.period_end >= window_start:
                return override.outcome_count
        return None

    @staticmethod
    def _record_window(records: list[dict[str, Any]]) -> tuple[date, date] | None:
        """Return the (min, max) calendar-date window spanned by records.

        Returns ``None`` when no record carries a parseable ISO date.
        """
        dates: list[date] = []
        for r in records:
            ts = r.get("timestamp", "")
            if len(ts) >= 10:
                try:
                    dates.append(date.fromisoformat(ts[:10]))
                except ValueError:
                    continue
        if not dates:
            return None
        return min(dates), max(dates)

    @staticmethod
    def _derive_outcome_count(records: list[dict[str, Any]]) -> int:
        """Derive an outcome count from activity data (Req 6.2).

        An outcome is a countable unit of delivered value:

        - **Agent-chat sessions** -- distinct ``sessionId`` among agent-chat
          records (identified by ``userIdentity == "agent-chat"`` or
          ``sourceApplication == "Agent Chat"``), falling back to the record
          ``id`` when no explicit session id is present (each paired
          user/assistant turn is one conversation record).
        - **Workflow executions** -- distinct ``workflowExecutionId`` /
          ``executionId`` among the remaining (non-chat) records, falling back to
          the record ``id`` when no explicit execution id is present.

        Returns the sum of distinct chat sessions and distinct workflow
        executions.
        """
        chat_sessions: set[str] = set()
        workflow_executions: set[str] = set()

        for r in records:
            if EfficiencyService._is_chat_record(r):
                session_id = r.get("sessionId") or r.get("id")
                if session_id is not None:
                    chat_sessions.add(str(session_id))
            else:
                execution_id = (
                    r.get("workflowExecutionId")
                    or r.get("executionId")
                    or r.get("id")
                )
                if execution_id is not None:
                    workflow_executions.add(str(execution_id))

        return len(chat_sessions) + len(workflow_executions)

    @staticmethod
    def _is_chat_record(record: dict[str, Any]) -> bool:
        """Return True when a record originates from the Agent Chat surface."""
        if record.get("userIdentity") == "agent-chat":
            return True
        source = record.get("sourceApplication", "")
        return source.strip().lower() == "agent chat"
