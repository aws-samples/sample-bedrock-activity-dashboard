"""Resolve inference-profile ARNs to their Application/Environment/Team tags.

Tags are resolved in priority order — CUR resource tags (populated during CUR
ingestion via :meth:`ProfileTagService.refresh_from_cur`) -> the cached
``inference_profile_tag`` row -> a live best-effort ``bedrock:ListTagsForResource``
call (then cached) -> untagged. Every resolution path is best-effort: any
failure is logged at WARNING and yields ``None`` (untagged) so the analytics
pipeline never fails because a profile could not be resolved.
"""

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import boto3
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models import InferenceProfileTag, utcnow

logger = logging.getLogger(__name__)

# Bedrock tag keys carrying the workload dimensions (case-insensitive match).
_APPLICATION_KEY = "application"
_ENVIRONMENT_KEY = "environment"
_TEAM_KEY = "team"
# Extended tag taxonomy (additive; see docs/tag taxonomy in the FAQ tab).
_AGENT_NAME_KEY = "agentname"
_COST_CENTER_KEY = "costcenter"
_USE_CASE_KEY = "usecase"
_OWNER_KEY = "owner"
_PROJECT_KEY = "project"
_DEPARTMENT_KEY = "department"

# Maps a lowercased Bedrock tag key to its ProfileTags field name, used by
# _map_tags to resolve all 9 dimensions from a single ListTagsForResource
# response (the live API is the richest source — it can carry the full
# taxonomy, unlike CUR resource tags which today only project the original 3).
_TAG_KEY_TO_FIELD: dict[str, str] = {
    _APPLICATION_KEY: "application",
    _ENVIRONMENT_KEY: "environment",
    _TEAM_KEY: "team",
    _AGENT_NAME_KEY: "agent_name",
    _COST_CENTER_KEY: "cost_center",
    _USE_CASE_KEY: "use_case",
    _OWNER_KEY: "owner",
    _PROJECT_KEY: "project",
    _DEPARTMENT_KEY: "department",
}


@dataclass(frozen=True)
class ProfileTags:
    """Workload dimensions resolved from an inference-profile ARN.

    ``application``/``environment``/``team`` are the original 3 dimensions;
    the remaining 6 are the extended taxonomy (all optional, additive).
    """

    application: Optional[str] = None
    environment: Optional[str] = None
    team: Optional[str] = None
    agent_name: Optional[str] = None
    cost_center: Optional[str] = None
    use_case: Optional[str] = None
    owner: Optional[str] = None
    project: Optional[str] = None
    department: Optional[str] = None


class ProfileTagService:
    """Cache-first resolver for inference-profile ARN -> workload tags."""

    def __init__(self) -> None:
        self._bedrock_client = None

    @property
    def client(self):
        """Lazily construct the Bedrock client so importing this module never
        performs any AWS I/O or fails when credentials/region are unavailable."""
        if self._bedrock_client is None:
            region = settings.bedrock_region or settings.aws_region
            session_kwargs: dict[str, str] = {"region_name": region}
            if settings.aws_access_key_id:
                session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
            if settings.aws_secret_access_key:
                session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
            if settings.aws_session_token:
                session_kwargs["aws_session_token"] = settings.aws_session_token
            self._bedrock_client = boto3.client("bedrock", **session_kwargs)
        return self._bedrock_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def resolve(self, arn: str, db: Session) -> Optional[ProfileTags]:
        """Resolve a single ARN, cache-first.

        Returns the stored ``inference_profile_tag`` row when present; on a
        miss, performs a best-effort ``bedrock:ListTagsForResource`` call, caches
        the result (``source="bedrock_api"``), and returns it. Any error (or an
        empty/blank ARN) logs a WARNING and returns ``None`` (untagged); this
        method never raises.
        """
        if not arn:
            return None

        cached = self._read_cached(arn, db)
        if cached is not None:
            return cached

        return self._resolve_from_api(arn, db)

    def resolve_map(self, arns: set[str], db: Session) -> dict[str, ProfileTags]:
        """Batch-resolve a set of ARNs for a single request.

        Cached rows are read in one query; misses are resolved lazily via the
        live API. Only successfully-resolved entries are returned — callers treat
        a missing key as untagged.
        """
        result: dict[str, ProfileTags] = {}
        wanted = {arn for arn in arns if arn}
        if not wanted:
            return result

        rows = (
            db.execute(
                select(InferenceProfileTag).where(InferenceProfileTag.profile_arn.in_(wanted))
            )
            .scalars()
            .all()
        )
        for row in rows:
            result[row.profile_arn] = ProfileTags(
                application=row.application,
                environment=row.environment,
                team=row.team,
                agent_name=row.agent_name,
                cost_center=row.cost_center,
                use_case=row.use_case,
                owner=row.owner,
                project=row.project,
                department=row.department,
            )

        for arn in wanted - result.keys():
            tags = self._resolve_from_api(arn, db)
            if tags is not None:
                result[arn] = tags

        return result

    def refresh_from_cur(
        self,
        db: Session,
        tag_rows: Iterable[tuple[str, Optional[str], Optional[str], Optional[str]]],
    ) -> None:
        """Populate/refresh the cache from CUR-derived resource tags.

        Called during CUR ingestion so profile tags carried by the CUR export
        populate the cache without a live Bedrock call. ``tag_rows`` is an
        iterable of ``(profile_arn, application, environment, team)`` tuples.
        Rows are upserted with ``source="cur"``; blank ARNs are skipped. This
        method is best-effort: any error is logged at WARNING and swallowed so
        ingestion is never broken by tag caching.
        """
        try:
            for row in tag_rows:
                arn = row[0]
                if not arn:
                    continue
                application, environment, team = row[1], row[2], row[3]
                self._upsert(
                    db,
                    arn,
                    ProfileTags(application=application, environment=environment, team=team),
                    source="cur",
                )
            db.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort cache refresh, never raise
            db.rollback()
            logger.warning("Failed to refresh profile tags from CUR: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _read_cached(self, arn: str, db: Session) -> Optional[ProfileTags]:
        """Return the cached tags for ``arn`` if a row exists, else ``None``."""
        row = db.execute(
            select(InferenceProfileTag).where(InferenceProfileTag.profile_arn == arn)
        ).scalar_one_or_none()
        if row is None:
            return None
        return ProfileTags(
            application=row.application,
            environment=row.environment,
            team=row.team,
            agent_name=row.agent_name,
            cost_center=row.cost_center,
            use_case=row.use_case,
            owner=row.owner,
            project=row.project,
            department=row.department,
        )

    def _resolve_from_api(self, arn: str, db: Session) -> Optional[ProfileTags]:
        """Best-effort live resolution via ``bedrock:ListTagsForResource``.

        On success, caches the mapped tags (``source="bedrock_api"``) and returns
        them. On any failure, logs WARNING and returns ``None`` (untagged).
        """
        try:
            response = self.client.list_tags_for_resource(resourceARN=arn)
        except Exception as exc:  # noqa: BLE001 - best-effort, any error -> untagged
            logger.warning("ListTagsForResource failed for %s: %s", arn, exc)
            return None

        tags = self._map_tags(response.get("tags", []))
        # Cache best-effort; a caching failure must not discard resolved tags.
        try:
            self._upsert(db, arn, tags, source="bedrock_api")
            db.commit()
        except Exception as exc:  # noqa: BLE001 - caching is best-effort
            db.rollback()
            logger.warning("Failed to cache profile tags for %s: %s", arn, exc)
        return tags

    @staticmethod
    def _map_tags(raw_tags: list[dict]) -> ProfileTags:
        """Map a Bedrock ``tags`` list ([{key, value}, ...]) to :class:`ProfileTags`.

        Tag-key matching is case-insensitive against the full 8-key taxonomy
        (Application/Environment/Team/AgentName/CostCenter/UseCase/Owner/Project).
        """
        mapped: dict[str, Optional[str]] = dict.fromkeys(_TAG_KEY_TO_FIELD.values())
        for tag in raw_tags:
            key = str(tag.get("key", "")).strip().lower()
            field = _TAG_KEY_TO_FIELD.get(key)
            if field is not None:
                mapped[field] = tag.get("value")
        return ProfileTags(**mapped)

    @staticmethod
    def _upsert(db: Session, arn: str, tags: ProfileTags, source: str) -> None:
        """Idempotent upsert of a cache row keyed on the unique ``profile_arn``.

        The extended taxonomy (agent_name/cost_center/use_case/owner/project/
        department) is only ever populated via the live Bedrock API today (CUR
        resource tags currently project just the original 3 dimensions — see
        :meth:`refresh_from_cur`). To avoid a CUR-sourced refresh silently
        wiping out extended tags a prior ``bedrock_api`` resolution already
        cached, each extended column is updated with
        ``COALESCE(new_value, existing_value)`` on conflict instead of a blind
        overwrite. The core 3 dimensions keep the original overwrite semantics
        (CUR is the authoritative/refreshed source for those).
        """
        now = utcnow()
        table = InferenceProfileTag.__table__
        stmt = pg_insert(table).values(
            profile_arn=arn,
            application=tags.application,
            environment=tags.environment,
            team=tags.team,
            agent_name=tags.agent_name,
            cost_center=tags.cost_center,
            use_case=tags.use_case,
            owner=tags.owner,
            project=tags.project,
            department=tags.department,
            resolved_at=now,
            source=source,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["profile_arn"],
            set_={
                "application": tags.application,
                "environment": tags.environment,
                "team": tags.team,
                "agent_name": func.coalesce(stmt.excluded.agent_name, table.c.agent_name),
                "cost_center": func.coalesce(stmt.excluded.cost_center, table.c.cost_center),
                "use_case": func.coalesce(stmt.excluded.use_case, table.c.use_case),
                "owner": func.coalesce(stmt.excluded.owner, table.c.owner),
                "project": func.coalesce(stmt.excluded.project, table.c.project),
                "department": func.coalesce(stmt.excluded.department, table.c.department),
                "resolved_at": now,
                "source": source,
            },
        )
        db.execute(stmt)
