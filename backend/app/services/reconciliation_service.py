"""Reconciliation service (Tier B estimate -> Tier A billed).

Scales the existing token-based per-prompt cost estimates (Tier B) so that
drill-downs sum to the authoritative billed cost (Tier A) ingested into
``cur_billed_cost``. The billed lookup is a single indexed query on
``cur_billed_cost``; Athena is never queried on this read path (Req 11.2).

The reconciliation math is intentionally kept as pure functions
(``compute_factors`` / ``reconcile_prompts``) so the correctness properties
(Properties 1-5) can exercise them in isolation without a database.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CurBilledCost

# Slices reconcile exactly to billed within half a cent (Req 3.3).
RECON_TOLERANCE = 0.005

# Per-prompt reconciled cost is rounded to this many decimals before the
# leftover residual is absorbed. Kept fine-grained so tiny sub-cent per-prompt
# token costs are not collapsed to zero; the residual absorption still makes
# the slice sum exactly to billed.
_ROUND_DECIMALS = 6


class SliceKey(NamedTuple):
    """Coarse reconciliation key: (team-or-principal, model, day)."""

    team_or_principal: str
    model: str
    day: str


@dataclass
class ReconciledSlice:
    """A single reconciled slice.

    ``factor`` is ``billed / estimated`` when a factor is applied, else ``1.0``.
    ``variance`` is ``billed - estimated`` when billed is present, else ``None``.
    ``flag`` is ``"billed_no_estimate"`` when billed is present but the estimate
    is zero (no division possible). ``basis`` is ``"reconciled"`` when billed
    data drives the slice, else ``"estimated_only"``.
    """

    key: SliceKey
    billed_cost: float | None
    estimated_cost: float
    factor: float
    variance: float | None
    flag: str | None
    basis: str


def _to_date(value: Any) -> date:
    """Coerce a date/datetime/ISO-8601 string to a ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"Unsupported date value: {value!r}")


class ReconciliationService:
    """Reconciles Tier B estimates against Tier A billed cost per slice."""

    @staticmethod
    def load_billed_slices(
        db: Session,
        start: Any,
        end: Any,
        principals: Iterable[str] | None = None,
    ) -> dict[SliceKey, float]:
        """Load billed cost per :class:`SliceKey` for the window.

        Runs a single indexed query on ``cur_billed_cost`` (usage_date range,
        optionally scoped to ``principals``). No Athena call is made here. Rows
        are collapsed to the coarse ``(team-or-principal, model, day)`` key,
        preferring the activated Team tag and falling back to the IAM principal
        when the tag is absent.
        """
        start_date = _to_date(start)
        end_date = _to_date(end)

        stmt = select(CurBilledCost).where(
            CurBilledCost.usage_date >= start_date,
            CurBilledCost.usage_date <= end_date,
        )
        principal_list = [p for p in principals if p] if principals is not None else None
        if principal_list:
            stmt = stmt.where(CurBilledCost.iam_principal.in_(principal_list))

        billed: dict[SliceKey, float] = {}
        for row in db.execute(stmt).scalars():
            team_or_principal = row.team_tag or row.iam_principal or ""
            key = SliceKey(
                team_or_principal=team_or_principal,
                model=row.model_id,
                day=row.usage_date.isoformat(),
            )
            billed[key] = billed.get(key, 0.0) + float(row.billed_cost)
        return billed

    @staticmethod
    def compute_factors(
        billed: dict[SliceKey, float],
        estimated: dict[SliceKey, float],
    ) -> dict[SliceKey, ReconciledSlice]:
        """Compute a :class:`ReconciledSlice` for every slice key.

        Rules (Req 3.1-3.7):
          * both present and ``estimated > 0`` -> ``factor = billed / estimated``,
            ``basis = "reconciled"``.
          * ``estimated == 0`` and ``billed > 0`` -> no division; ``factor = 1.0``,
            ``flag = "billed_no_estimate"``, ``basis = "reconciled"``.
          * billed absent -> ``factor = 1.0``, ``basis = "estimated_only"``,
            ``variance = None``.
          * ``variance = billed - estimated`` whenever billed is present.
        """
        result: dict[SliceKey, ReconciledSlice] = {}
        for key in set(billed) | set(estimated):
            est = estimated.get(key, 0.0)
            billed_cost = billed.get(key)

            if billed_cost is None:
                # No billed data: identity operation (Property 3).
                result[key] = ReconciledSlice(
                    key=key,
                    billed_cost=None,
                    estimated_cost=est,
                    factor=1.0,
                    variance=None,
                    flag=None,
                    basis="estimated_only",
                )
                continue

            variance = billed_cost - est
            if est > 0:
                # Factor applied (Property 2).
                result[key] = ReconciledSlice(
                    key=key,
                    billed_cost=billed_cost,
                    estimated_cost=est,
                    factor=billed_cost / est,
                    variance=variance,
                    flag=None,
                    basis="reconciled",
                )
            else:
                # Zero estimate: never divide (Property 4).
                result[key] = ReconciledSlice(
                    key=key,
                    billed_cost=billed_cost,
                    estimated_cost=est,
                    factor=1.0,
                    variance=variance,
                    flag="billed_no_estimate" if billed_cost > 0 else None,
                    basis="reconciled",
                )
        return result

    @staticmethod
    def reconcile_prompts(records: list[dict[str, Any]], slice: ReconciledSlice) -> None:
        """Attach ``reconciledCost`` to each record in the slice in place.

        For a reconciled slice, each record's estimate is scaled by the slice
        factor and rounded; the rounding residual (``billed - sum(rounded)``) is
        absorbed into the largest-estimate record so the slice sums exactly to
        billed within ``RECON_TOLERANCE`` (Property 1).

        When ``basis == "estimated_only"`` (no billed data) each record's
        ``reconciledCost`` is left equal to its estimate (factor 1, Property 3).
        When the slice is flagged ``billed_no_estimate`` there is no estimate to
        scale, so records keep their (zero) estimate.
        """
        if not records:
            return

        # No billed data, or billed present but nothing to scale: identity.
        if slice.basis == "estimated_only" or slice.flag == "billed_no_estimate":
            for record in records:
                record["reconciledCost"] = record.get("costEstimate")
            return

        estimates = [float(record.get("costEstimate") or 0.0) for record in records]
        factor = slice.factor
        rounded = [round(est * factor, _ROUND_DECIMALS) for est in estimates]

        # Absorb the leftover residual into the largest-estimate record so the
        # slice sums exactly to billed (largest-remainder residual absorption).
        target = slice.billed_cost if slice.billed_cost is not None else sum(rounded)
        residual = target - sum(rounded)
        largest_idx = max(range(len(estimates)), key=lambda i: estimates[i])
        rounded[largest_idx] = round(rounded[largest_idx] + residual, _ROUND_DECIMALS)

        for record, value in zip(records, rounded):
            record["reconciledCost"] = value
