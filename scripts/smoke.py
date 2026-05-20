"""
End-to-end smoke test of the chassis. Proves:
  1. trigger -> queue -> worker -> run -> cited artifact (the whole spine)
  2. tenant isolation: a second org cannot see org #1's runs

Run:  python -m scripts.smoke

NOTE: raw selects in this script are a legitimate exception to scoped() —
system-level setup, not a tenant request.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.models import AgentRegistration, Org, Run
from app.queue import enqueue
from app.tenancy import scoped
from app.worker import run_once
from scripts.seed import main as seed_main


def main() -> None:
    create_all()
    seed_main()
    db = SessionLocal()
    try:
        onit = db.execute(select(Org).where(Org.domain == "onit.com")).scalar_one()
        reg = db.execute(
            scoped(AgentRegistration, onit.id).where(AgentRegistration.key == "market_intel")
        ).scalar_one()

        run = Run(org_id=onit.id, agent_registration_id=reg.id, agent_key="market_intel",
                  trigger="manual", status="queued")
        db.add(run)
        db.commit()
        db.refresh(run)
        enqueue(db, onit.id, "run_agent", {"run_id": run.id})

        assert run_once() is True, "worker did not pick up the job"
        db.refresh(run)
        print(f"\nRun status: {run.status}  cost=${run.cost_usd:.4f}")
        assert run.status == "succeeded", f"run failed: {run.error}"

        from app.models import Artifact
        art = db.execute(scoped(Artifact, onit.id).where(Artifact.run_id == run.id)).scalar_one()
        print(f"Artifact: {art.title}\nCitations: {[c['source'] for c in art.citations]}")
        print("\n--- BRIEF (first 900 chars) ---")
        print(art.body["narrative"][:900])

        # Tenant isolation check
        other = Org(name="Acme", domain="acme.com")
        db.add(other)
        db.commit()
        leaked = db.execute(scoped(Run, other.id)).scalars().all()
        assert leaked == [], "TENANT LEAK: another org could see Onit's runs!"
        print("\n[OK] Tenant isolation holds — Acme sees 0 of Onit's runs.")
        print("[OK] Smoke test passed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
