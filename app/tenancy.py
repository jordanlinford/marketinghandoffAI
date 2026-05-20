"""
App-layer tenant isolation: the primary guard, works on any database.

Every query against a tenant table must be filtered by org_id. `scoped()` is the
one helper to use everywhere — it refuses to build a query without an org_id, so
a forgotten filter fails loudly instead of leaking another org's data. Postgres
RLS (sql/schema.sql) is defense-in-depth on top of this.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.sql import Select


def scoped(model, org_id: str) -> Select:
    if not org_id:
        raise ValueError("Refusing to query a tenant table without an org_id")
    return select(model).where(model.org_id == org_id)


def assert_same_org(obj, org_id: str) -> None:
    if obj is None or getattr(obj, "org_id", None) != org_id:
        raise PermissionError("Object does not belong to the current org")
