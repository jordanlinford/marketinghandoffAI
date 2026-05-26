"""
Artifact tags — the small PATCH surface for editing the UTM tags on a
content_draft after creation.

The agent stamps sensible defaults at generation time so the join key is
always present; this endpoint exists so a human can adjust them before the
draft is carried to its channel. The path is intentionally narrow:

  PATCH /api/artifacts/{artifact_id}/tags

Tenant-scoped via scoped(). Only content_draft artifacts are editable —
the join key isn't meaningful on a market brief. Recomputes the
body.tagged_url so the convenience link always matches the saved tags.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.utm import build_tagged_url, utm_field_keys
from app.auth import current_user
from app.db import get_db
from app.models import Artifact, User
from app.tenancy import scoped

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class ArtifactTagsIn(BaseModel):
    """All five fields are optional — a PATCH only updates what's supplied.
    Empty string clears the field (write a blank UTM); omit to leave it."""
    utm_campaign: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_content: str | None = None
    destination_url: str | None = None


@router.patch("/{artifact_id}/tags")
def update_tags(artifact_id: str, body: ArtifactTagsIn,
                user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    art = db.execute(
        scoped(Artifact, user.org_id).where(Artifact.id == artifact_id)
    ).scalar_one_or_none()
    if art is None:
        raise HTTPException(404, "Artifact not found")
    if art.type != "content_draft":
        raise HTTPException(400, "UTM tags are only editable on content_draft "
                                 "artifacts.")
    fields = body.model_dump(exclude_unset=True)
    # Only the four UTM columns + destination_url are editable here. We use
    # exclude_unset above so an absent field keeps its existing value.
    for key in utm_field_keys():
        if key in fields:
            setattr(art, key, (fields[key] or "").strip() or None)
    if "destination_url" in fields:
        art.destination_url = (fields["destination_url"] or "").strip() or None
    # Keep body.tagged_url in sync so the UI's copy-paste link reflects the
    # latest saved tags without recomputing client-side.
    utms = {k: getattr(art, k) or "" for k in utm_field_keys()}
    body_dict = dict(art.body or {})
    body_dict["tagged_url"] = build_tagged_url(art.destination_url, utms)
    art.body = body_dict
    db.commit()
    db.refresh(art)
    return {
        "id": art.id,
        "utm_campaign": art.utm_campaign, "utm_source": art.utm_source,
        "utm_medium": art.utm_medium, "utm_content": art.utm_content,
        "destination_url": art.destination_url,
        "tagged_url": body_dict["tagged_url"],
    }
