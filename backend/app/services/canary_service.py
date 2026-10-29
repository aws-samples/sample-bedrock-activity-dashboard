"""Unattributed-spend canary service (Cost Accountability & Governance).

Computes the share of spend and records that cannot be attributed to any team
membership, plus the share attributable to base model IDs / untagged inference
profiles. All share computations are pure and testable so they can be exercised
by the Hypothesis property tests (Correctness Properties 9 and 10):

- Property 9: the unattributed spend share lies in ``[0, 1]`` and complements
  the attributed share (attributed spend + unattributed spend == total spend).
- Property 10: the base-model / untagged-profile share lies in ``[0, 1]`` and
  complements the tagged-profile share.

Team-membership matching reuses :meth:`FilterService.member_matches_record` so
canary attribution agrees exactly with the team rollups produced by
``AggregationService``. Basis is billed when CUR billed data is available for the
window, else estimated (labeled) — the caller selects the basis.
"""

from dataclasses import dataclass
from typing import Any

from app.services.filter_service import FilterService

# Basis label -> the record cost field weighted for spend shares. "billed" uses
# the authoritative Tier A figure attached during reconciliation; "estimated"
# uses the existing Tier B token-based estimate.
_BASIS_COST_FIELDS: dict[str, str] = {
    "billed": "billedCost",
    "estimated": "costEstimate",
}


def _clamp_unit(value: float) -> float:
    """Clamp a share into ``[0, 1]`` to absorb floating-point drift."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _record_spend(record: dict[str, Any], cost_field: str) -> float:
    """Return a record's spend for the chosen basis, treating missing/None as 0."""
    value = record.get(cost_field)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class CanaryResult:
    """Result of an unattributed-share computation.

    ``unattributed_spend_share`` and ``unattributed_record_share`` always lie in
    ``[0, 1]``. The attributed counterparts are derived so that attributed and
    unattributed always sum back to the total (Property 9).
    """

    unattributed_spend_share: float
    unattributed_record_share: float
    basis: str
    total_spend: float
    unattributed_spend: float
    total_records: int
    unattributed_records: int

    @property
    def attributed_spend(self) -> float:
        """Spend attributable to a team membership (total - unattributed)."""
        return self.total_spend - self.unattributed_spend

    @property
    def attributed_records(self) -> int:
        """Record count attributable to a team membership."""
        return self.total_records - self.unattributed_records

    @property
    def attributed_spend_share(self) -> float:
        """Complement of the unattributed spend share (sums to 1.0)."""
        return 1.0 - self.unattributed_spend_share

    @property
    def attributed_record_share(self) -> float:
        """Complement of the unattributed record share (sums to 1.0)."""
        return 1.0 - self.unattributed_record_share


class CanaryService:
    """Computes unattributed-spend and base-model canary shares.

    All public methods are pure with respect to their inputs (no I/O), so the
    share invariants can be verified directly by property tests.
    """

    @staticmethod
    def _resolve_cost_field(basis: str) -> str:
        """Map a basis label to its record cost field, raising on unknown basis."""
        try:
            return _BASIS_COST_FIELDS[basis]
        except KeyError:
            raise ValueError(
                f"Unsupported basis {basis!r}; expected one of "
                f"{sorted(_BASIS_COST_FIELDS)}."
            ) from None

    @staticmethod
    def _is_attributed(
        record: dict[str, Any],
        members: list[str],
    ) -> bool:
        """True when the record's caller identity flex-matches ANY team member.

        Delegates to :meth:`FilterService.member_matches_record` so attribution
        agrees exactly with the team rollups.
        """
        return any(
            FilterService.member_matches_record(member, record) for member in members
        )

    @staticmethod
    def _is_base_model(record: dict[str, Any]) -> bool:
        """True when a record used a base model ID / untagged profile.

        A record is "base model / untagged" when it carries no
        ``inferenceProfileArn`` OR its resolved ``environment`` / ``application``
        dimension is ``"unknown"`` (Req 7.2 / 4.5). The complement — carrying a
        profile ARN with both dimensions resolved — is the tagged-profile share.
        """
        if not record.get("inferenceProfileArn"):
            return True
        environment = (record.get("environment") or "unknown").strip().lower()
        application = (record.get("application") or "unknown").strip().lower()
        return environment == "unknown" or application == "unknown"

    @staticmethod
    def _flatten_members(team_members_map: dict[str, list[str]]) -> list[str]:
        """Collect the de-duplicated, non-blank member identities across teams."""
        seen: set[str] = set()
        members: list[str] = []
        for team_members in team_members_map.values():
            for member in team_members:
                if not member or not member.strip():
                    continue
                key = member.strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                members.append(member)
        return members

    def unattributed_share(
        self,
        records: list[dict[str, Any]],
        team_members_map: dict[str, list[str]],
        basis: str,
    ) -> CanaryResult:
        """Compute the unattributed share of spend and of records.

        A record is *attributed* when its caller identity matches at least one
        team membership (via :meth:`FilterService.member_matches_record`). Spend
        is weighted by the cost field selected by ``basis`` ("billed" or
        "estimated"); missing/None costs count as zero.

        The result guarantees ``attributed + unattributed == total`` for both
        spend and record counts, and both shares lie in ``[0, 1]`` (Property 9).
        When the total is zero the share is defined as ``0.0``.
        """
        cost_field = self._resolve_cost_field(basis)
        members = self._flatten_members(team_members_map)

        total_spend = 0.0
        unattributed_spend = 0.0
        total_records = 0
        unattributed_records = 0

        for record in records:
            spend = _record_spend(record, cost_field)
            total_spend += spend
            total_records += 1
            if not self._is_attributed(record, members):
                unattributed_spend += spend
                unattributed_records += 1

        spend_share = (
            _clamp_unit(unattributed_spend / total_spend) if total_spend > 0 else 0.0
        )
        record_share = (
            _clamp_unit(unattributed_records / total_records)
            if total_records > 0
            else 0.0
        )

        return CanaryResult(
            unattributed_spend_share=spend_share,
            unattributed_record_share=record_share,
            basis=basis,
            total_spend=total_spend,
            unattributed_spend=unattributed_spend,
            total_records=total_records,
            unattributed_records=unattributed_records,
        )

    def base_model_share(
        self,
        records: list[dict[str, Any]],
        basis: str = "estimated",
    ) -> float:
        """Share of spend attributable to base model IDs / untagged profiles.

        Weighted by the ``basis`` cost field when there is spend to weight;
        falls back to a record-count share when total spend is zero so the value
        stays well-defined. The result lies in ``[0, 1]`` and complements the
        tagged-profile share (Property 10 / Req 7.2, 4.5).
        """
        cost_field = self._resolve_cost_field(basis)

        total_spend = 0.0
        base_spend = 0.0
        total_records = 0
        base_records = 0

        for record in records:
            spend = _record_spend(record, cost_field)
            total_spend += spend
            total_records += 1
            if self._is_base_model(record):
                base_spend += spend
                base_records += 1

        if total_spend > 0:
            return _clamp_unit(base_spend / total_spend)
        if total_records > 0:
            return _clamp_unit(base_records / total_records)
        return 0.0

    def trend(
        self,
        records_by_day: dict[str, list[dict[str, Any]]],
        team_members_map: dict[str, list[str]],
        basis: str,
    ) -> list[dict[str, Any]]:
        """Per-day unattributed share, sorted ascending by date.

        Each entry carries the day's spend and record shares plus the basis
        label, so the frontend can plot the canary trend over the window.
        """
        points: list[dict[str, Any]] = []
        for day in sorted(records_by_day):
            result = self.unattributed_share(
                records_by_day[day], team_members_map, basis
            )
            points.append(
                {
                    "date": day,
                    "unattributedSpendShare": result.unattributed_spend_share,
                    "unattributedRecordShare": result.unattributed_record_share,
                    "basis": result.basis,
                }
            )
        return points

    @staticmethod
    def evaluate_target(share: float, target: float) -> bool:
        """True when the observed share meets the target (share <= target)."""
        return share <= target
