"""On-demand demo data service.

Admins can generate a batch of demo activity on demand. Unlike the old
always-on ``DEMO_MODE`` request-time injection, this persists records to the
``demo_activity_records`` table so they behave like real history: they flow
through the analytics pipeline (cost application, filtering, pagination) and
survive across requests until explicitly cleared.

Every record generated is a GENUINE Bedrock invocation: a real prompt is sent
to a real model via the bedrock-runtime Converse API, and the persisted
record uses the model's real response text and real token usage (so cost
computed downstream is real too). Each prompt embeds CLEARLY-FAKE PII and
sensitive-looking content (standard test values only) so the PII Detector
agent has real content to flag; the ``pii_types`` column records which
categories appear in each record.

The one thing that is staged rather than literal is the persisted
``timestamp``: instead of using the moment the call actually happened (which
would bunch every record into a single instant), timestamps are spread across
the last ~14 days so trend-oriented agents (Usage Forecaster, Cost Anomaly
Alerter) have something meaningful to analyze in a demo. The content,
response, tokens, and cost are all real regardless of the timestamp used.

Invocations run concurrently (thread pool) to keep total latency reasonable
even for larger batches. Per-record failures (model not enabled / access
denied / throttled) are tolerated and reported; a failed record is simply not
persisted.
"""

import concurrent.futures
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import boto3
from botocore.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import DemoActivityRecord
from app.services.crypto_service import get_crypto
from app.v2.services.guardrail_creation_service import get_latest_published_guardrail

logger = logging.getLogger(__name__)

# Human-readable demo users (8 distinct identities).
_DEMO_USERS = [
    "amelia.chen",
    "raj.patel",
    "sofia.martinez",
    "liam.oconnor",
    "yuki.tanaka",
    "grace.okafor",
    "noah.schmidt",
    "priya.nair",
]

# Source applications from the known set recognized by CloudTrailService.
_DEMO_SOURCE_APPS = [
    "Kiro",
    "VS Code",
    "AWS Console",
    "Python SDK (Boto3)",
    "Bedrock Agent",
]

_DEMO_ACCOUNT_ID = "000000000000"

# Cheap models to invoke for every demo record, in priority order. All are
# Active on the Bedrock model lifecycle (never Legacy/EOL) as of this writing
# — see https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html.
# Claude 3.5 Haiku was previously in this list but is Legacy (EOL 2026-06-19);
# replaced with Claude Haiku 4.5, the same current-generation model already
# used by this dashboard's other agents (PII Detector, AgentCore invoker,
# etc.). Failures per model (not enabled / access denied) are tolerated and
# skipped — the record simply isn't persisted rather than falling back to
# fabricated content.
_REAL_INVOCATION_MODELS = [
    "us.amazon.nova-lite-v1:0",
    "us.amazon.nova-micro-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.meta.llama3-3-70b-instruct-v1:0",
]

# Cap on concurrent in-flight Bedrock calls per batch.
_MAX_CONCURRENT_INVOCATIONS = 8

# ---------------------------------------------------------------------------
# "Tagged application spend" option (Cost Governance demo support).
#
# When requested, a portion of demo jobs invoke Bedrock through a small set
# of the dashboard's OWN application inference profiles instead of a bare
# model id. Each profile is tagged with Application/Environment/Team, so the
# resulting records resolve a real (non-"unknown") Application dimension via
# the existing ProfileTagService -> enrich_records_with_dimensions pipeline,
# giving Cost Governance's Spend-by-Application view real data to show
# without requiring the user to script `aws bedrock create-inference-profile`
# by hand. Profiles are get-or-created (by name) and reused across batches —
# generating demo data twice does not create duplicate profiles.
# ---------------------------------------------------------------------------
_DEMO_APP_PROFILES: list[dict[str, str]] = [
    {"name": "bedrock-dashboard-demo-claims-processor", "application": "ClaimsProcessor", "environment": "Production", "team": "InsuranceOps"},
    {"name": "bedrock-dashboard-demo-support-bot", "application": "SupportBot", "environment": "Production", "team": "CustomerSuccess"},
    {"name": "bedrock-dashboard-demo-onboarding-assistant", "application": "OnboardingAssistant", "environment": "Staging", "team": "PeopleOps"},
]

# Model every demo application profile copies from. Must be a model this
# account has access to; reuses the first entry of _REAL_INVOCATION_MODELS
# (resolved lazily to avoid a hard dependency on list ordering at import time).
def _demo_profile_source_model() -> str:
    return _REAL_INVOCATION_MODELS[0]

# Bound on how far apart a demo record's real invocation time and the
# independently-ingested real record's timestamp may be, for the purpose of
# recognizing them as the same underlying call. Mirrors chat_message_service's
# _DEDUPE_TOLERANCE_SECONDS — generous enough to cover CloudWatch/S3 ingestion
# lag without risking a false match against an unrelated invocation.
_DUPLICATE_MATCH_TOLERANCE_SECONDS = 120

# ---------------------------------------------------------------------------
# Fake PII / sensitive-content fragments — STANDARD TEST VALUES ONLY.
# These are well-known placeholder values that are safe to embed and are easy
# for a PII detector to flag. They are NOT real credentials or identities.
# ---------------------------------------------------------------------------
_FAKE_PII_FRAGMENTS: dict[str, str] = {
    "ssn": "My SSN is 123-45-6789 for the verification form.",
    "credit_card": "Charge it to card 4111 1111 1111 1111, exp 12/29.",
    "email": "Reach me at jane.doe@example.com regarding the account.",
    "phone": "Call the on-call engineer at 555-0142 after hours.",
    "address": "Ship it to 1234 Elm Street, Springfield, IL 62704.",
    "api_key": "The old access key AKIAIOSFODNN7EXAMPLE is still active.",
    "password": "Temporary password is P@ssw0rd!2024 — rotate it soon.",
}

_PII_TYPES = list(_FAKE_PII_FRAGMENTS.keys())

_BASE_PROMPTS = [
    "Summarize the quarterly cost report for the platform team.",
    "Draft a customer follow-up email about their support ticket.",
    "Help me fill out the onboarding form for a new hire.",
    "Review this account note and flag anything sensitive.",
    "Write a short incident summary for last night's outage.",
    "Explain the anomaly detected in yesterday's usage.",
]


def _build_prompt_with_pii(rng: random.Random) -> tuple[str, str, list[str]]:
    """Build a prompt embedding 1-3 fake-PII fragments.

    Returns (prompt_text, base_topic, pii_types) where pii_types lists the
    categories embedded so downstream detectors can be validated against a
    known ground truth. The real model response is filled in later by the
    actual Bedrock call — this function only builds the outbound prompt.
    """
    base = rng.choice(_BASE_PROMPTS)
    count = rng.randint(1, 3)
    chosen = rng.sample(_PII_TYPES, count)
    fragments = " ".join(_FAKE_PII_FRAGMENTS[t] for t in chosen)
    prompt = f"{base} {fragments}"
    return prompt, base, chosen


def _bedrock_session_kwargs() -> dict[str, Any]:
    """Shared session kwargs (region + optional static creds) for Bedrock clients."""
    region = settings.bedrock_region or settings.aws_region
    session_kwargs: dict[str, Any] = {"region_name": region}
    if settings.aws_access_key_id:
        session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
    if settings.aws_secret_access_key:
        session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.aws_session_token:
        session_kwargs["aws_session_token"] = settings.aws_session_token
    return session_kwargs


def _bedrock_runtime_client():
    """Build a bedrock-runtime client using the existing session/region pattern."""
    session = boto3.Session(**_bedrock_session_kwargs())
    return session.client(
        "bedrock-runtime",
        config=Config(connect_timeout=10, read_timeout=45, retries={"max_attempts": 1}),
    )


def _bedrock_control_plane_client():
    """Build a bedrock (control-plane) client for inference-profile management."""
    session = boto3.Session(**_bedrock_session_kwargs())
    return session.client(
        "bedrock",
        config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}),
    )


def _demo_profile_source_arn() -> str:
    """Build the ``modelSource.copyFrom`` ARN for the demo app profiles.

    ``CreateInferenceProfile`` requires a full ARN here (either a
    foundation-model ARN or a system-defined inference-profile ARN) — a bare
    model id like ``"us.amazon.nova-lite-v1:0"`` fails ``ValidationException``
    on the ``modelSource.copyFrom`` regex constraint. ``_demo_profile_source_model()``
    returns a cross-region inference-profile id (the ``us.`` prefix), so the
    matching ARN form is the system-defined ``inference-profile/<id>`` one —
    confirmed against the live API during development (a bare foundation-model
    ARN does not accept a ``us.``-prefixed id).
    """
    session = boto3.Session(**_bedrock_session_kwargs())
    account_id = session.client("sts").get_caller_identity()["Account"]
    region = settings.bedrock_region or settings.aws_region
    return f"arn:aws:bedrock:{region}:{account_id}:inference-profile/{_demo_profile_source_model()}"


def _get_or_create_demo_app_profiles(client: Any) -> list[dict[str, str]]:
    """Get-or-create the demo application inference profiles, returning
    ``[{"arn": ..., "application": ..., "environment": ..., "team": ...}, ...]``.

    Best-effort: looks up existing ``APPLICATION``-type profiles by name first
    (so re-running demo generation never creates duplicates), creating any
    that don't exist yet and tagging them with their Application/Environment/
    Team dimensions. Any failure (missing IAM permission, model not enabled,
    throttling, a malformed ``copyFrom`` ARN) is logged at WARNING and that
    profile is simply omitted from the returned list — callers degrade to
    bare-model-id invocations for the portion of jobs that would have used
    it, exactly like a model invocation failure already degrades for
    :func:`_invoke_job`.
    """
    try:
        existing_by_name: dict[str, str] = {}
        paginator_token: Optional[str] = None
        while True:
            kwargs: dict[str, Any] = {"typeEquals": "APPLICATION", "maxResults": 100}
            if paginator_token:
                kwargs["nextToken"] = paginator_token
            response = client.list_inference_profiles(**kwargs)
            for summary in response.get("inferenceProfileSummaries", []):
                existing_by_name[summary["inferenceProfileName"]] = summary["inferenceProfileArn"]
            paginator_token = response.get("nextToken")
            if not paginator_token:
                break
    except Exception as exc:  # noqa: BLE001 - best-effort; degrade to no profiles
        logger.warning("Could not list existing demo inference profiles: %s", exc)
        existing_by_name = {}

    resolved: list[dict[str, str]] = []
    source_arn = _demo_profile_source_arn()
    for spec in _DEMO_APP_PROFILES:
        arn = existing_by_name.get(spec["name"])
        if arn is None:
            try:
                create_response = client.create_inference_profile(
                    inferenceProfileName=spec["name"],
                    # Must match CreateInferenceProfile's description regex
                    # (alphanumeric, ':', '.', spaces, '_', '-' only) — an
                    # em dash fails ValidationException.
                    description="Bedrock Dashboard Demo Data tagged application profile.",
                    modelSource={"copyFrom": source_arn},
                    tags=[
                        {"key": "Application", "value": spec["application"]},
                        {"key": "Environment", "value": spec["environment"]},
                        {"key": "Team", "value": spec["team"]},
                    ],
                )
                arn = create_response["inferenceProfileArn"]
            except Exception as exc:  # noqa: BLE001 - best-effort, skip this profile
                logger.warning(
                    "Could not create demo inference profile %s: %s", spec["name"], exc
                )
                continue
        resolved.append({**spec, "arn": arn})

    return resolved


def _new_batch_id() -> str:
    return uuid.uuid4().hex[:16]


def _build_guardrail_config(db: Session) -> dict[str, Any] | None:
    """Return a Converse ``guardrailConfig`` dict for the dashboard's managed
    guardrail, or None when no recommendation has been approved+published yet.
    """
    latest = get_latest_published_guardrail(db)
    if latest is None:
        return None
    guardrail_id, version = latest
    return {"guardrailIdentifier": guardrail_id, "guardrailVersion": version, "trace": "enabled"}


def _spread_timestamp(rng: random.Random, now: datetime, days_back: int) -> datetime:
    """Return a timestamp within the last ``days_back`` days, business-ish hours.

    Used only to STAGE the persisted timestamp for demo storytelling (so
    trend/forecast agents have multi-day data to analyze) — the invocation
    itself, its response, and its token usage are all real regardless of
    this value.

    Never returns a timestamp after ``now``: when ``day_offset`` lands on
    today (0), naively overwriting the hour/minute/second with a random
    "business hours" value can land AFTER the real current time whenever
    ``now`` itself falls earlier in the day than the random hour (e.g. it's
    01:00 UTC and the random hour picked is 17) — producing a future
    timestamp. Clamped to ``now`` in that case.
    """
    day_offset = rng.randint(0, max(days_back - 1, 0))
    ts = now - timedelta(days=day_offset)
    candidate = ts.replace(
        hour=rng.randint(8, 18),
        minute=rng.randint(0, 59),
        second=rng.randint(0, 59),
        microsecond=0,
    )
    return min(candidate, now)


# Fraction of jobs routed through a tagged application inference profile
# when the "tagged application spend" option is requested. Not all jobs use
# one — a mix of tagged and bare-model-id calls is more representative of a
# real deployment (where plenty of usage is still untagged/"unknown").
_APP_PROFILE_JOB_FRACTION = 0.6


def _build_jobs(
    count: int,
    now: datetime,
    app_profiles: Optional[list[dict[str, str]]] = None,
) -> list[dict[str, Any]]:
    """Build ``count`` independent per-record job specs for the thread pool.

    When ``app_profiles`` is non-empty, roughly ``_APP_PROFILE_JOB_FRACTION``
    of jobs are assigned one of the profiles (round-robin-ish via random
    choice) and invoke through its ARN instead of a bare model id; the rest
    stay as plain bare-model-id jobs (``app_profile`` is ``None``).
    """
    jobs = []
    for _ in range(count):
        rng = random.Random()
        prompt_text, _, pii_types = _build_prompt_with_pii(rng)
        app_profile = (
            rng.choice(app_profiles)
            if app_profiles and rng.random() < _APP_PROFILE_JOB_FRACTION
            else None
        )
        jobs.append(
            {
                "user": rng.choice(_DEMO_USERS),
                "model_id": rng.choice(_REAL_INVOCATION_MODELS),
                "source_app": rng.choice(_DEMO_SOURCE_APPS),
                "prompt_text": prompt_text,
                "pii_types": pii_types,
                "ts": _spread_timestamp(rng, now, days_back=14),
                "app_profile": app_profile,
            }
        )
    return jobs


def _invoke_job(
    client: Any, job: dict[str, Any], guardrail_config: dict[str, Any] | None
) -> dict[str, Any]:
    """Run one real Bedrock Converse call for ``job``. Never raises.

    Captures ``invoked_at`` — the ACTUAL wall-clock time of the call — which
    is later persisted separately from the staged display ``timestamp`` so the
    real duplicate that independently shows up via CloudWatch/S3/CloudTrail
    ingestion can be matched and suppressed (see
    ``suppress_real_duplicates_of_demo_invocations``).

    ``guardrail_config``, when provided, applies the dashboard's own managed
    Bedrock Guardrail to this call — scoped to Demo Data generation only, not
    account-wide enforcement. Since demo prompts intentionally embed fake PII,
    an approved+published guardrail can genuinely block/anonymize demo
    responses here, which is expected and a good live demonstration of
    enforcement working.
    """
    invoked_at = datetime.now(timezone.utc)
    app_profile = job.get("app_profile")
    # When a tagged application profile was assigned to this job, invoke
    # through its ARN instead of the bare model id — this is what makes
    # the resulting invocation log carry a resolvable inferenceProfileArn
    # (see _extract_inference_profile_arn in cloudtrail_service.py).
    invoke_model_id = app_profile["arn"] if app_profile else job["model_id"]
    try:
        kwargs: dict[str, Any] = {
            "modelId": invoke_model_id,
            "messages": [{"role": "user", "content": [{"text": job["prompt_text"]}]}],
            "inferenceConfig": {"maxTokens": 256, "temperature": 0.5},
        }
        if guardrail_config:
            kwargs["guardrailConfig"] = guardrail_config
        response = client.converse(**kwargs)
        return {
            **job,
            "ok": True,
            "invoked_at": invoked_at,
            "response_text": _extract_converse_text(response),
            "usage": response.get("usage", {}) or {},
            "guardrail_intervened": response.get("stopReason") == "guardrail_intervened",
        }
    except Exception as exc:
        return {**job, "ok": False, "invoked_at": invoked_at, "error": str(exc)[:300]}


def generate_demo_data(
    db: Session, count: int = 30, with_applications: bool = False
) -> dict[str, Any]:
    """Generate a batch of demo data — every record a REAL Bedrock invocation.

    Args:
        db: SQLAlchemy session.
        count: number of demo records to attempt (each a real Bedrock call).
        with_applications: when True, get-or-create a small set of tagged
            application inference profiles (see ``_DEMO_APP_PROFILES``) and
            route roughly 60% of jobs through one of them, so the resulting
            records resolve a real Application dimension in Cost Governance's
            Spend-by-Application view instead of all landing in "unknown".
            Best-effort: if profile creation fails (missing IAM permission,
            model not enabled), generation proceeds with bare-model-id jobs
            exactly as when this option is False.

    Returns:
        Summary dict: ``{requested, succeeded, failed, batchId,
        guardrailIntervenedCount, applicationProfilesUsed}`` where ``failed``
        is a list of ``{model, error}`` for invocations that failed (model not
        enabled / access denied / throttled, etc.), and
        ``applicationProfilesUsed`` lists the Application names of any tagged
        profile that was actually used by at least one succeeded invocation
        (empty when ``with_applications`` is False or profile setup failed).
    """
    batch_id = _new_batch_id()
    now = datetime.now(timezone.utc)
    count = max(0, int(count))

    if count == 0:
        return {
            "requested": 0, "succeeded": 0, "failed": [], "batchId": batch_id,
            "guardrailIntervenedCount": 0, "applicationProfilesUsed": [],
        }

    try:
        client = _bedrock_runtime_client()
    except Exception as exc:
        logger.warning("Could not build bedrock-runtime client for demo: %s", exc)
        return {
            "requested": count,
            "succeeded": 0,
            "failed": [{"model": "n/a", "error": f"Could not create Bedrock client: {exc}"[:300]}],
            "batchId": batch_id,
            "guardrailIntervenedCount": 0,
            "applicationProfilesUsed": [],
        }

    app_profiles: list[dict[str, str]] = []
    if with_applications:
        try:
            control_client = _bedrock_control_plane_client()
            app_profiles = _get_or_create_demo_app_profiles(control_client)
        except Exception:
            logger.warning(
                "Tagged application profile setup failed — proceeding with "
                "bare-model-id demo jobs only", exc_info=True,
            )
            app_profiles = []

    jobs = _build_jobs(count, now, app_profiles=app_profiles)

    # Apply the dashboard's own managed Bedrock Guardrail (if a Guardrail
    # Recommender finding has been approved+published) — scoped to this Demo
    # Data batch only, not account-wide enforcement.
    try:
        guardrail_config = _build_guardrail_config(db)
    except Exception:
        logger.warning("Guardrail lookup failed — proceeding without a guardrail", exc_info=True)
        guardrail_config = None

    max_workers = max(1, min(_MAX_CONCURRENT_INVOCATIONS, count))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(
            executor.map(lambda job: _invoke_job(client, job, guardrail_config), jobs)
        )

    succeeded = 0
    guardrail_intervened_count = 0
    failed: list[dict[str, str]] = []
    applications_used: set[str] = set()
    for result in results:
        if not result["ok"]:
            failed.append({"model": result["model_id"], "error": result["error"]})
            continue

        if result.get("guardrail_intervened"):
            guardrail_intervened_count += 1

        app_profile = result.get("app_profile")
        if app_profile:
            applications_used.add(app_profile["application"])

        usage = result["usage"]
        # Encrypt prompt/response text at rest, matching the Agent Chat path.
        # Writes still succeed when the toggle is OFF (demo generation itself
        # is admin-triggered and gated on the Settings page), but any demo
        # row persisted this way is encrypted whenever a key is loaded so
        # the dashboard's at-rest encryption promise applies uniformly to
        # every row this app owns (chat_messages + demo_activity_records).
        crypto = get_crypto()
        db.add(
            DemoActivityRecord(
                id=f"demo-{batch_id}-{uuid.uuid4().hex[:8]}",
                timestamp=result["ts"].isoformat(),
                invoked_at=result["invoked_at"],
                user_identity=f"arn:aws:iam::{_DEMO_ACCOUNT_ID}:user/demo.{result['user']}",
                user_name=result["user"],
                model_id=_strip_region_prefix(result["model_id"]),
                source_application=result["source_app"],
                source_ip="203.0.113.10",
                input_tokens=int(usage.get("inputTokens", 0) or 0),
                output_tokens=int(usage.get("outputTokens", 0) or 0),
                cache_read_tokens=int(usage.get("cacheReadInputTokens", 0) or 0),
                cache_write_tokens=int(usage.get("cacheWriteInputTokens", 0) or 0),
                prompt_text=crypto.encrypt(result["prompt_text"]),
                response_text=crypto.encrypt(result["response_text"]),
                pii_types=result["pii_types"],
                is_real_invocation=True,
                batch_id=batch_id,
                inference_profile_arn=app_profile["arn"] if app_profile else None,
            )
        )
        succeeded += 1

    db.commit()

    if guardrail_intervened_count:
        logger.info(
            "Guardrail intervened on %d/%d demo invocation(s) in batch %s",
            guardrail_intervened_count, succeeded, batch_id,
        )

    return {
        "requested": count,
        "succeeded": succeeded,
        "failed": failed,
        "batchId": batch_id,
        "guardrailIntervenedCount": guardrail_intervened_count,
        "applicationProfilesUsed": sorted(applications_used),
    }


def _strip_region_prefix(model_id: str) -> str:
    """Strip a cross-region inference prefix (us./eu./ap.) for cost matching."""
    for prefix in ("us.", "eu.", "ap.", "global."):
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


def _extract_converse_text(response: dict[str, Any]) -> str:
    """Extract assistant text from a Converse API response."""
    output = response.get("output", {}) or {}
    message = output.get("message", {}) or {}
    blocks = message.get("content", []) or []
    parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("text")]
    return " ".join(parts).strip()


def clear_demo_data(db: Session) -> dict[str, Any]:
    """Delete all persisted demo records. Returns ``{deleted}``."""
    total = db.execute(select(func.count()).select_from(DemoActivityRecord)).scalar_one()
    db.execute(delete(DemoActivityRecord))
    db.commit()
    return {"deleted": int(total or 0)}


def get_demo_status(db: Session) -> dict[str, Any]:
    """Return demo record counts and last-batch metadata (no prompt text)."""
    record_count = db.execute(
        select(func.count()).select_from(DemoActivityRecord)
    ).scalar_one()
    real_count = db.execute(
        select(func.count())
        .select_from(DemoActivityRecord)
        .where(DemoActivityRecord.is_real_invocation.is_(True))
    ).scalar_one()

    last_row = db.execute(
        select(DemoActivityRecord)
        .order_by(DemoActivityRecord.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    last_generated_at = (
        last_row.created_at.isoformat() if last_row and last_row.created_at else None
    )
    last_batch_id = last_row.batch_id if last_row else None

    return {
        "recordCount": int(record_count or 0),
        "realCount": int(real_count or 0),
        "lastGeneratedAt": last_generated_at,
        "lastBatchId": last_batch_id,
    }


def get_demo_records(
    db: Session,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Return persisted demo records within [start_date, end_date] as activity dicts.

    The returned dicts match the activity-record shape the pipeline expects, so
    they can be merged directly with real CloudTrail records before cost
    application and filtering. ``costEstimate`` is None (populated downstream).

    Each dict also carries ``_invokedAt`` (ISO string, or None for legacy rows
    predating the column) — the ACTUAL time the underlying Bedrock call
    happened, as opposed to ``timestamp`` which is a staged display value. This
    is used only by :func:`suppress_real_duplicates_of_demo_invocations` to
    match and drop the real duplicate that independently arrives via
    CloudWatch/S3/CloudTrail ingestion; it is not part of the public
    activity-record shape and downstream consumers should not rely on it.
    """
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)

    rows = db.execute(select(DemoActivityRecord)).scalars().all()

    crypto = get_crypto()
    records: list[dict[str, Any]] = []
    for row in rows:
        ts = _parse_ts(row.timestamp)
        if ts is not None and not (start_date <= ts <= end_date):
            continue
        input_tokens = row.input_tokens or 0
        output_tokens = row.output_tokens or 0
        cache_read = row.cache_read_tokens or 0
        cache_write = row.cache_write_tokens or 0
        records.append(
            {
                "id": row.id,
                "timestamp": row.timestamp,
                "userIdentity": row.user_identity,
                "userName": row.user_name,
                "modelId": row.model_id,
                "sourceApplication": row.source_application,
                "sourceIp": row.source_ip,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cacheReadTokens": cache_read,
                "cacheWriteTokens": cache_write,
                "totalTokens": input_tokens + output_tokens + cache_read + cache_write,
                "costEstimate": None,
                # Decrypt on read. Legacy plaintext rows (persisted before
                # the encryption-at-rest work) pass through unchanged
                # because the envelope prefix is absent — see
                # crypto_service.decrypt.
                "promptText": crypto.decrypt(row.prompt_text or ""),
                "responseText": crypto.decrypt(row.response_text or ""),
                "isDemo": True,
                "_invokedAt": row.invoked_at.isoformat() if row.invoked_at else None,
                # Non-null only for demo records generated with the "tagged
                # application spend" option whose job was routed through a
                # tagged application inference profile — see
                # generate_demo_data(with_applications=True). Resolved to a
                # real Application/Environment/Team dimension downstream by
                # ProfileTagService via the SAME mechanism real CloudTrail/
                # CloudWatch records use (enrich_records_with_dimensions in
                # routes/activity.py).
                "inferenceProfileArn": row.inference_profile_arn,
            }
        )
    return records


def suppress_real_duplicates_of_demo_invocations(
    demo_records: list[dict[str, Any]],
    real_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop real ingested records that duplicate a demo record's own invocation.

    Every demo record is now backed by a genuine Bedrock Converse call made by
    the backend itself. That call independently shows up via CloudWatch/S3/
    CloudTrail ingestion (under the backend's own task-role identity, at the
    REAL invocation time) — without this step the same invocation is
    double-counted: once as the demo record and once as a bare real record.
    This mirrors ``suppress_chat_placeholders_with_real_invocation`` in
    ``chat_message_service``, but keeps the DEMO record (which already carries
    the friendly demo user/source/staged-date) and drops the REAL duplicate
    instead (the opposite of the chat case, where the placeholder is dropped).

    Matching is by (region-stripped) model id + real record timestamp within a
    small tolerance of the demo record's ``_invokedAt``. Matching is greedy and
    STRICTLY one-to-one in both directions: each demo record can consume at
    most one real record, and each real record can be consumed by at most one
    demo record — so two unrelated real invocations that happen to land near
    the same demo timestamp are not both dropped. Demo rows with no
    ``_invokedAt`` (generated before the column existed) are never matched and
    never cause a real record to be dropped.

    Best-effort: mutates nothing; returns a filtered copy of ``real_records``.
    """
    if not demo_records or not real_records:
        return real_records

    tolerance = timedelta(seconds=_DUPLICATE_MATCH_TOLERANCE_SECONDS)
    demo_targets = []
    for d in demo_records:
        invoked_at = _parse_ts(d.get("_invokedAt"))
        model_id = d.get("modelId")
        if invoked_at is not None and model_id:
            demo_targets.append((invoked_at, model_id))

    if not demo_targets:
        return real_records

    consumed_real_indices: set[int] = set()
    for invoked_at, demo_model in demo_targets:
        for i, real_record in enumerate(real_records):
            if i in consumed_real_indices:
                continue
            if real_record.get("modelId") != demo_model:
                continue
            real_ts = _parse_ts(real_record.get("timestamp"))
            if real_ts is None:
                continue
            if abs(real_ts - invoked_at) <= tolerance:
                consumed_real_indices.add(i)
                break  # this demo target is consumed; move to the next one

    if consumed_real_indices:
        logger.info(
            "Suppressed %d real record(s) duplicating a demo invocation",
            len(consumed_real_indices),
        )

    return [r for i, r in enumerate(real_records) if i not in consumed_real_indices]


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp string, returning None on failure."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def demo_data_present(db: Session) -> tuple[bool, int]:
    """Return (present, count) for the demo banner signal. Tolerates errors."""
    count = db.execute(select(func.count()).select_from(DemoActivityRecord)).scalar_one()
    count = int(count or 0)
    return count > 0, count
