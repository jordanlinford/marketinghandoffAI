"""
Product inheritance resolver — the single source of truth for the merge
between OrgProfile and ProductProfile.

Agents read profile-level config ONLY through this resolver (via
ctx.profile). They never hit OrgProfile or ProductProfile rows directly.
That keeps inheritance honest and per-field traceable: the returned
ResolvedProfile carries provenance for each inheritable field, the same
way the Setup additive-merge surfaces per-field source tags.

Rules:
  * If product_id is None → return the org-level view unchanged. This is
    the backwards-compat path for every run that pre-dates the product
    layer (and the everyday "no product selected" flow).
  * If a ProductProfile exists for product_id, scoped to the org:
      - Inheritable overrides (brand_voice / banned_claims / rubric /
        conversion_goal / utm_*): the product value WINS when non-null;
        otherwise we fall back to the org's value. Provenance flips to
        "product_override" only when the override actually contributed.
      - Product-only fields (positioning, persona, value_props, etc.)
        live under resolved["product"]. They have no org equivalent and
        no inheritance.
  * If product_id is given but no matching row → we behave as if no
    product were selected (org view) AND record provenance accordingly.
    This is the right answer for a deleted product (artifacts that
    reference it revert to org-level).

The contract is "agents read a dict". We deliberately do NOT return an
ORM object — agents must not need a DB session.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import OrgProfile, ProductProfile
from app.tenancy import scoped


# The inheritable fields. The override column on ProductProfile is named
# "<field>_override" by convention; the org column is named "<field>".
# Keeping this list in one place means adding a new inheritable field is
# one tuple, one model change, one schema change.
_INHERITABLE: tuple[tuple[str, str, str], ...] = (
    # (resolved key, org column, product override column)
    ("brand_voice",      "brand_voice",      "brand_voice_override"),
    ("banned_claims",    "banned_claims",    "banned_claims_override"),
    ("conversion_goal",  "conversion_goal",  "conversion_goal_override"),
    ("content_rubric",   "content_rubric",   "rubric_override"),
)


def _org_layer(prof: OrgProfile | None) -> dict:
    """Stable org-level dict the agent has always seen. Returned even when
    no product is in play so the legacy ctx.org_profile contract is
    preserved."""
    if prof is None or not prof.confirmed:
        return {}
    return {
        "id": prof.id,
        "confirmed": True,
        "product_summary": prof.product_summary,
        "value_prop": prof.value_prop,
        "icp": prof.icp or {},
        "competitors": prof.competitors or [],
        "keywords": prof.keywords or [],
        "brand_voice": prof.brand_voice,
        "banned_claims": prof.banned_claims or [],
        "conversion_goal": prof.conversion_goal,
        "conversion_event": prof.conversion_event,
        "website_url": prof.website_url,
        "content_review_mode": prof.content_review_mode or "guardrail",
        "content_rubric": prof.content_rubric or [],
    }


def _product_layer(prod: ProductProfile | None) -> dict | None:
    """Product-only fields under resolved["product"]. None when there's
    no product or it's still a draft (drafts are not read by agents —
    same gating as OrgProfile.confirmed)."""
    if prod is None or prod.status != "confirmed":
        return None
    return {
        "id": prod.id,
        "name": prod.name,
        "slug": prod.slug,
        "status": prod.status,
        "website_url": prod.website_url,
        "positioning": prod.positioning,
        "target_persona": prod.target_persona or {},
        "value_props": prod.value_props or [],
        "proof_points": prod.proof_points or [],
        "differentiators": prod.differentiators or [],
        "key_features": prod.key_features or [],
        "use_cases": prod.use_cases or [],
        "product_competitors": prod.product_competitors or [],
    }


def _resolve_inheritable(org: dict, prod: ProductProfile | None
                         ) -> tuple[dict, dict]:
    """Apply the override rules. Returns (resolved_fields, provenance).
    For each inheritable, "product_override" wins when set and non-empty,
    else "org" — provenance reflects what actually drove the value."""
    resolved: dict = {}
    provenance: dict = {}
    for key, org_col, prod_col in _INHERITABLE:
        org_val = org.get(org_col)
        prod_val = getattr(prod, prod_col, None) if prod is not None else None
        # "set" means non-None for scalars, and non-None-AND-non-empty for
        # JSON list fields. An empty list override would functionally
        # erase the org's value, which is rarely what the user means —
        # but we honor an explicit empty list IF prod_val is [] (a
        # deliberate "no banned claims here"). NULL still means inherit.
        if prod_val is not None:
            resolved[key] = prod_val
            provenance[key] = "product_override"
        else:
            resolved[key] = org_val if org_val is not None else (
                "" if isinstance(_default_for(key), str) else [])
            provenance[key] = "org"
    return resolved, provenance


def _default_for(key: str):
    """Empty value type per field so a missing org returns a stable shape
    instead of None."""
    if key in ("brand_voice", "conversion_goal"):
        return ""
    return []


def _utm_layer(org: dict, prod: ProductProfile | None) -> dict:
    """Product UTM defaults override the per-content-type defaults the
    template registry knows about. campaign_prefix is the product slug
    (or None), which the content_engine inserts BEFORE the slugged topic
    so reports naturally segment by product."""
    src = getattr(prod, "utm_source_default", None) if prod is not None else None
    med = getattr(prod, "utm_medium_default", None) if prod is not None else None
    return {
        "utm_source_default": src,
        "utm_medium_default": med,
        # Only confirmed products contribute a prefix — a draft product
        # shouldn't be tagging real content yet.
        "utm_campaign_prefix": (prod.slug if prod is not None
                                and prod.status == "confirmed" else None),
    }


def resolve_product_profile(db: Session, org_id: str,
                            product_id: str | None) -> dict:
    """The ONE function agents read profile-level config through.

    Returns a ResolvedProfile dict shaped like:

      {
        # Org-level fields (always present when an OrgProfile is confirmed)
        "product_summary", "value_prop", "icp", "competitors",
        "keywords", "conversion_event", "website_url",
        "content_review_mode",
        # Inheritable (org or product_override)
        "brand_voice", "banned_claims", "conversion_goal", "content_rubric",
        # Product-only metadata (None when no product / draft product)
        "product": {... | None},
        # UTM defaults the content engine reads
        "utm_source_default", "utm_medium_default", "utm_campaign_prefix",
        # Per-field provenance: "org" | "product_override" | "product"
        "provenance": {...},
        # The unresolved org layer (for ctx.org_profile backwards-compat)
        "_org": {...},
      }

    Tolerant: a missing/draft product behaves as "no product".
    """
    org_row = db.execute(scoped(OrgProfile, org_id)).scalar_one_or_none()
    org = _org_layer(org_row)

    prod_row: ProductProfile | None = None
    if product_id:
        prod_row = db.execute(
            scoped(ProductProfile, org_id).where(ProductProfile.id == product_id)
        ).scalar_one_or_none()
        # If it exists but is still a draft, it's intentionally invisible
        # to agents — same gating as OrgProfile.confirmed.
        if prod_row is not None and prod_row.status != "confirmed":
            prod_row = None

    inherited, provenance = _resolve_inheritable(org, prod_row)
    product_layer = _product_layer(prod_row)
    utm = _utm_layer(org, prod_row)

    # Provenance for product-only fields: if a product layer exists,
    # they came from "product"; otherwise they're absent and we don't
    # claim provenance.
    if product_layer is not None:
        for key in product_layer.keys():
            if key in ("id", "status", "slug", "name"):
                continue
            provenance[f"product.{key}"] = "product"

    # Messaging notes (objection_handling, launch_messaging, ...) live on
    # the ProductProfile JSON column and are populated by document
    # extraction's promotion step. Agents (content_engine) read them
    # through the resolved profile so the chassis contract stays "ctx
    # only" — no DB reads from agent code.
    messaging_notes: dict = {}
    if prod_row is not None and prod_row.status == "confirmed":
        messaging_notes = dict(prod_row.messaging_notes or {})

    resolved: dict = {
        # Org-level fields the agent still reads directly
        "product_summary": org.get("product_summary", ""),
        "value_prop": org.get("value_prop", ""),
        "icp": org.get("icp", {}),
        "competitors": org.get("competitors", []),
        "keywords": org.get("keywords", []),
        "conversion_event": org.get("conversion_event", ""),
        "website_url": (product_layer or {}).get("website_url")
                       or org.get("website_url", ""),
        "content_review_mode": org.get("content_review_mode", "guardrail"),
        # Inheritable fields (already applied)
        **inherited,
        # Product layer
        "product": product_layer,
        "product_id": product_layer["id"] if product_layer else None,
        # Messaging notes — schemaless lists keyed by note type. Empty
        # dict when there's no product or none have been promoted yet.
        "messaging_notes": messaging_notes,
        # UTM defaults
        **utm,
        "provenance": provenance,
        "_org": org,
    }
    return resolved
