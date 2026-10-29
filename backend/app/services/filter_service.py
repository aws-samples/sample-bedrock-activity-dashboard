"""Filter service for applying date range, user, team, and source filters to activity records."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import TeamMembership


def parse_iso_to_utc(value: str, *, end_of_day_if_bare: bool = False) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime.

    Correctly handles both naive and offset-bearing inputs:

    - If the parsed datetime is naive (no tzinfo), it is assumed to already be
      UTC and stamped as such via ``replace(tzinfo=utc)``.
    - If the parsed datetime carries a tz offset, it is converted to UTC via
      ``astimezone(utc)`` so the underlying instant is preserved (rather than
      silently shifted, which ``replace(tzinfo=utc)`` would do).

    Args:
        value: An ISO-8601 date or datetime string.
        end_of_day_if_bare: When True and ``value`` is a bare date (length <= 10,
            e.g. ``"2024-01-15"``), the time is set to 23:59:59 UTC so the day is
            treated inclusively as an end-of-range boundary.

    Returns:
        A timezone-aware ``datetime`` in UTC.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    # Preserve the existing bare-date end-of-day behavior for end_date. A bare
    # date parses to midnight; bump it to the last second of the day so the
    # whole day is included in an inclusive end boundary.
    if end_of_day_if_bare and len(value) <= 10:
        parsed = parsed.replace(hour=23, minute=59, second=59)

    return parsed


@dataclass
class ActivityFilters:
    """Filter parameters for activity records."""

    start_date: datetime
    end_date: datetime
    user_identities: list[str] = field(default_factory=list)
    team_id: str | None = None
    source_application: str | None = None
    environment: str | None = None
    application: str | None = None
    # Regions to include (matches the record's ``region`` field — the
    # region a Bedrock invocation log entry was fetched FROM, not
    # necessarily where the model ran; see cloudtrail_service.py). Empty
    # list means no region filter (all ingested regions included), same
    # convention as ``user_identities``.
    regions: list[str] = field(default_factory=list)


class FilterService:
    """Applies filters to activity records with AND logic."""

    @staticmethod
    def validate_date_range(start_date: datetime, end_date: datetime) -> None:
        """Validate that start_date is not after end_date.

        Raises:
            HTTPException: 422 if start_date > end_date.
        """
        if start_date > end_date:
            raise HTTPException(
                status_code=422,
                detail="Invalid date range: start date must not be after end date.",
            )

    @staticmethod
    def get_default_date_range() -> tuple[datetime, datetime]:
        """Return the default date range (last 7 days).

        Returns:
            Tuple of (start_date, end_date) where end_date is now (UTC)
            and start_date is 7 days before.
        """
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)
        return start_date, end_date

    @staticmethod
    def resolve_team_members(team_id: str, db: Session) -> list[str]:
        """Look up team memberships and return user identities for a team.

        Args:
            team_id: UUID string of the team.
            db: SQLAlchemy database session.

        Returns:
            List of user_identity strings belonging to the team.
        """
        memberships = (
            db.query(TeamMembership.user_identity)
            .filter(TeamMembership.team_id == team_id)
            .all()
        )
        return [m.user_identity for m in memberships]

    @staticmethod
    def apply_filters(
        records: list[dict[str, Any]],
        filters: ActivityFilters,
    ) -> list[dict[str, Any]]:
        """Filter records by date range, user identities, source application, environment, application.

        All filters are combined with AND logic. Empty user_identities list
        means no user filter is applied.

        Args:
            records: List of activity record dicts.
            filters: ActivityFilters with filter parameters.

        Returns:
            Filtered list of activity record dicts.
        """
        result = []
        start_iso = filters.start_date.isoformat()
        end_iso = filters.end_date.isoformat()

        # Pre-normalize the non-blank user filter values once (case-insensitive
        # matching). Blank/whitespace-only values are ignored so they never
        # accidentally match every record via substring logic.
        user_values = [
            v.strip().lower()
            for v in filters.user_identities
            if v and v.strip()
        ]
        region_values = [
            v.strip().lower()
            for v in filters.regions
            if v and v.strip()
        ]
        source_value = (
            filters.source_application.strip().lower()
            if filters.source_application and filters.source_application.strip()
            else None
        )
        environment_value = (
            filters.environment.strip().lower()
            if filters.environment and filters.environment.strip()
            else None
        )
        application_value = (
            filters.application.strip().lower()
            if filters.application and filters.application.strip()
            else None
        )

        for record in records:
            # Date range filter
            ts = record.get("timestamp", "")
            if ts < start_iso or ts > end_iso:
                continue

            # User identity filter (flexible, case-insensitive). A record
            # matches if ANY filter value equals, or is a substring of, the
            # record's userIdentity (full IAM ARN) or userName. This lets a
            # human-entered name/username ("amelia") match an ARN like
            # "arn:aws:iam::...:user/demo.amelia.chen" while still matching
            # exact full-ARN values.
            if user_values:
                if not FilterService._matches_user(record, user_values):
                    continue

            # Region filter (case-insensitive, exact match against the region
            # a record was fetched from). Records ingested before the
            # multi-region refactor (or from any code path that hasn't been
            # updated to tag `region`) have no `region` field — such records
            # never match a region filter, matching the empty-list = "no
            # filter" convention used everywhere else in this method.
            if region_values:
                record_region = record.get("region", "")
                if not record_region or record_region.strip().lower() not in region_values:
                    continue

            # Source application filter (case-insensitive equality).
            if source_value is not None:
                if record.get("sourceApplication", "").strip().lower() != source_value:
                    continue

            # Environment filter (case-insensitive equality). Records carry
            # "unknown" when profile-tag resolution yields no environment, which
            # still filters/groups correctly.
            if environment_value is not None:
                if record.get("environment", "").strip().lower() != environment_value:
                    continue

            # Application filter (case-insensitive equality). Same "unknown"
            # degraded-state handling as environment.
            if application_value is not None:
                if record.get("application", "").strip().lower() != application_value:
                    continue

            result.append(record)

        return result

    @staticmethod
    def member_matches_record(member: str, record: dict[str, Any]) -> bool:
        """Return True if a single member/user value flex-matches a record.

        This is the canonical flexible matching used by BOTH the activity user
        filter and team-usage aggregation, so the two agree on which records
        belong to a person. The ``member`` value is stripped and lower-cased; a
        blank/whitespace-only value never matches (so it can't accidentally
        match every record via substring logic).

        A match occurs when, comparing case-insensitively, ``member``:

        - equals the record's ``userIdentity`` or ``userName``, OR
        - is a substring of the record's ``userIdentity`` or ``userName``.

        This lets a human-entered username/email ("demo.amelia.chen",
        "amelia@example.com") match a full IAM ARN like
        "arn:aws:iam::...:user/demo.amelia.chen". Blank record fields never
        match.
        """
        value = member.strip().lower() if member else ""
        if not value:
            return False

        identity = record.get("userIdentity", "")
        identity = identity.strip().lower() if identity else ""
        user_name = record.get("userName", "")
        user_name = user_name.strip().lower() if user_name else ""

        if identity and (value == identity or value in identity):
            return True
        if user_name and (value == user_name or value in user_name):
            return True
        return False

    @staticmethod
    def _matches_user(record: dict[str, Any], user_values: list[str]) -> bool:
        """Return True if the record matches any of the given user filter values.

        ``user_values`` must already be normalized (stripped, lower-cased, and
        non-blank). Delegates to :meth:`member_matches_record` so user filtering
        and team aggregation share one matching implementation.
        """
        return any(FilterService.member_matches_record(value, record) for value in user_values)

    @staticmethod
    def filter_by_team(
        records: list[dict[str, Any]],
        team_id: str,
        db: Session,
    ) -> list[dict[str, Any]]:
        """Resolve team members then filter records to only those users.

        Args:
            records: List of activity record dicts.
            team_id: UUID string of the team.
            db: SQLAlchemy database session.

        Returns:
            Filtered list containing only records from team members.
        """
        members = FilterService.resolve_team_members(team_id, db)
        if not members:
            return []
        member_set = set(members)
        return [r for r in records if r.get("userIdentity", "") in member_set]
