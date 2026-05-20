from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import current_user, require_admin
from app.db import get_db
from app.models import AgentRegistration, Org, User
from app.tenancy import scoped

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/me")
def whoami(user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = db.get(Org, user.org_id)
    return {"user": {"email": user.email, "name": user.name, "role": user.role},
            "org": {"id": org.id, "name": org.name, "domain": org.domain}}


@router.get("/agents")
def list_agents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    regs = db.execute(scoped(AgentRegistration, user.org_id)).scalars().all()
    return [{"key": r.key, "display_name": r.display_name, "kind": r.kind,
             "enabled": r.enabled, "schedule_cron": r.schedule_cron} for r in regs]


class RegisterAgentIn(BaseModel):
    key: str
    display_name: str
    kind: str = "builtin"
    endpoint: str = ""
    schedule_cron: str | None = None
    config: dict = Field(default_factory=dict)


@router.post("/agents")
def register_agent(body: RegisterAgentIn, user: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    reg = AgentRegistration(org_id=user.org_id, key=body.key, display_name=body.display_name,
                            kind=body.kind, endpoint=body.endpoint,
                            schedule_cron=body.schedule_cron, config=body.config, enabled=True)
    db.add(reg)
    db.commit()
    db.refresh(reg)
    return {"id": reg.id, "key": reg.key, "kind": reg.kind, "enabled": reg.enabled}
