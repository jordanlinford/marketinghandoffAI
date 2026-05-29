"""
One-time backfill that promotes legacy reports to Artifact.type =
"report_draft".

Why this exists: until this build, the report_composer created
Artifacts with type="content_draft" and only carried the audience in
body.content.content_type. The Library filter + badge layer can't reach
into body JSON cheaply, so reports were invisible to the kind filter.

The fix: a top-level kind, the SAME field every other kind already uses
(Artifact.type). New reports are written with type="report_draft" at
creation in /api/reports/generate. Existing reports — including the
confabulated July one — get the same treatment via this idempotent
backfill at API startup.

Reads only existing tables. No new column added. Idempotent — running
it twice is a no-op. Cross-org safe because every UPDATE is scoped via
the source query.
"""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Artifact


_REPORT_TYPE = "report_draft"


def backfill_report_artifact_type(db: Session) -> int:
    """Find every Artifact whose body.content.content_type starts with
    "report_" but whose top-level type isn't "report_draft", and set
    type="report_draft". Returns the number of rows updated.

    System-level migration: runs outside any tenant context, so we
    deliberately do NOT use scoped() here (same rationale the seed
    script uses for its raw selects). The query touches every org.
    """
    # We have to filter in Python because body is JSON. The candidate
    # set is small (just content_draft + report_draft rows), so this
    # is cheap. We re-write only what's actually mismatched.
    candidates = db.execute(
        select(Artifact).where(
            Artifact.type.in_(("content_draft", _REPORT_TYPE))
        )
    ).scalars().all()
    to_update: list[str] = []
    for art in candidates:
        body = art.body or {}
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, dict):
            continue
        ct = (content.get("content_type") or "")
        if isinstance(ct, str) and ct.startswith("report_") \
                and art.type != _REPORT_TYPE:
            to_update.append(art.id)
    if not to_update:
        return 0
    db.execute(
        update(Artifact)
        .where(Artifact.id.in_(to_update))
        .values(type=_REPORT_TYPE)
    )
    db.commit()
    return len(to_update)
