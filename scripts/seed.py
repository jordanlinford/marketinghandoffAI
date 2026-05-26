"""
Seed org #1 = Onit. Run once:  python -m scripts.seed
Idempotent: safe to re-run.

NOTE: raw selects here are a legitimate exception to scoped() — this script
runs as system-level setup, not as a tenant request.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.models import AgentRegistration, Guardrail, Org, User

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

        # Default guardrail rules. "content" stays here as the SCOPE shell;
        # OrgProfile.banned_claims is merged in at worker time so the profile
        # remains the single source of truth for which phrases trip the rule.
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
