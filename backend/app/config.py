import json
import logging
from pydantic_settings import BaseSettings
from typing import Optional

logger = logging.getLogger(__name__)


def _resolve_database_url(
    database_url: str,
    database_host: Optional[str],
    database_port: str,
    database_name: str,
    db_secret_arn: Optional[str],
    aws_region: str,
) -> str:
    """Build DATABASE_URL from components + Secrets Manager if running in AWS."""
    # If a full DATABASE_URL is provided and we're not in AWS, use it directly
    if not database_host or not db_secret_arn:
        return database_url

    # Fetch credentials from Secrets Manager
    try:
        import boto3
        # The ECS secret injection gives us the full JSON string
        secret_data = json.loads(db_secret_arn)
        username = secret_data.get("username", "postgres")
        password = secret_data.get("password", "")
    except (json.JSONDecodeError, TypeError):
        # If it's an actual ARN, fetch from Secrets Manager
        try:
            import boto3
            client = boto3.client("secretsmanager", region_name=aws_region)
            resp = client.get_secret_value(SecretId=db_secret_arn)
            secret_data = json.loads(resp["SecretString"])
            username = secret_data.get("username", "postgres")
            password = secret_data.get("password", "")
        except Exception as e:
            logger.warning("Could not fetch DB secret: %s. Falling back to default URL.", e)
            return database_url

    # Explicit "+psycopg2" driver: SQLAlchemy >=2.1 resolves a bare
    # "postgresql://" scheme to the psycopg (v3) dialect by default, but this
    # image only installs psycopg2-binary (requirements.txt), which raised
    # "ModuleNotFoundError: No module named 'psycopg'" on container start.
    return f"postgresql+psycopg2://{username}:{password}@{database_host}:{database_port}/{database_name}"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database — full URL (used for local dev). Explicit "+psycopg2" driver —
    # see the matching comment in _resolve_database_url for why the bare
    # "postgresql://" scheme breaks on SQLAlchemy >=2.1 with only
    # psycopg2-binary installed.
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/bedrock_dashboard"

    # Database — component parts (used in AWS, built from ECS env vars)
    database_host: Optional[str] = None
    database_port: str = "5432"
    database_name: str = "bedrock_dashboard"
    db_secret_arn: Optional[str] = None

    # AWS
    aws_region: str = "us-east-1"
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_session_token: Optional[str] = None

    # Application
    app_name: str = "Bedrock Activity Dashboard"
    debug: bool = False
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Demo mode — when true, inject synthetic activity records at request time so
    # every analytics tab shows meaningful, non-zero data. No DB migration.
    demo_mode: bool = False

    # SNS topic for agent notifications (cost anomaly alerter, weekly digest)
    sns_topic_arn: Optional[str] = None

    # CloudTrail — comma-separated list of regions to query for Bedrock events
    cloudtrail_regions: str = ""

    # Bedrock invocation log group (CloudWatch Logs)
    bedrock_log_group: str = "/aws/bedrock/model-invocations"

    # Bedrock invocation log S3 destination. Set explicitly from the Backend
    # CDK stack so CloudTrailService does not have to auto-detect at boot —
    # auto-detect races the Step 3.5 put-model-invocation-logging-configuration
    # call on a fresh deploy and can cache stale values that survive for the
    # container's lifetime. When unset, CloudTrailService falls back to
    # auto-detecting via bedrock:GetModelInvocationLoggingConfiguration so
    # existing deploys and out-of-band logging setups keep working.
    bedrock_s3_log_bucket: Optional[str] = None
    bedrock_s3_log_key_prefix: Optional[str] = None

    # Bedrock Agent
    # Default: Amazon Nova Pro — cost-effective model for the AI Agent Chat.
    # Override via BEDROCK_MODEL_ID env var, e.g.:
    #   us.anthropic.claude-sonnet-4-6                       (Claude Sonnet 4.6)
    #   us.anthropic.claude-haiku-4-5-20251001-v1:0          (cheaper, faster)
    #   us.amazon.nova-lite-v1:0                             (Nova Lite — cheapest)
    bedrock_model_id: str = "us.amazon.nova-pro-v1:0"
    bedrock_region: Optional[str] = None  # defaults to aws_region if not set

    # Cognito authentication / authorization. These are populated from ECS
    # task env vars (see infrastructure/lib/backend-stack.ts), which in turn
    # get them from the Cognito resources owned by the Data stack — nothing
    # is pinned to a specific AWS account. For local development, either set
    # AUTH_ENABLED=false to bypass verification entirely, or export the
    # COGNITO_* env vars to point at a dev pool.
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_region: str = ""
    # Hosted UI base URL, e.g., https://<prefix>.auth.<region>.amazoncognito.com.
    # Exposed to the SPA via GET /api/health/config so the frontend does not
    # bake any Cognito endpoint into its build.
    cognito_domain: str = ""
    # Optional Cognito federated identity provider name. When set, the SPA
    # passes it as the ``identity_provider`` parameter on the
    # ``/oauth2/authorize`` call so users are deep-linked into that provider
    # (SAML, OIDC, or IAM Identity Center) instead of landing on the Cognito
    # hosted-UI account chooser. Only meaningful when deploying against a
    # pool that has a federated IdP configured — typically the BYO path
    # (see infrastructure/lib/data-stack.ts). Empty default keeps the
    # standard hosted-UI login flow for the fresh-account path.
    cognito_identity_provider: str = ""
    # Safety valve: when False, auth verification is bypassed entirely (all
    # requests treated as an admin-equivalent "system" user). Toggle via env.
    auth_enabled: bool = True

    # CUR 2.0 authoritative billed-cost ingestion via Athena.
    # All optional so their absence drives graceful degradation to
    # estimated-only cost figures (the app never configures these AWS
    # prerequisites, it only consumes them).
    athena_database: Optional[str] = None  # ATHENA_DATABASE — Glue DB overlaying the CUR export
    athena_table: Optional[str] = None  # ATHENA_TABLE — CUR table name in the Glue DB
    athena_workgroup: str = "primary"  # ATHENA_WORKGROUP
    athena_output_s3: Optional[str] = None  # ATHENA_OUTPUT_S3 — query-results location, s3://bucket/prefix
    athena_region: Optional[str] = None  # ATHENA_REGION — defaults to aws_region when unset
    cur_staleness_hours: int = 48  # CUR_STALENESS_HOURS — billed-data staleness window
    cur_ingest_enabled: bool = True  # CUR_INGEST_ENABLED — kill switch for ingestion

    # AgentCore Runtime consumption cost tracking. Computed live from the
    # CloudWatch metrics AgentCore Runtime vends per agent — no Athena/CUR
    # dependency. Rates default to the published us-west-2 on-demand prices
    # (https://aws.amazon.com/bedrock/agentcore/pricing/) and are overridable
    # so a price change or a different region never requires a code change.
    agentcore_cost_tracking_enabled: bool = True  # AGENTCORE_COST_TRACKING_ENABLED
    agentcore_vcpu_hourly_rate: float = 0.0895  # AGENTCORE_VCPU_HOURLY_RATE — $/vCPU-hour
    agentcore_memory_gb_hourly_rate: float = 0.00945  # AGENTCORE_MEMORY_GB_HOURLY_RATE — $/GB-hour

    # AgentCore Runtime discovery: account-wide visibility scan (read-only,
    # separate from the governed v2_agent_configs registry). Comma-separated
    # region list, same convention as cloudtrail_regions. Runs daily via the
    # scheduled path (python -m app.services.agentcore_discovery_service);
    # also triggerable on demand via POST /api/agentcore-discovery/refresh.
    agentcore_discovery_enabled: bool = True  # AGENTCORE_DISCOVERY_ENABLED — kill switch
    agentcore_discovery_regions: str = "us-west-2,us-east-1"  # AGENTCORE_DISCOVERY_REGIONS

    # Prompt-history at-rest encryption key — a 32-byte, URL-safe-base64
    # Fernet key (see ``cryptography.fernet.Fernet.generate_key()``). When
    # set, Agent Chat message content is encrypted before being written to
    # the ``chat_messages`` table and transparently decrypted on read; when
    # unset or empty, writes fall back to plaintext storage AND the admin
    # Settings UI surfaces an explicit warning so operators know their
    # prompt data is not encrypted at rest.
    #
    # Rotate by setting a new value — only new writes use the new key.
    # Legacy plaintext rows (or rows encrypted under an older key the
    # service no longer has) are returned verbatim to the UI; see
    # app.services.crypto_service for the compatibility layer.
    #
    # Injected from AWS Secrets Manager in prod via the Backend CDK stack
    # (see infrastructure/lib/backend-stack.ts). For local dev, generate a
    # key with ``python -c "from cryptography.fernet import Fernet;
    # print(Fernet.generate_key().decode())"`` and export it as the env var.
    prompt_history_encryption_key: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


settings = Settings()

# Resolve the database URL from components if running in AWS
settings.database_url = _resolve_database_url(
    database_url=settings.database_url,
    database_host=settings.database_host,
    database_port=settings.database_port,
    database_name=settings.database_name,
    db_secret_arn=settings.db_secret_arn,
    aws_region=settings.aws_region,
)
