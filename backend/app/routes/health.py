"""System health check endpoint — returns status of all backend dependencies."""

import logging
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import require_admin
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/health", tags=["health"])


class CognitoConfig(BaseModel):
    """Cognito settings the SPA needs to initiate OAuth2 / PKCE sign-in.

    All values come from environment variables populated by the CDK Backend
    stack from the Cognito resources owned by the Data stack, so nothing is
    pinned to a specific AWS account. Consumed by
    ``frontend/src/auth/cognito.ts`` at app startup.

    ``identityProvider`` is optional. When non-empty, the SPA appends it as
    the ``identity_provider`` query parameter on the Cognito hosted UI's
    ``/oauth2/authorize`` call, deep-linking users into that federated IdP
    (SAML, OIDC, or IAM Identity Center) instead of the hosted UI account
    chooser. Empty string preserves the standard hosted-UI flow.
    """

    userPoolId: str
    clientId: str
    domain: str
    region: str
    identityProvider: str = ""


class ConfigResponse(BaseModel):
    """Lightweight runtime config exposed to the frontend.

    Served by GET /api/health/config, which is public (no auth) so the SPA
    can bootstrap its Cognito redirect before any user is authenticated.
    """

    demoMode: bool
    appName: str
    # True when at least one persisted demo record exists. Drives the demo
    # banner now that always-on request-time injection is disabled.
    demoDataPresent: bool = False
    demoRecordCount: int = 0
    # Cognito hosted-UI settings — empty strings when auth is disabled or the
    # backend has not yet been configured with a user pool.
    cognito: CognitoConfig
    # Regions CloudTrailService fans ingestion out across (CLOUDTRAIL_REGIONS,
    # falling back to just the deploy region). Drives the frontend's region
    # filter dropdown so the SPA never needs a rebuild when the deployed
    # region set changes.
    availableRegions: list[str] = []


def _check_database() -> dict[str, Any]:
    """Check PostgreSQL connectivity."""
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return {"name": "PostgreSQL", "status": "healthy", "detail": settings.database_url.split("@")[-1] if "@" in settings.database_url else "localhost"}
    except Exception as e:
        return {"name": "PostgreSQL", "status": "unhealthy", "detail": str(e)[:200]}


def _check_aws_credentials() -> dict[str, Any]:
    """Check AWS STS caller identity."""
    try:
        session = boto3.Session(region_name=settings.aws_region)
        sts = session.client("sts", config=Config(connect_timeout=5, read_timeout=5))
        identity = sts.get_caller_identity()
        return {
            "name": "AWS Credentials",
            "status": "healthy",
            "detail": f"Account {identity['Account']}, ARN {identity['Arn']}",
        }
    except Exception as e:
        return {"name": "AWS Credentials", "status": "unhealthy", "detail": str(e)[:200]}


def _check_cloudwatch_logs() -> dict[str, Any]:
    """Check CloudWatch Logs log group exists."""
    try:
        session = boto3.Session(region_name=settings.aws_region)
        logs = session.client("logs", config=Config(connect_timeout=5, read_timeout=5))
        resp = logs.describe_log_groups(logGroupNamePrefix=settings.bedrock_log_group, limit=1)
        groups = resp.get("logGroups", [])
        if groups:
            return {"name": "CloudWatch Logs", "status": "healthy", "detail": f"Log group: {settings.bedrock_log_group}"}
        return {"name": "CloudWatch Logs", "status": "warning", "detail": f"Log group '{settings.bedrock_log_group}' not found — enable Bedrock model invocation logging"}
    except Exception as e:
        return {"name": "CloudWatch Logs", "status": "unhealthy", "detail": str(e)[:200]}


def _check_cloudtrail() -> dict[str, Any]:
    """Check CloudTrail can be queried."""
    try:
        session = boto3.Session(region_name=settings.aws_region)
        ct = session.client("cloudtrail", config=Config(connect_timeout=5, read_timeout=5))
        ct.lookup_events(MaxResults=1)
        return {"name": "CloudTrail", "status": "healthy", "detail": f"Region: {settings.aws_region}"}
    except Exception as e:
        return {"name": "CloudTrail", "status": "unhealthy", "detail": str(e)[:200]}


def _check_bedrock() -> dict[str, Any]:
    """Check Bedrock model access."""
    try:
        session = boto3.Session(region_name=settings.bedrock_region or settings.aws_region)
        bedrock = session.client("bedrock-runtime", config=Config(connect_timeout=5, read_timeout=5))
        # Just check the client can be created and endpoint resolves
        bedrock.meta.endpoint_url
        return {"name": "Bedrock Runtime", "status": "healthy", "detail": f"Model: {settings.bedrock_model_id}, Region: {settings.bedrock_region or settings.aws_region}"}
    except Exception as e:
        return {"name": "Bedrock Runtime", "status": "unhealthy", "detail": str(e)[:200]}


def _check_s3_logs() -> dict[str, Any]:
    """Check S3 log bucket accessibility."""
    try:
        session = boto3.Session(region_name=settings.aws_region)
        # Auto-detect bucket from Bedrock logging config
        bedrock_client = session.client("bedrock", config=Config(connect_timeout=5, read_timeout=5))
        log_config = bedrock_client.get_model_invocation_logging_configuration()
        s3_config = log_config.get("loggingConfig", {}).get("s3Config", {})
        bucket = s3_config.get("bucketName", "")
        prefix = s3_config.get("keyPrefix", "")
        if not bucket:
            return {"name": "S3 Log Bucket", "status": "warning", "detail": "No S3 bucket configured in Bedrock logging settings"}
        s3 = session.client("s3", config=Config(connect_timeout=5, read_timeout=5))
        s3.head_bucket(Bucket=bucket)
        return {"name": "S3 Log Bucket", "status": "healthy", "detail": f"Bucket: {bucket}, Prefix: {prefix}"}
    except Exception as e:
        return {"name": "S3 Log Bucket", "status": "warning", "detail": str(e)[:200]}


def _demo_data_present() -> tuple[bool, int]:
    """Quick DB count of persisted demo records; tolerate DB errors -> (False, 0)."""
    try:
        from app.services.demo_service import demo_data_present

        db = SessionLocal()
        try:
            return demo_data_present(db)
        finally:
            db.close()
    except Exception:
        logger.warning("Could not compute demoDataPresent; defaulting to False", exc_info=True)
        return False, 0


@router.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    """Return lightweight runtime config flags for the frontend."""
    from app.services.cloudtrail_service import parse_regions

    present, count = _demo_data_present()
    available_regions = parse_regions(settings.cloudtrail_regions) or [settings.aws_region]
    return ConfigResponse(
        demoMode=settings.demo_mode,
        appName=settings.app_name,
        demoDataPresent=present,
        demoRecordCount=count,
        cognito=CognitoConfig(
            userPoolId=settings.cognito_user_pool_id,
            clientId=settings.cognito_client_id,
            domain=settings.cognito_domain,
            region=settings.cognito_region or settings.aws_region,
            identityProvider=settings.cognito_identity_provider,
        ),
        availableRegions=available_regions,
    )


@router.get("/systems", dependencies=[Depends(require_admin)])
def get_system_health():
    """Return health status of all backend dependencies. Admin only."""
    # NOTE (Req 9.5 — cost-accountability-governance): The cost-governance
    # prerequisites — a configured CUR 2.0 export (Athena) and tagged
    # inference profiles — are INTENTIONALLY NOT checked here. They are
    # optional, out-of-band data sources that degrade gracefully when absent,
    # so their unavailability must never flip overall system health to
    # degraded/unhealthy. Their availability is surfaced only via
    # `GET /api/governance/cur/status`. Do not add a CUR/Athena/inference-
    # profile check to this list.
    checks = [
        _check_database(),
        _check_aws_credentials(),
        _check_cloudwatch_logs(),
        _check_cloudtrail(),
        _check_s3_logs(),
        _check_bedrock(),
    ]

    overall = "healthy"
    for c in checks:
        if c["status"] == "unhealthy":
            overall = "unhealthy"
            break
        if c["status"] == "warning" and overall == "healthy":
            overall = "degraded"

    return {
        "overall": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "region": settings.aws_region,
        "systems": checks,
    }
