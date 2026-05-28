"""
GET /api/memory — the inspectable read surface for persistent marketing
memory. Returns the same Pattern dicts the planner + content engine
consume; this is "what the system has learned, with the evidence."

Scoped via the shared query_memory service. Empty array when there's
no telemetry to learn from — the UI shows the honest "not enough yet"
empty state.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.memory import query_memory
from app.models import User

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
def get_memory(product_id: str | None = Query(None),
               audience: str | None = Query(None),
               channel: str | None = Query(None),
               content_type: str | None = Query(None),
               campaign_type: str | None = Query(None),
               lookback_days: int = Query(180, ge=1, le=730),
               user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    patterns = query_memory(
        db, user.org_id,
        product_id=product_id,
        audience=audience,
        channel=channel,
        content_type=content_type,
        campaign_type=campaign_type,
        lookback_days=lookback_days,
    )
    return {
        "patterns": patterns,
        "lookback_days": lookback_days,
        "filters": {
            "product_id": product_id, "audience": audience,
            "channel": channel, "content_type": content_type,
            "campaign_type": campaign_type,
        },
        # Cheap honest summary surfaces in the UI without any extra calls.
        "summary": {
            "total": len(patterns),
            "actionable": sum(1 for p in patterns
                              if p["confidence"] != "insufficient"),
            "watching": sum(1 for p in patterns
                            if p["confidence"] == "insufficient"),
        },
    }
