"""Team CRUD API routes for the Bedrock Activity Dashboard."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.database import get_db
from app.models import Team, TeamMembership
from app.routes.activity import source_activity_records_with_meta
from app.services.aggregation_service import AggregationService
from app.services.cloudtrail_service import CloudTrailError
from app.services.filter_service import FilterService, parse_iso_to_utc

router = APIRouter(prefix="/api/teams", tags=["teams"])

# Shared service instances (mirrors the activity route pattern).
_aggregation_service = AggregationService()
_filter_service = FilterService()

# Team usage totals default to the last 30 days — a more meaningful horizon for
# team budgets than the 7-day default the activity views use.
_DEFAULT_TEAM_WINDOW_DAYS = 30

# Zeroed usage totals used for non-admin callers and create/update/delete
# responses (which have no reporting-window context).
_ZERO_TOTALS: dict[str, Any] = {"totalTokens": 0, "totalCostEstimate": None}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateTeamRequest(BaseModel):
    name: str
    description: Optional[str] = None
    members: list[str] = Field(default_factory=list)


class UpdateTeamRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    members: Optional[list[str]] = None


class TeamMemberResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    memberCount: int
    members: list[str]
    totalTokens: int
    totalCostEstimate: Optional[float]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _team_to_response(team: Team, totals: Optional[dict[str, Any]] = None) -> dict:
    """Convert a Team ORM object to the API response dict.

    ``totals`` carries precomputed usage numbers
    (``{"totalTokens": int, "totalCostEstimate": float | None}``). When omitted
    (create/update/delete responses, or non-admin callers) usage defaults to
    zero — only the LIST endpoint computes real windowed totals.
    """
    members = [m.user_identity for m in team.memberships]
    totals = totals or _ZERO_TOTALS
    return {
        "id": str(team.id),
        "name": team.name,
        "description": team.description,
        "memberCount": len(members),
        "members": members,
        "totalTokens": totals["totalTokens"],
        "totalCostEstimate": totals["totalCostEstimate"],
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
def list_teams(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all teams with member counts and real usage totals.

    Usage totals (``totalTokens`` / ``totalCostEstimate``) are aggregated over a
    reporting window: the caller-supplied ``[start_date, end_date]`` when both
    are provided, otherwise the last 30 days.

    SECURITY: only admins receive real cross-user usage totals. Non-admin callers
    get zero/None totals so the team view never leaks one user's usage to
    another — consistent with the per-user scoping the activity routes enforce in
    ``_scope_to_user``. The team LIST itself stays visible to any authenticated
    user; only the numbers are gated.
    """
    teams = db.query(Team).order_by(Team.name).all()

    # Callers without cross-user view rights: return the team list with zeroed
    # usage (no cross-user leakage).
    if current_user is None or not current_user.can_view_all:
        return [_team_to_response(t) for t in teams]

    # Resolve the reporting window (default: last 30 days).
    if start_date and end_date:
        start_dt = parse_iso_to_utc(start_date)
        end_dt = parse_iso_to_utc(end_date, end_of_day_if_bare=True)
    else:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=_DEFAULT_TEAM_WINDOW_DAYS)
    _filter_service.validate_date_range(start_dt, end_dt)

    # Source ALL records once over the window using the SAME pipeline the
    # activity route uses, then aggregate per team via flexible member↔record
    # matching (usernames/emails vs full IAM ARNs).
    try:
        ct_records, chat_records, _, _ = source_activity_records_with_meta(start_dt, end_dt, db)
    except CloudTrailError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": exc.error_code, "message": exc.message},
        )

    all_records = ct_records + chat_records
    responses = []
    for team in teams:
        members = [m.user_identity for m in team.memberships]
        totals = _aggregation_service.aggregate_team_totals(all_records, members)
        responses.append(_team_to_response(team, totals))
    return responses


@router.post("", status_code=201, dependencies=[Depends(require_admin)])
def create_team(body: CreateTeamRequest, db: Session = Depends(get_db)):
    """Create a new team and optionally assign members."""
    # Check for duplicate name
    existing = db.query(Team).filter(Team.name == body.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Team with name '{body.name}' already exists.")

    team = Team(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
    )
    db.add(team)
    db.flush()

    # Add members
    for user_identity in body.members:
        membership = TeamMembership(
            id=uuid.uuid4(),
            team_id=team.id,
            user_identity=user_identity,
        )
        db.add(membership)

    db.commit()
    db.refresh(team)
    return _team_to_response(team)


@router.put("/{team_id}", dependencies=[Depends(require_admin)])
def update_team(team_id: str, body: UpdateTeamRequest, db: Session = Depends(get_db)):
    """Update a team's name, description, or members."""
    try:
        tid = uuid.UUID(team_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid team ID format.")

    team = db.query(Team).filter(Team.id == tid).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    if body.name is not None:
        # Check uniqueness if name is changing
        if body.name != team.name:
            dup = db.query(Team).filter(Team.name == body.name).first()
            if dup:
                raise HTTPException(status_code=409, detail=f"Team with name '{body.name}' already exists.")
        team.name = body.name

    if body.description is not None:
        team.description = body.description

    if body.members is not None:
        # Replace membership list: remove old, add new
        db.query(TeamMembership).filter(TeamMembership.team_id == tid).delete()
        for user_identity in body.members:
            membership = TeamMembership(
                id=uuid.uuid4(),
                team_id=team.id,
                user_identity=user_identity,
            )
            db.add(membership)

    db.commit()
    db.refresh(team)
    return _team_to_response(team)


@router.delete("/{team_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_team(team_id: str, db: Session = Depends(get_db)):
    """Delete a team and its memberships."""
    try:
        tid = uuid.UUID(team_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid team ID format.")

    team = db.query(Team).filter(Team.id == tid).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    db.delete(team)
    db.commit()
    return None
