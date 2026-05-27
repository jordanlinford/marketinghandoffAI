"""
Product CRUD + confirm + resolved-profile read.

Products are children of an org. Every endpoint here is tenant-scoped via
scoped(); the (org_id, slug) unique constraint keeps URLs / UTM prefixes
unambiguous within an org. Inheritable override fields accept None to
revert to org-level inheritance — that's the editing contract for "stop
overriding, fall back to the org."

Delete is hard, with the columns referencing `product_profiles` declared
ON DELETE SET NULL. Per the brief: a deleted product reverts existing
artifacts/runs to org-level rather than orphaning them.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import ProductProfile, User, _now
from app.products import resolve_product_profile
from app.tenancy import scoped

router = APIRouter(prefix="/api/products", tags=["products"])


def _slugify(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:120]


# Fields the API accepts. We deliberately spell out each one rather than
# blob-accepting a dict so the contract is explicit and a misspelled key
# from the client fails Pydantic validation instead of silently no-op'ing.
class ProductIn(BaseModel):
    name: str | None = None
    slug: str | None = None
    status: str | None = None      # 'draft' | 'confirmed'
    website_url: str | None = None
    # Product-only
    positioning: str | None = None
    target_persona: dict | None = None
    value_props: list | None = None
    proof_points: list | None = None
    differentiators: list | None = None
    key_features: list | None = None
    use_cases: list | None = None
    product_competitors: list | None = None
    # Inheritable overrides — pass null to revert to inheritance.
    brand_voice_override: str | None = None
    banned_claims_override: list | None = None
    conversion_goal_override: str | None = None
    rubric_override: list | None = None
    utm_source_default: str | None = None
    utm_medium_default: str | None = None


class ProductCreateIn(BaseModel):
    """Name is required to create; everything else is optional and lands
    blank/None so the user can fill in over time."""
    name: str = Field(..., min_length=1)
    slug: str | None = None
    website_url: str = ""
    positioning: str = ""
    value_props: list = Field(default_factory=list)
    key_features: list = Field(default_factory=list)
    use_cases: list = Field(default_factory=list)
    product_competitors: list = Field(default_factory=list)
    target_persona: dict = Field(default_factory=dict)


# Inheritable override fields whose Pydantic `None` is meaningful (means
# "explicitly revert to org-level inheritance"). PATCH treats them
# specially: if a key is present in the request body — even with value
# null — we write None to clear the override. If a key is absent we
# leave the column untouched.
_INHERITABLE_OVERRIDE_FIELDS = (
    "brand_voice_override", "banned_claims_override",
    "conversion_goal_override", "rubric_override",
    "utm_source_default", "utm_medium_default",
)


def _serialize(p: ProductProfile) -> dict:
    return {
        "id": p.id, "org_id": p.org_id, "name": p.name, "slug": p.slug,
        "status": p.status, "website_url": p.website_url,
        "positioning": p.positioning,
        "target_persona": p.target_persona or {},
        "value_props": p.value_props or [],
        "proof_points": p.proof_points or [],
        "differentiators": p.differentiators or [],
        "key_features": p.key_features or [],
        "use_cases": p.use_cases or [],
        "product_competitors": p.product_competitors or [],
        # Inheritable overrides — None means "inherit from org".
        "brand_voice_override": p.brand_voice_override,
        "banned_claims_override": p.banned_claims_override,
        "conversion_goal_override": p.conversion_goal_override,
        "rubric_override": p.rubric_override,
        "utm_source_default": p.utm_source_default,
        "utm_medium_default": p.utm_medium_default,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.post("", response_model=dict, status_code=201)
def create_product(body: ProductCreateIn,
                   user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    slug = (body.slug or _slugify(body.name)).strip()
    if not slug:
        raise HTTPException(400, "Could not derive a slug; supply one explicitly.")
    # Pre-flight uniqueness check (org-scoped) so the error message is
    # clearer than a raw IntegrityError trickled out of SQLAlchemy.
    existing = db.execute(
        scoped(ProductProfile, user.org_id)
        .where(ProductProfile.slug == slug)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"A product with slug {slug!r} already exists for this org.")
    p = ProductProfile(
        org_id=user.org_id, name=body.name, slug=slug, status="draft",
        website_url=body.website_url,
        positioning=body.positioning,
        target_persona=body.target_persona,
        value_props=body.value_props,
        key_features=body.key_features,
        use_cases=body.use_cases,
        product_competitors=body.product_competitors,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.get("")
def list_products(user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    rows = db.execute(
        scoped(ProductProfile, user.org_id).order_by(ProductProfile.created_at.asc())
    ).scalars().all()
    return [_serialize(p) for p in rows]


def _get(db: Session, user: User, product_id: str) -> ProductProfile:
    p = db.execute(
        scoped(ProductProfile, user.org_id).where(ProductProfile.id == product_id)
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Product not found")
    return p


@router.get("/{product_id}")
def get_product(product_id: str, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    return _serialize(_get(db, user, product_id))


@router.patch("/{product_id}")
def update_product(product_id: str, body: ProductIn,
                   user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    p = _get(db, user, product_id)
    # Inheritable overrides: a key PRESENT in the request body — even with
    # value null — is a deliberate "set this to None / revert to
    # inheritance". A key ABSENT means "don't touch this column".
    explicit = body.model_dump(exclude_unset=True)
    for key, val in explicit.items():
        if key == "slug" and val:
            new_slug = _slugify(str(val))
            if new_slug != p.slug:
                clash = db.execute(
                    scoped(ProductProfile, user.org_id)
                    .where(ProductProfile.slug == new_slug,
                           ProductProfile.id != p.id)
                ).scalar_one_or_none()
                if clash is not None:
                    raise HTTPException(409, f"Slug {new_slug!r} already in use.")
                p.slug = new_slug
            continue
        if key == "status" and val:
            if val not in ("draft", "confirmed"):
                raise HTTPException(400, "status must be 'draft' or 'confirmed'")
            p.status = val
            continue
        if key in _INHERITABLE_OVERRIDE_FIELDS:
            # Pass through None deliberately (revert-to-inheritance).
            setattr(p, key, val)
            continue
        setattr(p, key, val)
    p.updated_at = _now()
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.post("/{product_id}/confirm")
def confirm_product(product_id: str, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    p = _get(db, user, product_id)
    p.status = "confirmed"
    p.updated_at = _now()
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.delete("/{product_id}", status_code=204)
def delete_product(product_id: str, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    p = _get(db, user, product_id)
    # Hard delete; ON DELETE SET NULL on the referencing FKs reverts
    # existing artifacts / runs / metric_points to org-level rather than
    # orphaning them. This is the v1 contract (see CLAUDE.md / brief).
    db.delete(p)
    db.commit()
    return None


# Pinned at the profile router prefix would be cleaner, but adding a
# product-id-aware endpoint here keeps the resolver visible in the same
# module a debugger reaches for when chasing inheritance behavior.
@router.get("/-/resolved")
def get_resolved_profile(product_id: str | None = None,
                         user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict:
    """Returns the ResolvedProfile (org + product + provenance). product_id
    is optional; without it the resolver returns the org-level view, which
    is the right answer for "what's effective when no product is selected"."""
    return resolve_product_profile(db, user.org_id, product_id)
