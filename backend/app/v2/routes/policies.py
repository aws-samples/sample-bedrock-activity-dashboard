"""V2 Policy CRUD routes for organizational and data residency policies."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.v2.models import V2AgentPolicy, V2AgentResidencyPolicy
from app.v2.schemas import (
    PolicyCreate,
    PolicyRead,
    PolicyUpdate,
    ResidencyPolicyCreate,
    ResidencyPolicyRead,
    ResidencyPolicyUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/agents", tags=["v2-policies"])


# ---------------------------------------------------------------------------
# Organizational policies
# ---------------------------------------------------------------------------

@router.get("/policies", response_model=list[PolicyRead])
def list_policies(db: Session = Depends(get_db)):
    """List all organizational policies."""
    return db.query(V2AgentPolicy).order_by(V2AgentPolicy.created_at).all()


@router.post(
    "/policies",
    response_model=PolicyRead,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_policy(body: PolicyCreate, db: Session = Depends(get_db)):
    """Create a new organizational policy."""
    policy = V2AgentPolicy(**body.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.put(
    "/policies/{policy_id}",
    response_model=PolicyRead,
    dependencies=[Depends(require_admin)],
)
def update_policy(
    policy_id: UUID,
    body: PolicyUpdate,
    db: Session = Depends(get_db),
):
    """Update an existing organizational policy."""
    policy = db.query(V2AgentPolicy).filter(V2AgentPolicy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found.")
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")
    for key, value in update_data.items():
        setattr(policy, key, value)
    db.commit()
    db.refresh(policy)
    return policy


@router.delete(
    "/policies/{policy_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
def delete_policy(policy_id: UUID, db: Session = Depends(get_db)):
    """Delete an organizational policy."""
    policy = db.query(V2AgentPolicy).filter(V2AgentPolicy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found.")
    db.delete(policy)
    db.commit()


# ---------------------------------------------------------------------------
# Data residency policies
# ---------------------------------------------------------------------------

@router.get("/residency-policies", response_model=list[ResidencyPolicyRead])
def list_residency_policies(db: Session = Depends(get_db)):
    """List all data residency policies."""
    return (
        db.query(V2AgentResidencyPolicy)
        .order_by(V2AgentResidencyPolicy.created_at)
        .all()
    )


@router.post(
    "/residency-policies",
    response_model=ResidencyPolicyRead,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_residency_policy(
    body: ResidencyPolicyCreate, db: Session = Depends(get_db),
):
    """Create a new data residency policy."""
    policy = V2AgentResidencyPolicy(**body.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.put(
    "/residency-policies/{policy_id}",
    response_model=ResidencyPolicyRead,
    dependencies=[Depends(require_admin)],
)
def update_residency_policy(
    policy_id: UUID,
    body: ResidencyPolicyUpdate,
    db: Session = Depends(get_db),
):
    """Update an existing data residency policy."""
    policy = (
        db.query(V2AgentResidencyPolicy)
        .filter(V2AgentResidencyPolicy.id == policy_id)
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="Residency policy not found.")
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")
    for key, value in update_data.items():
        setattr(policy, key, value)
    db.commit()
    db.refresh(policy)
    return policy


@router.delete(
    "/residency-policies/{policy_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
def delete_residency_policy(policy_id: UUID, db: Session = Depends(get_db)):
    """Delete a data residency policy."""
    policy = (
        db.query(V2AgentResidencyPolicy)
        .filter(V2AgentResidencyPolicy.id == policy_id)
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="Residency policy not found.")
    db.delete(policy)
    db.commit()
