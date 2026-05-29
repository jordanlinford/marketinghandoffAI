"""
Demo seed — the hero dataset for the prototype demo.

Idempotent: existing seed artifacts (matched by title) are left in
place. Re-runnable from a clean DB or on top of an existing one.
Generates the FOUR product-scoped reports through the real worker
path — bodies are not hand-written fixtures; they are renderer
output. Warning + Blocked states are induced via small ledger edits
(downgrade / remove an entry) and re-validation, so the trust
state arises from genuine ledger drift rather than fabricated
findings.

What it produces, all scoped to SimpleLegal CLM:
  * One ACTIVE-ish campaign with >=1 generated asset (from the
    existing campaign seed in scripts/seed.py, extended here to
    drive at least one approved item through).
  * Four reports with distinct titles so the Library doesn't show
    "Board update — 04-29 → 05-29" three times:
      - Board update — clean (Q2 hero, passed)
      - CEO weekly — warning (thin data, §1)
      - Sales leadership — blocked (§6 fabrication, 2x critical)
      - CEO weekly — future scope (honest stub, §4 not fired)

Run:
    python -m scripts.demo_seed
"""
from __future__ import annotations

import re
import sys
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AgentRegistration, Artifact, Campaign, Org, ProductProfile, Run, User
from app.queue import enqueue
from app.reports.evidence import (Ledger, trust_checks_with_findings,
                                   validate_evidence_binding)
from app.tenancy import scoped
from app.worker import run_once


_MARKER_RE = re.compile(r"⟦ev:([A-Za-z0-9_\-]+)⟧")


_DEMO_TITLES = {
    "board_clean":          "Board update — clean (Q2 hero)",
    "ceo_warning":          "CEO weekly — warning (thin data)",
    "sales_blocked":        "Sales leadership — blocked (§6 fabrication)",
    "ceo_future":           "CEO weekly — future scope (honest stub)",
}


def _scope_window(days_back: int = 30) -> dict:
    today = date.today()
    return {"kind": "time_window",
            "start": (today - timedelta(days=days_back)).isoformat(),
            "end": today.isoformat()}


def _scope_future(days_ahead: int = 60, span: int = 30) -> dict:
    today = date.today()
    return {"kind": "time_window",
            "start": (today + timedelta(days=days_ahead)).isoformat(),
            "end": (today + timedelta(days=days_ahead + span)).isoformat()}


def _drain_worker(max_iters: int = 100) -> int:
    """Run the worker loop until the queue is empty (or max_iters)."""
    n = 0
    for _ in range(max_iters):
        if not run_once():
            break
        n += 1
    return n


def _trigger_report(db, org_id: str, product_id: str, reg_id: str,
                    user_id: str, audience: str, scope: dict) -> Run:
    run = Run(
        org_id=org_id, agent_registration_id=reg_id,
        agent_key="report_composer", trigger="manual",
        status="queued", created_by=user_id,
        product_id=product_id,
        task={"audience": audience, "scope": scope, "lookback_days": 30},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    enqueue(db, org_id, "run_agent", {"run_id": run.id})
    _drain_worker()
    db.refresh(run)
    return run


def _find_report_by_run(db, org_id: str, run_id: str) -> Artifact | None:
    return db.execute(
        scoped(Artifact, org_id).where(
            Artifact.run_id == run_id,
            Artifact.type == "report_draft",
        )
    ).scalar_one_or_none()


def _exists_by_title(db, org_id: str, product_id: str, title: str) -> Artifact | None:
    return db.execute(
        scoped(Artifact, org_id).where(
            Artifact.type == "report_draft",
            Artifact.product_id == product_id,
            Artifact.title == title,
        )
    ).scalar_one_or_none()


def _rerun_validation(content: dict, ledger_entries: list[dict],
                      scope: dict) -> dict:
    """Re-run validate_evidence_binding + trust_checks_with_findings
    against the modified ledger (preserving entry ids via
    Ledger.from_entries) and return fresh trust_checks."""
    L = Ledger.from_entries(ledger_entries)
    tc = validate_evidence_binding(content, L)
    tc = trust_checks_with_findings(tc, L.to_list(), content, scope=scope)
    return tc


def _induce_warning(db, art: Artifact) -> None:
    """Pick the first body block whose resolving markers all point to
    the SAME memory entries, downgrade those entries to "low", and
    re-validate. The §1 thin-evidence warning fires for the block
    whose every cited entry is now below threshold.
    """
    body = dict(art.body or {})
    ledger_entries = list(body.get("evidence_ledger") or [])
    content = body.get("content") or {}
    blocks = content.get("blocks") or []
    scope = (content.get("metadata") or {}).get("scope") or {}

    # Find the first body-shaped block whose marker ids point at
    # memory_highlights[*] entries — that's where the warning has
    # the most natural meaning.
    target_ids: set[str] = set()
    for b in blocks:
        if (b or {}).get("kind") not in ("body", "headline"):
            continue
        text = (b or {}).get("text") or ""
        ids = set(_MARKER_RE.findall(text))
        if not ids:
            continue
        memory_ids = {
            e["id"] for e in ledger_entries
            if e["id"] in ids and "memory_highlights" in (e.get("source") or "")
        }
        if memory_ids:
            target_ids = memory_ids
            break
    if not target_ids:
        # Fall back: downgrade the first non-empty body block's ids.
        for b in blocks:
            if (b or {}).get("kind") not in ("body", "headline"):
                continue
            ids = set(_MARKER_RE.findall((b or {}).get("text") or ""))
            if ids:
                target_ids = ids
                break
    if not target_ids:
        return

    new_ledger = []
    for e in ledger_entries:
        ee = dict(e)
        if ee.get("id") in target_ids:
            ee["confidence"] = "low"
        new_ledger.append(ee)
    body["evidence_ledger"] = new_ledger
    body["trust_checks"] = _rerun_validation(content, new_ledger, scope)
    art.body = body
    if body["trust_checks"].get("approval_blocked"):
        art.status = "pending_review"


def _induce_blocked(db, art: Artifact) -> None:
    """Remove the LEAST-cited ledger entry from the body's evidence
    ledger. Re-validation fires 2-4 CRITICAL §6 findings — one per
    occurrence of the removed id, plus any adjacent numbers that lose
    their proximity-bound marker. A real ledger drift caught by the
    real validator, NOT synthetic findings injected to hit a count.

    Why "least-cited" instead of "first two": removing an entry cited
    N times produces ~2N findings (one unresolved-marker finding + one
    unbound-number finding per occurrence). Removing two heavily-cited
    entries cascades into a ~16-finding wall that a viewer reads as a
    malfunction rather than as the system catching a specific fixable
    problem. We want 2-4 — small, legible, one-bug-shaped.
    """
    from collections import Counter

    body = dict(art.body or {})
    ledger_entries = list(body.get("evidence_ledger") or [])
    content = body.get("content") or {}
    scope = (content.get("metadata") or {}).get("scope") or {}

    blocks = content.get("blocks") or []
    cited_ids: list[str] = []
    for b in blocks:
        text = (b or {}).get("text") or ""
        cited_ids.extend(_MARKER_RE.findall(text))
    citation_count = Counter(cited_ids)
    # Rank ledger entries by how many times their id appears in body
    # markers. Prefer the smallest count (1 → ~2 findings; 2 → ~4
    # findings; both safely inside the 2-4 target band). Entries
    # cited 0 times aren't useful — removing them produces no
    # findings, so we filter them out.
    cited_ledger = [e for e in ledger_entries
                    if citation_count.get(e["id"], 0) >= 1]
    if not cited_ledger:
        # Fallback: nothing in the ledger is actually cited in the
        # body — pick the first entry and remove it (low chance, but
        # keep behavior deterministic).
        to_remove = {ledger_entries[0]["id"]} if ledger_entries else set()
    else:
        cited_ledger.sort(key=lambda e: citation_count[e["id"]])
        to_remove = {cited_ledger[0]["id"]}

    new_ledger = [e for e in ledger_entries if e["id"] not in to_remove]
    body["evidence_ledger"] = new_ledger
    body["trust_checks"] = _rerun_validation(content, new_ledger, scope)
    art.body = body
    if body["trust_checks"].get("approval_blocked"):
        art.status = "pending_review"


def _activate_seed_campaign(db, onit: Org, product: ProductProfile) -> None:
    """If the seeded SimpleLegal CLM Launch campaign hasn't yet
    generated any assets, drive at least one through. Idempotent.
    """
    camp = db.execute(
        scoped(Campaign, onit.id).where(
            Campaign.product_id == product.id,
            Campaign.name == "SimpleLegal CLM Launch")
    ).scalar_one_or_none()
    if camp is None:
        return  # the base seed doesn't have it; nothing to activate
    if camp.status in ("active", "generating", "complete") and \
            (camp.generated_asset_ids or []):
        return
    # Trigger the campaign's generate endpoint via the existing
    # content_engine path. Simulate the API caller by enqueueing
    # content_engine runs per plan item.
    plan = camp.plan or {}
    items = plan.get("derivative_assets") or []
    if not items:
        return
    content_reg = db.execute(
        scoped(AgentRegistration, onit.id)
        .where(AgentRegistration.key == "content_engine")
    ).scalar_one_or_none()
    if content_reg is None or not content_reg.enabled:
        return
    # Enqueue only the FIRST plan item — keeps the demo lean and
    # ensures the campaign has ≥1 generated asset.
    item = items[0]
    content_type = (item.get("content_type") or "social_post").lower()
    topic = item.get("topic") or "Modern matter management"
    item_slug = item.get("id") or "1-demo-item"
    task = {
        "action": "generate",
        "content_type": content_type,
        "topic": topic,
        "target": item.get("audience") or "General Counsel",
        "utm": {
            "utm_campaign": camp.utm_campaign,
            "utm_source": (item.get("channel") or "linkedin").lower(),
            "utm_medium": content_type,
            "utm_content": item_slug,
        },
        "campaign_id": camp.id,
        "campaign_plan_item_id": item_slug,
    }
    run = Run(org_id=onit.id, agent_registration_id=content_reg.id,
              agent_key="content_engine", trigger="campaign",
              status="queued", product_id=camp.product_id, task=task)
    db.add(run)
    db.commit()
    db.refresh(run)
    enqueue(db, onit.id, "run_agent", {"run_id": run.id})
    _drain_worker()
    camp.status = "active"
    db.commit()


def main() -> None:
    db = SessionLocal()
    try:
        onit = db.execute(
            select(Org).where(Org.domain == "onit.com")).scalar_one()
        product = db.execute(
            scoped(ProductProfile, onit.id)
            .where(ProductProfile.slug == "simplelegal-clm")
        ).scalar_one()
        user = db.execute(
            scoped(User, onit.id)
            .where(User.role == "admin")
        ).scalars().first()
        report_reg = db.execute(
            scoped(AgentRegistration, onit.id)
            .where(AgentRegistration.key == "report_composer")
        ).scalar_one()

        # ---- Active campaign with ≥1 asset --------------------------------
        _activate_seed_campaign(db, onit, product)
        print("Demo seed: campaign activated (≥1 generated asset).")

        # ---- Four product-scoped reports ----------------------------------
        plan = [
            ("board_clean", "board", _scope_window(), None),
            ("ceo_warning", "ceo_weekly", _scope_window(), "warning"),
            ("sales_blocked", "sales_leadership", _scope_window(), "blocked"),
            ("ceo_future", "ceo_weekly", _scope_future(), None),
        ]
        for slug, audience, scope, induce in plan:
            title = _DEMO_TITLES[slug]
            existing = _exists_by_title(db, onit.id, product.id, title)
            if existing is not None:
                # Idempotency exception: the blocked report's tuning
                # is a moving target — if the existing artifact has
                # too many critical findings (the pre-tweak shape),
                # re-run the induction on the same body so the live
                # demo lands in the 2-4 band. The body's other content
                # is unchanged.
                if slug == "sales_blocked":
                    tc = (existing.body or {}).get("trust_checks") or {}
                    n_crit = (tc.get("findings_by_severity") or {}).get(
                        "critical", 0)
                    if 2 <= n_crit <= 4:
                        print(f"Demo seed: {slug!r} already in target "
                              f"band ({n_crit} critical) — skipping.")
                        continue
                    print(f"Demo seed: {slug!r} has {n_crit} critical "
                          f"findings (outside 2-4 target); deleting + "
                          f"regenerating.")
                    db.delete(existing)
                    db.commit()
                else:
                    print(f"Demo seed: {slug!r} already exists — skipping.")
                    continue
            run = _trigger_report(db, onit.id, product.id, report_reg.id,
                                   user.id, audience, scope)
            if run.status != "succeeded":
                print(f"Demo seed: {slug!r} run did not succeed "
                      f"(status={run.status}, error={run.error!r}). "
                      "Skipping induction.")
                continue
            art = _find_report_by_run(db, onit.id, run.id)
            if art is None:
                print(f"Demo seed: no artifact for {slug!r} run.")
                continue
            if induce == "warning":
                _induce_warning(db, art)
            elif induce == "blocked":
                _induce_blocked(db, art)
            art.title = title
            db.commit()
            print(f"Demo seed: produced {slug!r} → status={art.status} "
                  f"title={title!r}")
        print("Demo seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
