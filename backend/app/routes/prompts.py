"""Prompt history API routes for the Bedrock Activity Dashboard."""

import logging
import math
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.services import settings_service
from app.services.chat_message_service import (
    get_chat_activity_records,
    get_chat_record_by_id,
    ingestion_regions_for_chat_dedup,
    suppress_chat_placeholders_with_real_invocation,
)
from app.services.cloudtrail_service import CloudTrailError, CloudTrailService
from app.services.cost_service import CostService
from app.services.demo_service import get_demo_records, suppress_real_duplicates_of_demo_invocations
from app.services.filter_service import ActivityFilters, FilterService, parse_iso_to_utc

router = APIRouter(prefix="/api/activity/prompts", tags=["prompts"])

logger = logging.getLogger(__name__)

# Shared service instances
_cloudtrail_service = CloudTrailService()
_cost_service = CostService()
_filter_service = FilterService()


def _parse_filters(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_identities: Optional[str] = None,
    team_id: Optional[str] = None,
    source_application: Optional[str] = None,
    regions: Optional[str] = None,
) -> dict[str, Any]:
    """Parse and validate common query parameters into a filter dict."""
    if start_date and end_date:
        start_dt = parse_iso_to_utc(start_date)
        end_dt = parse_iso_to_utc(end_date, end_of_day_if_bare=True)
    elif start_date or end_date:
        default_start, default_end = _filter_service.get_default_date_range()
        start_dt = parse_iso_to_utc(start_date) if start_date else default_start
        if end_date:
            end_dt = parse_iso_to_utc(end_date, end_of_day_if_bare=True)
        else:
            end_dt = default_end
    else:
        start_dt, end_dt = _filter_service.get_default_date_range()

    _filter_service.validate_date_range(start_dt, end_dt)

    users = (
        [u.strip() for u in user_identities.split(",") if u.strip()]
        if user_identities
        else []
    )
    region_list = (
        [r.strip() for r in regions.split(",") if r.strip()]
        if regions
        else []
    )

    return {
        "start_date": start_dt,
        "end_date": end_dt,
        "user_identities": users,
        "team_id": team_id,
        "source_application": source_application,
        "regions": region_list,
    }


def _scope_to_user(params: dict[str, Any], current_user: CurrentUser) -> dict[str, Any]:
    """Best-effort per-user scoping.

    Non-admin callers are restricted to their own prompt records by overriding
    the effective ``user_identities`` filter with the caller's own identity
    (email or username). Admins keep full/unfiltered access and any
    client-supplied user filters.
    """
    if current_user is not None and not current_user.can_view_all:
        params["user_identities"] = [current_user.identity]
    return params


def _fetch_and_filter(
    params: dict[str, Any],
    db: Session,
) -> list[dict[str, Any]]:
    """Fetch records from CloudTrail + Agent Chat, apply costs, then apply filters.

    Pipeline: CloudTrailService + ChatMessageService → CostService → FilterService
    Agent Chat records respect the same user identity filter as CloudTrail
    records: they are included only when no user filter is set (or when the
    filter matches their "agent-chat" identity), so filtering for a specific
    human user no longer leaks Agent Chat records.
    """
    # Widen ingestion (not the display filter) to always include the region
    # Agent Chat's real Bedrock calls run in — see the matching comment in
    # routes/activity.py's source_activity_records_with_meta. Without this, a
    # region filter that excludes Agent Chat's home region starves the
    # suppression step below of the real invocation record it needs to match
    # against, leaving the chat placeholder (0 tokens/N/A cost) visible even
    # though the real, priced invocation exists in that other region.
    ingestion_regions = ingestion_regions_for_chat_dedup(params.get("regions") or None)
    records = _cloudtrail_service.get_activity_records(
        params["start_date"], params["end_date"], ingestion_regions
    )

    # Merge PERSISTED demo records into the same pipeline so they flow through
    # cost application, filtering, sorting, and pagination just like real
    # CloudTrail records (they carry userIdentity/promptText/responseText).
    # These only exist when an admin has generated a demo batch, so this is
    # safe to always run (no-op otherwise).
    try:
        demo_records = get_demo_records(db, params["start_date"], params["end_date"])
        if demo_records:
            logger.info("Merged %d persisted demo records", len(demo_records))

            # Every demo record is backed by a genuine Bedrock call the backend
            # itself made, which independently shows up via CloudTrail ingestion
            # under the task-role identity at the real invocation time — drop
            # that real duplicate so prompt history doesn't show the same
            # invocation twice. Best-effort.
            before = len(records)
            records = suppress_real_duplicates_of_demo_invocations(demo_records, records)
            suppressed = before - len(records)
            if suppressed:
                logger.info(
                    "Suppressed %d real record(s) duplicating a demo invocation", suppressed
                )
        records = records + demo_records
    except Exception:
        logger.exception("Failed to fetch persisted demo records")

    # Fetch Agent Chat records (graceful degradation on failure). Metadata
    # for Agent Chat / demo / CloudWatch / S3 is always included in the
    # list view — the Prompt History toggle gates BODY visibility (prompt
    # and response text), not metadata rows. Body gating happens on the
    # detail endpoint; see get_prompt_detail below.
    chat_records: list[dict[str, Any]] = []
    try:
        chat_records = get_chat_activity_records(db, params["start_date"], params["end_date"])
        logger.info("Fetched %d Agent Chat records for prompt history", len(chat_records))
    except Exception:
        logger.exception("Failed to fetch Agent Chat records for prompt history")

    # Every Agent Chat turn also makes a real Bedrock call that independently
    # shows up via CloudTrail/CloudWatch/S3 ingestion (with real token counts
    # and cost), so without this step the same invocation is double-counted:
    # once as a real record, once as the chat placeholder (which always
    # carries inputTokens=0/costEstimate=None). Drop the placeholder whenever
    # a matching real record is present; best-effort, so an unmatched
    # placeholder is kept rather than silently dropped. Mirrors the same step
    # in routes/activity.py's source_activity_records_with_meta — this route
    # has its own separate ingestion pipeline and needs the same fix applied
    # independently.
    try:
        before = len(chat_records)
        chat_records = suppress_chat_placeholders_with_real_invocation(chat_records, records)
        suppressed = before - len(chat_records)
        if suppressed:
            logger.info(
                "Suppressed %d Agent Chat placeholder(s) with a matching real invocation",
                suppressed,
            )
    except Exception:
        logger.exception("Failed to suppress duplicate Agent Chat placeholders")

    # Apply costs to all records
    all_records = records + chat_records
    _cost_service.apply_costs(all_records, db)

    # Apply filters to CloudTrail records (includes user identity filter)
    filters = ActivityFilters(
        start_date=params["start_date"],
        end_date=params["end_date"],
        user_identities=params["user_identities"],
        source_application=params["source_application"],
        regions=params.get("regions", []),
    )
    filtered_ct = _filter_service.apply_filters(records, filters)

    # Apply filters to Agent Chat records using the SAME user identity filter as
    # the CloudTrail records. When a user filter is set, chat records are only
    # included if they match it (they won't for a specific human user, since
    # their identity is "agent-chat"). When no user filter is set (empty list),
    # apply_filters applies no user predicate and chat records are still included.
    #
    # NOTE: region filter intentionally NOT applied to chat records — see the
    # matching comment in routes/activity.py's _fetch_and_filter_with_meta.
    chat_filters = ActivityFilters(
        start_date=params["start_date"],
        end_date=params["end_date"],
        user_identities=params["user_identities"],
        source_application=params["source_application"],
    )
    filtered_chat = _filter_service.apply_filters(chat_records, chat_filters)

    combined = filtered_ct + filtered_chat

    if params["team_id"]:
        # Only apply team filter to CloudTrail records, keep all chat records
        team_filtered_ct = _filter_service.filter_by_team(filtered_ct, params["team_id"], db)
        combined = team_filtered_ct + filtered_chat

    return combined


def _error_response(exc: CloudTrailError) -> dict[str, Any]:
    """Build a 502 error response body from a CloudTrailError."""
    return {
        "error": {
            "code": exc.error_code,
            "message": exc.message,
        }
    }


def _to_prompt_entry(record: dict[str, Any]) -> dict[str, Any]:
    """Convert an activity record to a prompt history list entry."""
    return {
        "id": record.get("id", ""),
        "timestamp": record.get("timestamp", ""),
        "userName": record.get("userName", ""),
        "modelId": record.get("modelId", ""),
        "sourceApplication": record.get("sourceApplication", ""),
        "inputTokens": record.get("inputTokens", 0),
        "outputTokens": record.get("outputTokens", 0),
        "cacheReadTokens": record.get("cacheReadTokens", 0),
        "cacheWriteTokens": record.get("cacheWriteTokens", 0),
        "costEstimate": record.get("costEstimate"),
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("")
def get_prompts(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user_identities: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    source_application: Optional[str] = Query(None),
    regions: Optional[str] = Query(None, description="Comma-separated regions to filter to"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Paginated prompt history with filters. Default sort: reverse chronological."""
    try:
        params = _parse_filters(
            start_date, end_date, user_identities, team_id, source_application, regions
        )
        params = _scope_to_user(params, current_user)
        records = _fetch_and_filter(params, db)

        # Sort by (timestamp, id) descending for a deterministic total order.
        # Tie-breaking on id keeps page boundaries stable across re-fetches when
        # multiple records share the same timestamp.
        records.sort(key=lambda r: (r.get("timestamp", ""), r.get("id", "")), reverse=True)

        total = len(records)
        total_pages = math.ceil(total / page_size) if total > 0 else 0

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = records[start_idx:end_idx]

        return {
            "items": [_to_prompt_entry(r) for r in page_items],
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
        }
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))


@router.get("/{record_id}")
def get_prompt_detail(
    record_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Full detail for a single activity record.

    Metadata (timestamp, user, model, source, cost, token counts) is always
    returned. Prompt and response BODY TEXT are gated by the admin Prompt
    History toggle:

    - toggle ON  -> promptText / responseText populated, regardless of source.
      For records that live in our DB (Agent Chat, demo records) this means
      Fernet-decrypted content; for records fetched live from CloudWatch
      Logs / S3 / CloudTrail it's the content as returned by AWS (whose
      own at-rest encryption on those services is what's actually in
      effect — this dashboard cannot encrypt data it does not store).
    - toggle OFF -> body fields are blanked and ``promptBodyHidden=true`` is
      added so the UI can render a "content hidden" state. This hiding
      applies universally — the same CloudWatch/S3-sourced row that would
      previously surface plaintext through this endpoint is suppressed too.
    """
    try:
        params = _parse_filters(start_date, end_date)
        # Scope BEFORE fetching/filtering: for non-admins this pins the effective
        # user_identities filter to the caller's own identity, so the scoped set
        # searched below can never contain another user's record.
        params = _scope_to_user(params, current_user)
        # Reuses the cached raw-ingestion path (CloudTrailService cache) via
        # _fetch_and_filter rather than triggering a fresh full ingestion.
        records = _fetch_and_filter(params, db)

        # Find the record by ID within the caller's already-scoped/filtered set.
        record = next((r for r in records if r.get("id") == record_id), None)

        prompt_history_enabled = settings_service.get_prompt_history_enabled(db)

        # If not found in CloudTrail records, check Agent Chat records —
        # but only when the prompt-history toggle is on, so a direct detail
        # URL from a prior session can't exfiltrate chat content after an
        # admin has turned the toggle off.
        if record is None and prompt_history_enabled:
            try:
                record = get_chat_record_by_id(
                    db,
                    record_id,
                    viewer_identity=current_user.identity,
                    can_view_all=current_user.can_view_all,
                )
            except Exception:
                logger.warning("Failed to search Agent Chat records for detail", exc_info=True)

        if record is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "NOT_FOUND", "message": f"Record {record_id} not found"}},
            )

        # Build detail response. Always include metadata.
        detail = _to_prompt_entry(record)
        detail["userIdentity"] = record.get("userIdentity", "")
        detail["sourceIp"] = record.get("sourceIp", "")
        detail["totalTokens"] = record.get("totalTokens", 0)

        # Universal body gate: when the toggle is off we never return the
        # prompt/response bodies, regardless of whether the source is our DB
        # or AWS logs. The UI uses promptBodyHidden to render a clear
        # "content hidden" state instead of empty strings.
        if prompt_history_enabled:
            detail["promptText"] = record.get("promptText", "")
            detail["responseText"] = record.get("responseText", "")
            detail["promptBodyHidden"] = False
        else:
            detail["promptText"] = ""
            detail["responseText"] = ""
            detail["promptBodyHidden"] = True

        return detail
    except CloudTrailError as exc:
        return JSONResponse(status_code=502, content=_error_response(exc))
