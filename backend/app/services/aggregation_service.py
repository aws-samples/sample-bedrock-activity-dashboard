"""Aggregation service for grouping and summarizing Bedrock activity records."""

from collections import defaultdict
from typing import Any

from app.services.filter_service import FilterService


class AggregationService:
    """Groups activity records by various dimensions and computes totals."""

    _TOKEN_FIELDS = (
        "inputTokens",
        "outputTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
    )

    @staticmethod
    def _empty_totals() -> dict[str, Any]:
        return {
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
            "totalTokens": 0,
            "costEstimate": None,
            "reconciledCost": None,
            "invocationCount": 0,
        }

    @staticmethod
    def _accumulate(totals: dict[str, Any], record: dict[str, Any]) -> None:
        """Add a single record's values into a running totals dict."""
        for field in AggregationService._TOKEN_FIELDS:
            totals[field] += record.get(field, 0)
        totals["totalTokens"] += record.get("totalTokens", 0)
        totals["invocationCount"] += 1

        cost = record.get("costEstimate")
        if cost is not None:
            if totals["costEstimate"] is None:
                totals["costEstimate"] = 0.0
            totals["costEstimate"] += cost

        reconciled = record.get("reconciledCost")
        if reconciled is not None:
            if totals.get("reconciledCost") is None:
                totals["reconciledCost"] = 0.0
            totals["reconciledCost"] += reconciled

    # ------------------------------------------------------------------
    # Public aggregation methods
    # ------------------------------------------------------------------

    def aggregate_by_user(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate records per user identity.

        Returns a list of dicts with user info and token/cost totals,
        sorted descending by totalTokens.
        """
        groups: dict[str, dict[str, Any]] = {}
        for r in records:
            key = r.get("userIdentity", "unknown")
            if key not in groups:
                groups[key] = {
                    "userIdentity": key,
                    "userName": r.get("userName", key),
                    **self._empty_totals(),
                }
            self._accumulate(groups[key], r)

        result = list(groups.values())
        result.sort(key=lambda x: x["totalTokens"], reverse=True)
        return result

    def aggregate_by_model(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate records per model ID.

        Returns a list of dicts with model info, token/cost totals, and
        percentageOfTotal (invocation-based).
        """
        groups: dict[str, dict[str, Any]] = {}
        for r in records:
            key = r.get("modelId", "unknown")
            if key not in groups:
                groups[key] = {"modelId": key, **self._empty_totals()}
            self._accumulate(groups[key], r)

        result = list(groups.values())
        total_invocations = sum(g["invocationCount"] for g in result)
        for g in result:
            g["percentageOfTotal"] = (
                (g["invocationCount"] / total_invocations * 100.0)
                if total_invocations > 0
                else 0.0
            )

        result.sort(key=lambda x: x["invocationCount"], reverse=True)
        return result

    def aggregate_by_source(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate records per source application."""
        groups: dict[str, dict[str, Any]] = {}
        for r in records:
            key = r.get("sourceApplication", "Unknown")
            if key not in groups:
                groups[key] = {"sourceApplication": key, **self._empty_totals()}
            self._accumulate(groups[key], r)

        result = list(groups.values())
        result.sort(key=lambda x: x["invocationCount"], reverse=True)
        return result

    def aggregate_by_environment(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Aggregate records per environment dimension.

        Keyed on the additive ``environment`` record field (default
        ``"unknown"`` when absent). Mirrors the other ``aggregate_by_*``
        shape and is sorted descending by invocationCount.
        """
        groups: dict[str, dict[str, Any]] = {}
        for r in records:
            key = r.get("environment") or "unknown"
            if key not in groups:
                groups[key] = {"environment": key, **self._empty_totals()}
            self._accumulate(groups[key], r)

        result = list(groups.values())
        result.sort(key=lambda x: x["invocationCount"], reverse=True)
        return result

    def aggregate_by_application(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Aggregate records per application/workload dimension.

        Keyed on the additive ``application`` record field (default
        ``"unknown"`` when absent). Mirrors the other ``aggregate_by_*``
        shape and is sorted descending by invocationCount.
        """
        groups: dict[str, dict[str, Any]] = {}
        for r in records:
            key = r.get("application") or "unknown"
            if key not in groups:
                groups[key] = {"application": key, **self._empty_totals()}
            self._accumulate(groups[key], r)

        result = list(groups.values())
        result.sort(key=lambda x: x["invocationCount"], reverse=True)
        return result

    def aggregate_by_department(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Aggregate records per department dimension (executive rollup L2).

        Keyed on the additive ``department`` record field (default
        ``"unknown"`` when absent/untagged). Mirrors the other
        ``aggregate_by_*`` shape and is sorted descending by invocationCount.
        """
        groups: dict[str, dict[str, Any]] = {}
        for r in records:
            key = r.get("department") or "unknown"
            if key not in groups:
                groups[key] = {"department": key, **self._empty_totals()}
            self._accumulate(groups[key], r)

        result = list(groups.values())
        result.sort(key=lambda x: x["invocationCount"], reverse=True)
        return result

    def aggregate_by_agent_name(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Aggregate records per agent-name dimension (executive rollup L3).

        Keyed on the additive ``agentName`` record field (default
        ``"unknown"`` when absent/untagged). Mirrors the other
        ``aggregate_by_*`` shape and is sorted descending by invocationCount.
        """
        groups: dict[str, dict[str, Any]] = {}
        for r in records:
            key = r.get("agentName") or "unknown"
            if key not in groups:
                groups[key] = {"agentName": key, **self._empty_totals()}
            self._accumulate(groups[key], r)

        result = list(groups.values())
        result.sort(key=lambda x: x["invocationCount"], reverse=True)
        return result

    def aggregate_by_date(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate records per calendar date (YYYY-MM-DD).

        Returns a list of dicts sorted ascending by date.
        """
        groups: dict[str, dict[str, Any]] = {}
        for r in records:
            ts = r.get("timestamp", "")
            date_key = ts[:10] if len(ts) >= 10 else "unknown"
            if date_key not in groups:
                groups[date_key] = {"date": date_key, **self._empty_totals()}
            self._accumulate(groups[date_key], r)

        result = list(groups.values())
        result.sort(key=lambda x: x["date"])
        return result

    def aggregate_by_team(
        self,
        records: list[dict[str, Any]],
        team_members_map: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        """Aggregate records per team.

        Args:
            records: Activity records.
            team_members_map: Mapping of team name → list of user identities.

        Returns a list of team summary dicts.
        """
        # Build reverse lookup: user_identity → set of team names
        user_to_teams: dict[str, set[str]] = defaultdict(set)
        for team_name, members in team_members_map.items():
            for member in members:
                user_to_teams[member].add(team_name)

        groups: dict[str, dict[str, Any]] = {}
        for team_name in team_members_map:
            groups[team_name] = {
                "teamName": team_name,
                "memberCount": len(team_members_map[team_name]),
                **self._empty_totals(),
            }

        for r in records:
            user_id = r.get("userIdentity", "")
            for team_name in user_to_teams.get(user_id, set()):
                self._accumulate(groups[team_name], r)

        result = list(groups.values())
        result.sort(key=lambda x: x["totalTokens"], reverse=True)
        return result

    def aggregate_team_totals(
        self,
        records: list[dict[str, Any]],
        members: list[str],
    ) -> dict[str, Any]:
        """Compute total tokens and cost for a team over the given records.

        A record is counted when ANY of the team's ``members`` flex-matches it
        via :meth:`FilterService.member_matches_record` (the SAME case-insensitive
        equality-or-substring matching used by the activity user filter). This is
        what makes usernames/emails stored on memberships line up with the full
        IAM ARNs on activity records.

        Each record is counted AT MOST ONCE even when several members match it.
        Blank member values are ignored. ``totalCostEstimate`` stays ``None``
        unless at least one matched record carried a cost, in which case it is
        the sum of those costs.

        Returns:
            ``{"totalTokens": int, "totalCostEstimate": float | None}``.
        """
        normalized = [m for m in members if m and m.strip()]
        totals = self._empty_totals()
        if normalized:
            for record in records:
                if any(FilterService.member_matches_record(m, record) for m in normalized):
                    self._accumulate(totals, record)

        return {
            "totalTokens": totals["totalTokens"],
            "totalCostEstimate": totals["costEstimate"],
        }

    def compute_user_model_matrix(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Compute a user-to-model matrix.

        Returns a list of dicts, one per (user, model) pair with
        invocationCount and costEstimate.
        """
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for r in records:
            user_id = r.get("userIdentity", "unknown")
            model_id = r.get("modelId", "unknown")
            key = (user_id, model_id)
            if key not in groups:
                groups[key] = {
                    "userIdentity": user_id,
                    "userName": r.get("userName", user_id),
                    "modelId": model_id,
                    "invocationCount": 0,
                    "costEstimate": None,
                }
            groups[key]["invocationCount"] += 1
            cost = r.get("costEstimate")
            if cost is not None:
                if groups[key]["costEstimate"] is None:
                    groups[key]["costEstimate"] = 0.0
                groups[key]["costEstimate"] += cost

        return list(groups.values())

    def compute_overview(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute a top-level overview summary across all records."""
        totals = self._empty_totals()
        users: set[str] = set()
        models: set[str] = set()

        for r in records:
            self._accumulate(totals, r)
            users.add(r.get("userIdentity", "unknown"))
            models.add(r.get("modelId", "unknown"))

        # Determine date range from records
        timestamps = [r.get("timestamp", "") for r in records if r.get("timestamp")]
        date_start = min(timestamps) if timestamps else ""
        date_end = max(timestamps) if timestamps else ""

        return {
            "totalInvocations": totals["invocationCount"],
            "totalTokens": totals["totalTokens"],
            "totalInputTokens": totals["inputTokens"],
            "totalOutputTokens": totals["outputTokens"],
            "totalCacheReadTokens": totals["cacheReadTokens"],
            "totalCacheWriteTokens": totals["cacheWriteTokens"],
            "totalCostEstimate": totals["costEstimate"],
            "activeUsers": len(users),
            "activeModels": len(models),
            "dateRange": {"start": date_start, "end": date_end},
        }

    @staticmethod
    def compute_percentages(values: list[float]) -> list[float]:
        """Compute percentage of total for each value.

        Returns a list of percentages that sum to 100.0 (within
        floating-point tolerance). Returns all 0.0 if total is zero.
        """
        total = sum(values)
        if total == 0:
            return [0.0] * len(values)
        return [(v / total) * 100.0 for v in values]
