import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.auth import get_current_user
from app.database import SessionLocal
from app.routes.activity import router as activity_router
from app.routes.prompts import router as prompts_router
from app.routes.teams import router as teams_router
from app.routes.pricing import router as pricing_router, seed_default_pricing
from app.routes.agent import router as agent_router
from app.v2.routes.agents import router as v2_agents_router
from app.v2.routes.policies import router as v2_policies_router
from app.routes.chargeback import router as chargeback_router
from app.routes.demo import router as demo_router
from app.routes.governance import router as governance_router
from app.routes.agentcore_cost import router as agentcore_cost_router
from app.routes.settings import router as settings_router
from app.routes.health import router as health_router
from app.v2.seed_agent_configs import seed_agent_configs


def _configure_logging() -> None:
    """Configure app-wide INFO logging so logger.info() reaches CloudWatch.

    Without this, only uvicorn's own loggers emit output — every app-level
    logger.info() call is silently dropped (root logger defaults to WARNING
    with no handler). Guarded so a root logger already configured by the
    hosting process (e.g. uvicorn workers re-importing the module) isn't
    double-configured, which would duplicate every log line. Uvicorn's own
    loggers keep their handlers (propagate=False), so access logs still work.
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    else:
        root.setLevel(logging.INFO)
    # Keep third-party client noise out of CloudWatch.
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed default pricing + agent configs on startup. Best-effort and
    # non-fatal (a DB-less environment must still boot), but a failure here
    # leaves every cost as N/A, so log it at ERROR with context so the cause is
    # diagnosable from CloudWatch Logs rather than silently swallowed.
    try:
        db = SessionLocal()
        try:
            seed_default_pricing(db)
            seed_agent_configs(db)
        finally:
            db.close()
    except Exception:
        logger.error(
            "Startup seeding (pricing/agent configs) failed; costs may show as "
            "N/A until reseeded. Continuing startup (non-fatal).",
            exc_info=True,
        )

    yield


app = FastAPI(
    title=settings.app_name,
    description="API for Bedrock Activity Dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers.
# All API routers require authentication via the router-level dependency.
# The health_router (/api/health/*) and the top-level /health check below stay
# OPEN so the ALB health check and CORS preflight are never blocked.
_auth = [Depends(get_current_user)]

app.include_router(activity_router, dependencies=_auth)
app.include_router(prompts_router, dependencies=_auth)
app.include_router(teams_router, dependencies=_auth)
app.include_router(pricing_router, dependencies=_auth)
app.include_router(agent_router, dependencies=_auth)
app.include_router(v2_agents_router, dependencies=_auth)
app.include_router(v2_policies_router, dependencies=_auth)
app.include_router(chargeback_router, dependencies=_auth)
app.include_router(demo_router, dependencies=_auth)
app.include_router(governance_router, dependencies=_auth)
app.include_router(agentcore_cost_router, dependencies=_auth)
app.include_router(settings_router, dependencies=_auth)
app.include_router(health_router)


@app.get("/health")
def health_check():
    return {"status": "healthy"}
