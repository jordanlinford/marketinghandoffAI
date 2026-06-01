"""
Seed org #1 = Onit. Run once:  python -m scripts.seed
Idempotent: safe to re-run.

NOTE: raw selects here are a legitimate exception to scoped() — this script
runs as system-level setup, not as a tenant request.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.models import (AgentRegistration, Campaign, Guardrail, Org,
                        ProductProfile, User)

ONIT_ICP = {
    "category": "legal operations software",
    "industries": ["Legal Services", "Financial Services", "Insurance",
                   "Manufacturing", "Technology", "Healthcare"],
    "min_employees": 500,
    "keyword_seeds": ["legal operations", "matter management", "contract lifecycle",
                      "legal spend management", "enterprise legal management"],
    "channels": ["LinkedIn", "Google Search", "Email", "Webinars"],
}


def main() -> None:
    create_all()
    db = SessionLocal()
    try:
        org = db.execute(select(Org).where(Org.domain == "onit.com")).scalar_one_or_none()
        if org is None:
            org = Org(name="Onit", domain="onit.com")
            db.add(org)
            db.commit()
            db.refresh(org)
            print(f"Created org Onit ({org.id})")

        user = db.execute(select(User).where(User.email == "jordan@onit.com")).scalar_one_or_none()
        if user is None:
            db.add(User(org_id=org.id, email="jordan@onit.com", name="Jordan", role="admin"))
            db.commit()
            print("Created admin user jordan@onit.com")

        reg = db.execute(
            select(AgentRegistration).where(
                AgentRegistration.org_id == org.id, AgentRegistration.key == "market_intel")
        ).scalar_one_or_none()
        if reg is None:
            db.add(AgentRegistration(
                org_id=org.id, key="market_intel", display_name="Market intelligence",
                kind="builtin", enabled=True, schedule_cron="0 7 * * 1",
                config={"icp": ONIT_ICP, "company_limit": 120, "keyword_limit": 60,
                        "sam_min_fit": 0.6, "som_capture_rate": 0.08},
            ))
            db.commit()
            print("Registered market_intel agent for Onit (weekly Monday cron)")

        content_reg = db.execute(
            select(AgentRegistration).where(
                AgentRegistration.org_id == org.id, AgentRegistration.key == "content_engine")
        ).scalar_one_or_none()
        if content_reg is None:
            db.add(AgentRegistration(
                org_id=org.id, key="content_engine", display_name="Content engine",
                kind="builtin", enabled=True, schedule_cron=None,
                config={},   # no seed icp; grounds in the confirmed OrgProfile
            ))
            db.commit()
            print("Registered content_engine agent for Onit")

        report_reg = db.execute(
            select(AgentRegistration).where(
                AgentRegistration.org_id == org.id,
                AgentRegistration.key == "report_composer")
        ).scalar_one_or_none()
        if report_reg is None:
            db.add(AgentRegistration(
                org_id=org.id, key="report_composer", display_name="Report composer",
                kind="builtin", enabled=True, schedule_cron=None,
                config={},  # storytelling layer; reads from substrate + memory
            ))
            db.commit()
            print("Registered report_composer agent for Onit")

        deriv_reg = db.execute(
            select(AgentRegistration).where(
                AgentRegistration.org_id == org.id,
                AgentRegistration.key == "derivative_composer")
        ).scalar_one_or_none()
        if deriv_reg is None:
            db.add(AgentRegistration(
                org_id=org.id, key="derivative_composer",
                display_name="Derivative composer",
                kind="builtin", enabled=True, schedule_cron=None,
                config={},  # derives from anchor artifacts; §7 containment-gated
            ))
            db.commit()
            print("Registered derivative_composer agent for Onit")

        # Default guardrail rules. "content" stays here as the SCOPE shell;
        # OrgProfile.banned_claims is merged in at worker time so the profile
        # remains the single source of truth for which phrases trip the rule.
        # Seed one example product under Onit so the dev DB demonstrates
        # the product layer end-to-end (overrides → resolver → agent UTM
        # prefix) without anyone hand-creating it. Idempotent on (org_id,
        # slug). Status=confirmed so agents will read it.
        product = db.execute(
            select(ProductProfile).where(
                ProductProfile.org_id == org.id,
                ProductProfile.slug == "simplelegal-clm")
        ).scalar_one_or_none()
        if product is None:
            db.add(ProductProfile(
                org_id=org.id, name="SimpleLegal CLM", slug="simplelegal-clm",
                status="confirmed", website_url="https://onit.com/products/clm",
                positioning=(
                    "Contract lifecycle management built for in-house teams who "
                    "live in matters, not in legal-tech jargon."),
                target_persona={
                    "role": "General Counsel / Head of Legal Ops",
                    "seniority": "Director+",
                    "pains": ["Spreadsheet sprawl", "Lost contract context",
                              "Slow approvals"],
                },
                value_props=[
                    "One source of truth for contracts and approvals.",
                    "Cuts contract turnaround time in half within 90 days.",
                    "No-code workflows your legal team can own.",
                ],
                key_features=["Clause library", "Approval routing",
                              "Renewal tracking", "Integrations with DMS + CLM tools"],
                use_cases=["NDAs at scale", "Vendor onboarding",
                           "Renewals + obligations management"],
                product_competitors=[
                    {"name": "Ironclad", "url": "https://ironclad.com"},
                    {"name": "Agiloft", "url": "https://agiloft.com"},
                ],
                # No overrides: brand_voice/banned_claims/rubric/etc. inherit
                # from OrgProfile by default. UTM defaults left null too.
            ))
            db.commit()
            print("Seeded example product 'SimpleLegal CLM' under Onit")

        # Seed one demonstrative field_history entry on SimpleLegal CLM so
        # the dev DB shows the promotion shape (and rollback affordance)
        # without requiring an actual document upload. Idempotent: only
        # add if no history entries exist yet on this product.
        product = db.execute(
            select(ProductProfile).where(
                ProductProfile.org_id == org.id,
                ProductProfile.slug == "simplelegal-clm")
        ).scalar_one()
        if not (product.field_history or []):
            from datetime import datetime, timezone
            product.field_history = [{
                "field": "positioning",
                "value": product.positioning,
                "accepted_from_insight_id": "__seed__",
                "accepted_at": datetime.now(timezone.utc).isoformat(),
                "status": "active",
                "note": ("Seeded entry — represents the positioning that would "
                         "have been written by accepting a doc-extracted insight. "
                         "Demonstrates field_history shape without requiring an upload."),
            }]
            db.commit()
            print("Seeded a demonstrative field_history entry on SimpleLegal CLM")

        # Seed one example campaign under SimpleLegal CLM so the dev DB
        # demonstrates the campaign shape (a small approved plan + the
        # generated_asset_ids container) without anyone running through
        # Step 1 → Propose → Approve manually. Idempotent on (org_id, name).
        existing_campaign = db.execute(
            select(Campaign).where(Campaign.org_id == org.id,
                                   Campaign.name == "SimpleLegal CLM Launch")
        ).scalar_one_or_none()
        if existing_campaign is None:
            plan = {
                "derivative_assets": [
                    {"id": "1-linkedin-launch-announce",
                     "content_type": "social_post", "channel": "linkedin",
                     "topic": "Modern matter management for in-house teams",
                     "angle": "Launch announcement — name what's new + why now.",
                     "audience": "General Counsel, Head of Legal Ops",
                     "rationale": "Lead the motion with in-feed credibility.",
                     "cadence_hint": "Day 0"},
                    {"id": "2-email-cta-demo",
                     "content_type": "email", "channel": "email",
                     "topic": "See SimpleLegal CLM in 15 minutes",
                     "angle": "Pain-led outbound: cost of doing nothing.",
                     "audience": "GC + Legal Ops directors",
                     "rationale": "Highest-control channel for the demo CTA.",
                     "cadence_hint": "Day 0 + Day 3 follow-up"},
                    {"id": "3-ad-retarget",
                     "content_type": "ad", "channel": "linkedin",
                     "topic": "Cut contract turnaround by half — within 90 days",
                     "angle": "Single-claim retargeting ad pointing at the demo.",
                     "audience": "Visitors who engaged with the launch posts",
                     "rationale": "Capture engaged visitors after the launch.",
                     "cadence_hint": "Weeks 1–3, continuous"},
                ],
                "channel_mix": [
                    {"channel": "email", "weight": "primary",
                     "rationale": "Highest-control for the demo CTA."},
                    {"channel": "linkedin", "weight": "primary",
                     "rationale": "Reach + retargeting; B2B credibility."},
                    {"channel": "organic", "weight": "support",
                     "rationale": "SEO anchor compounds over time."},
                ],
                "cadence_guidance": (
                    "Lead with the LinkedIn launch post on Day 0, paired with "
                    "the announcement email. Retarget engaged visitors with "
                    "the LinkedIn ad through week 3. Re-evaluate after week "
                    "2 against the Insights funnel."),
                "source": "seed",
            }
            db.add(Campaign(
                org_id=org.id, product_id=product.id,
                name="SimpleLegal CLM Launch",
                description=("Coordinated launch motion for the SimpleLegal "
                             "CLM product — seeded example so the dev DB "
                             "demonstrates the orchestration shape."),
                campaign_type="launch",
                objective="Drive 25 qualified demo requests in the first 30 days.",
                status="planned",
                owner="jordan@onit.com",
                primary_cta="book a demo",
                target_personas=["General Counsel", "Head of Legal Ops"],
                target_industries=["Legal Services", "Financial Services"],
                selected_channels=["linkedin", "email", "organic"],
                channel_recommendations={
                    "recommended_channels": ["linkedin", "email", "organic"],
                    "rationale": "Best-practice B2B launch mix; no performance data consulted in v1.",
                },
                plan=plan,
                # Empty list — populated as the worker generates assets.
                # The detail endpoint refreshes from current artifacts.
                generated_asset_ids=[],
                # Slug convention matches the API: campaign-<product>__<name>.
                # The earlier order (<product>__campaign-<name>) was a one-off
                # in seed only and made the dev DB inconsistent with anything
                # created through /api/campaigns.
                utm_campaign="campaign-simplelegal-clm__simplelegal-clm-launch",
            ))
            db.commit()
            print("Seeded example campaign 'SimpleLegal CLM Launch' (status=planned)")

        for scope, rules in [("spend", {"max_change_usd": 250}),
                             ("publish", {"require_human_review": True}),
                             ("content", {"banned_claims": []})]:
            exists = db.execute(
                select(Guardrail).where(Guardrail.org_id == org.id, Guardrail.scope == scope)
            ).scalar_one_or_none()
            if exists is None:
                db.add(Guardrail(org_id=org.id, scope=scope, rules=rules))
        db.commit()
        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
