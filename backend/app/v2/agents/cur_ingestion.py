"""CUR Ingestion Agent — deterministic wrapper around the CUR billed-cost ingestion.

Runs the authoritative CUR 2.0 billed-cost ingestion (Athena query -> idempotent
upsert into ``cur_billed_cost``) on the v2 direct-execution path. No LLM is
involved: the agent invokes :class:`CurIngestionService` and converts its
:class:`IngestionResult` into the platform finding shape.

Outcome mapping:
  - ``success``  -> one informational finding summarizing coverage/rows upserted.
  - ``skipped``  -> one informational finding (Athena unconfigured or ingestion
                    disabled). Benign by design — never a failure.
  - ``failed``   -> raises :class:`CurIngestionFailedError` so the execution
                    service records the run as a failure with error details.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class CurIngestionFailedError(Exception):
    """Raised when the underlying CUR ingestion run reports ``failed``.

    Propagates out of the direct-execution path so AgentExecutionService records
    the execution as a failure (with the ingestion's error detail) instead of
    falling back to an LLM invocation.
    """


def run_ingestion(db: Session) -> list[dict[str, Any]]:
    """Run a single CUR ingestion and return platform-shaped findings.

    Returns a single informational finding for ``success``/``skipped`` outcomes.
    Raises :class:`CurIngestionFailedError` on a ``failed`` outcome.
    """
    from app.services.cur_ingestion_service import CurIngestionService

    result = CurIngestionService().ingest(db)

    if result.status == "failed":
        raise CurIngestionFailedError(
            f"CUR ingestion failed: {result.error_detail or 'unknown error'}"
        )

    finding = {
        "status": result.status,
        "rows_upserted": result.rows_upserted,
        "covered_through": (
            result.covered_through.isoformat() if result.covered_through else None
        ),
        "latest_billing_period": result.latest_billing_period,
        "error_detail": result.error_detail,
        # "skipped" (unconfigured/disabled) is a benign informational outcome.
        "severity": "low",
    }
    logger.info(
        "cur-ingestion direct: status=%s rows=%d covered_through=%s period=%s",
        result.status,
        result.rows_upserted,
        finding["covered_through"],
        result.latest_billing_period,
    )
    return [finding]
