"""
The worker. Polls the job queue, runs one agent per job inside a tenant-scoped
AgentContext, and persists everything (artifacts, gated proposals, cost, logs,
audit trail). This is where the five usability fundamentals live: observability,
idempotency, guardrails, cost capture, and graceful failure.

Run as a separate process:  python -m app.worker
"""
from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app import guardrails
from app.agents.registry import get_agent
from app.config import get_settings
from app.data_sources.csv import CsvMarketDataSource
from app.data_sources.stub import StubMarketDataSource
from app.db import SessionLocal
from app.models import (AgentRegistration, Artifact, AuditLog, Guardrail, Org,
                        OrgProfile, ProductDocument, ProductProfile, Proposal,
                        Run, Upload, _now)
from app.products import resolve_product_profile
from app.documents.extract import extract_candidates
from app.documents.normalize import NormalizationError, normalize
from app.documents.storage import read_document
from app.models import ExtractedInsight
from app.queue import complete, lease_next
from app.schemas import AgentContext
from app.tenancy import assert_same_org, scoped


def _resolve_market_data(db: Session, org_id: str, upload_id: str | None):
    """Pick a data source for this run.
      * upload_id set → CsvMarketDataSource(uploaded rows) — the real-accounts path.
      * upload_id None → StubMarketDataSource — unchanged from P0.
    Later: inspect connections and return ZoomInfoDataSource / ApolloDataSource.
    Same interface either way; the agent does not change."""
    if upload_id:
        up = db.execute(
            scoped(Upload, org_id).where(Upload.id == upload_id)
        ).scalar_one_or_none()
        if up is None:
            raise RuntimeError(f"Upload {upload_id} not found for org {org_id}")
        return CsvMarketDataSource(up.rows)
    return StubMarketDataSource()


def _serialize_artifact(art: Artifact) -> dict:
    """Stable view the agent sees in ctx.prior_artifacts. Mirrors the columns
    the agent might read (notably parent_id + utm_* for the regenerate path)
    without exposing the ORM row."""
    return {
        "id": art.id, "type": art.type, "title": art.title,
        "body": art.body or {}, "citations": art.citations or [],
        "status": art.status,
        "parent_id": art.parent_id,
        "grade": art.grade,
        "utm_campaign": art.utm_campaign, "utm_source": art.utm_source,
        "utm_medium": art.utm_medium, "utm_content": art.utm_content,
        "destination_url": art.destination_url,
        "created_at": art.created_at.isoformat() if art.created_at else None,
    }


def _latest_market_brief(db: Session, org_id: str) -> dict | None:
    """Latest succeeded market_brief artifact for this org (via scoped()), so
    the content_engine can ground its suggestions in real market data. Returns
    None when none exists. Read by agents through ctx.prior_artifacts — they
    never query the DB."""
    art = db.execute(
        scoped(Artifact, org_id).where(Artifact.type == "market_brief")
        .order_by(Artifact.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if art is None:
        return None
    return _serialize_artifact(art)


def _parent_artifact(db: Session, org_id: str,
                     parent_artifact_id: str | None) -> dict | None:
    """Load a specific prior artifact by id via scoped() — used by the
    content_engine's "Give me something better" path. Returns None when the
    id is absent, missing, or belongs to a different org. Cross-tenant safety
    is enforced here so the agent never sees another org's artifact even if
    a client supplies a guessed id."""
    if not parent_artifact_id:
        return None
    art = db.execute(
        scoped(Artifact, org_id).where(Artifact.id == parent_artifact_id)
    ).scalar_one_or_none()
    if art is None:
        return None
    return _serialize_artifact(art)


def _build_guardrail_rules(db: Session, org_id: str,
                           profile: dict | None) -> dict[str, dict]:
    """Build the rules-by-scope dict the agent sees through ctx.guardrail_rules
    (and that this worker uses to gate proposals). Merges OrgProfile.banned_claims
    into the "content" scope so the profile stays the single source of truth —
    edit the profile, the content guardrail follows."""
    rules: dict[str, dict] = {
        g.scope: dict(g.rules or {})
        for g in db.execute(scoped(Guardrail, org_id)).scalars()
    }
    if profile and profile.get("banned_claims"):
        content_rules = rules.setdefault("content", {})
        content_rules["banned_claims"] = list(profile["banned_claims"])
    return rules


def _confirmed_org_profile(db: Session, org_id: str) -> dict | None:
    """Load the org's CONFIRMED OrgProfile (via scoped()) as the plain dict the
    agent reads through ctx.org_profile. Returns None when no confirmed profile
    exists, so the agent falls back to its seed config (no regression). A draft
    (confirmed=false) is intentionally NOT passed — only the user's saved truth
    drives a run."""
    prof = db.execute(scoped(OrgProfile, org_id)).scalar_one_or_none()
    if prof is None or not prof.confirmed:
        return None
    return {
        "product_summary": prof.product_summary,
        "value_prop": prof.value_prop,
        "icp": prof.icp or {},
        "competitors": prof.competitors or [],
        "keywords": prof.keywords or [],
        "brand_voice": prof.brand_voice,
        "banned_claims": prof.banned_claims or [],
        "conversion_goal": prof.conversion_goal,
        "website_url": prof.website_url,
        "content_review_mode": prof.content_review_mode or "guardrail",
    }


def _audit(db: Session, org_id: str, actor: str, action: str, target_type: str,
           target_id: str, meta: dict | None = None) -> None:
    db.add(AuditLog(org_id=org_id, actor=actor, action=action,
                    target_type=target_type, target_id=target_id, meta=meta or {}))


def process_run(db: Session, run: Run) -> None:
    reg = db.get(AgentRegistration, run.agent_registration_id)
    if reg is None or not reg.enabled:
        raise RuntimeError("Agent registration missing or disabled")
    # Belt-and-suspenders: PK lookup, so assert org match before we trust it.
    assert_same_org(reg, run.org_id)

    run.status = "running"
    run.started_at = _now()
    db.commit()

    org = db.get(Org, run.org_id)
    org_name = org.name if org else run.org_id
    logs: list[str] = []
    org_profile = _confirmed_org_profile(db, run.org_id)
    # Resolved profile = org + (optional) product layer + per-field
    # provenance. This is the canonical input the agent reads through
    # ctx.profile. With no product selected on the run, it's effectively
    # the org-level view (backwards-compatible).
    resolved_profile = resolve_product_profile(db, run.org_id, run.product_id)
    rules_by_scope = _build_guardrail_rules(db, run.org_id, org_profile)
    # Per-run task: prefer the explicit one persisted on the Run row (set by
    # the API caller), fall back to the agent's default_task from its config.
    task = run.task if run.task else reg.config.get("default_task", {})
    # Pre-load persistent marketing memory. The agent never queries the DB;
    # the worker hands it a list of evidence-backed Pattern dicts. With no
    # telemetry yet, this returns [] and agents fall back to today's
    # behavior — full backwards compat. Memory is the ONE shared service
    # both content_engine and the campaign planner call (planner calls it
    # directly from /api/campaigns/{id}/propose where a db is on hand).
    from app.memory import query_memory  # local import: keeps cold paths cheap
    memory_patterns = query_memory(
        db, run.org_id,
        product_id=run.product_id,
        campaign_type=(task.get("campaign_type") if isinstance(task, dict) else None),
        content_type=(task.get("content_type") if isinstance(task, dict) else None),
    )
    # Reports are the only agent that needs a pre-computed intelligence
    # object on ctx (same chassis contract as memory_patterns — the agent
    # never queries the DB). Only fire the engine for report_composer
    # runs so we don't waste a query on every market_intel/content_engine
    # tick.
    report_intelligence = None
    if run.agent_key == "report_composer" and isinstance(task, dict):
        from app.reports import build_report_intelligence  # local import
        scope = task.get("scope") or {}
        try:
            report_intelligence = build_report_intelligence(
                db, run.org_id, product_id=run.product_id, scope=scope,
                lookback_days=int(task.get("lookback_days") or 30))
        except Exception as exc:
            # Don't take down the worker on a bad scope — let the agent
            # see the empty intelligence and raise a meaningful error.
            logs.append(f"report intelligence engine raised: {exc!r}")
            report_intelligence = None
    # Tenant brand identity — PRESENTATION ONLY. Pre-load so the agent
    # never touches the DB. Defaults apply when no row exists; unset is
    # not an error. Brand tokens MUST stay on the chrome path inside
    # the agent (verbatim pass-through to renderers) and never enter
    # the intelligence object, evidence ledger, prompt selection, or
    # any §6/§7 validator input.
    from app.api.brand import brand_for_org  # local: keeps cold paths cheap
    brand = brand_for_org(db, run.org_id)
    ctx = AgentContext(
        org_id=run.org_id,
        org_name=org_name,
        agent_key=run.agent_key,
        registration_id=reg.id,
        run_id=run.id,
        trigger=run.trigger,
        task=task,
        brand_guide=reg.config.get("brand_guide", {}),
        icp=reg.config.get("icp", {}),
        config=reg.config,
        org_profile=org_profile,
        profile=resolved_profile,
        product_id=run.product_id,
        # The agent reads "what else exists for this org" only through this
        # list. Brief always; parent draft only when the task names one (the
        # regenerate path). Both are loaded via scoped() so cross-tenant ids
        # can't sneak through, even if a client guesses one.
        prior_artifacts=[a for a in (
            _parent_artifact(db, run.org_id,
                             (run.task or {}).get("parent_artifact_id")),
            _latest_market_brief(db, run.org_id),
        ) if a],
        guardrail_rules=rules_by_scope,
        memory_patterns=memory_patterns,
        report_intelligence=report_intelligence,
        brand=brand,
        get_market_data=lambda: _resolve_market_data(db, run.org_id, run.upload_id),
        log=logs.append,
    )

    agent = get_agent(run.agent_key)        # builtin resolution (http/mcp kinds: P3)
    result = agent.run(ctx)

    # Optional campaign back-reference. Campaign Builder enqueues runs
    # with task.campaign_id so the artifact + proposal carry the back-ref.
    # NULL = the run wasn't part of a campaign (the historical default).
    # Per the brief, the artifact stays a first-class asset even with a
    # back-ref — archiving the campaign SET NULLs the column rather than
    # deleting the asset.
    task_campaign_id = (run.task or {}).get("campaign_id")

    # Persist artifacts (with status — defaults to "ready", content_engine may
    # set "pending_review" for drafts that need queue review).
    artifact_rows: list[Artifact] = []
    for a in result.artifacts:
        row = Artifact(
            org_id=run.org_id, run_id=run.id,
            # Stamp the product on every artifact so the dashboard can
            # filter by product without re-joining through Run.
            product_id=run.product_id,
            campaign_id=task_campaign_id,
            type=a.type, title=a.title,
            body=a.body, citations=[c.model_dump() for c in a.citations],
            status=getattr(a, "status", "ready"),
            # New content-engine columns (None for non-content artifacts).
            parent_id=getattr(a, "parent_id", None),
            grade=getattr(a, "grade", None),
            utm_campaign=getattr(a, "utm_campaign", None),
            utm_source=getattr(a, "utm_source", None),
            utm_medium=getattr(a, "utm_medium", None),
            utm_content=getattr(a, "utm_content", None),
            destination_url=getattr(a, "destination_url", None),
        )
        db.add(row)
        artifact_rows.append(row)
    # Flush so the new artifact rows have ids the proposals can reference.
    db.flush()

    # Persist proposals, each gated before it could ever execute.
    for p in result.proposed_actions:
        status, detail = guardrails.evaluate(p, rules_by_scope)
        db.add(Proposal(
            org_id=run.org_id, run_id=run.id,
            product_id=run.product_id,
            campaign_id=task_campaign_id,
            action_type=p.action_type, payload=p.payload,
            guardrail_scope=p.guardrail_scope, guardrail_status=status, guardrail_detail=detail,
            reasoning=p.reasoning, status="pending",
        ))

    run.status = "succeeded"
    run.cost_usd = result.cost_usd
    run.finished_at = _now()
    run.logs = logs + result.logs
    _audit(db, run.org_id, "system", "run.succeeded", "run", run.id,
           {"agent": run.agent_key, "artifacts": len(result.artifacts),
            "proposals": len(result.proposed_actions), "cost_usd": result.cost_usd})
    db.commit()


def process_extraction(db: Session, doc_id: str, org_id: str) -> None:
    """Document-ingestion job: normalize text → call extractor → persist
    candidates. Errors land on product_document.extraction_error and the
    document's status flips to 'failed' — we NEVER let the worker crash
    over a bad upload, missing OCR binary, or LLM outage.

    Cross-tenant safety: load the doc via scoped() (org_id derived from
    the job — which the queue already verified against the run's
    creator). Reads the raw file from the tenant-scoped storage path
    written at upload time."""
    doc = db.execute(
        scoped(ProductDocument, org_id).where(ProductDocument.id == doc_id)
    ).scalar_one_or_none()
    if doc is None:
        raise RuntimeError(f"ProductDocument {doc_id} not found for org {org_id}")
    assert_same_org(doc, org_id)

    def _fail(message: str) -> None:
        doc.status = "failed"
        doc.extraction_error = message
        doc.updated_at = _now()
        db.commit()
        _audit(db, org_id, "system", "doc.extraction_failed",
               "product_document", doc.id, {"reason": message})
        db.commit()

    # --- Step 1: read + normalize the bytes ---------------------------
    try:
        raw = read_document(doc.storage_path)
    except Exception as exc:
        return _fail(f"Could not read stored file: {exc}")

    try:
        normalized = normalize(raw, doc.filename, doc.mime_type)
    except NormalizationError as exc:
        # Friendly errors (e.g. Tesseract missing) flow through here.
        return _fail(str(exc))
    except Exception as exc:
        return _fail(f"Unexpected normalization error: {exc}")

    text = (normalized.get("text") or "").strip()
    doc.extracted_text = text
    db.commit()
    if not text:
        return _fail("No text could be extracted from the document.")

    # --- Step 2: extract candidates via the LLM -----------------------
    # The resolver gives the agent's standard view of profile context; we
    # reuse it to ground the extractor's prompt the same way the agent
    # would see it.
    resolved = resolve_product_profile(db, org_id, doc.product_id)
    candidates, cost = extract_candidates(text, resolved.get("_org") or {},
                                          resolved.get("product") or {},
                                          doc_kind=doc.kind or "messaging_framework")
    if candidates is None:
        return _fail("LLM unavailable (no API key, parse failure, or API error). "
                     "Configure ANTHROPIC_API_KEY or retry.")

    # --- Step 3: persist candidate insights ----------------------------
    # OCR-derived candidates inherit a "via=ocr" anchor on their source
    # location so the UI can render lower-trust badges. Per-anchor pages/
    # slides aren't matched back to specific candidates in v1 (the LLM
    # quotes a verbatim passage; we'd need an index pass to figure out
    # which page it came from). source_location.via flags OCR globally.
    via_ocr = bool(normalized.get("ocr_used"))
    persisted = 0
    for c in candidates:
        loc = {"via": "ocr"} if via_ocr else None
        db.add(ExtractedInsight(
            org_id=org_id, product_id=doc.product_id,
            product_document_id=doc.id,
            field_name=c["field_name"],
            value=c["value"],
            confidence=c.get("confidence", 0.0),
            source_passage=c.get("source_passage", ""),
            source_location=loc,
            dimensions=None,    # reserved for variants; v1 always null
            status="pending",
        ))
        persisted += 1
    doc.status = "extracted"
    doc.extraction_error = None
    doc.updated_at = _now()
    _audit(db, org_id, "system", "doc.extracted",
           "product_document", doc.id,
           {"candidates": persisted, "cost_usd": cost,
            "via_ocr": via_ocr})
    db.commit()


def run_once(db: Session | None = None) -> bool:
    """Process at most one job. Returns True if a job was handled.

    Dispatches by job.kind so the worker can grow new job types without
    touching the queue plumbing:
      * 'run_agent'        → process_run (the legacy agent runs).
      * 'extract_document' → process_extraction (Phase 1, Build B).
    Anything else is failed loudly so we don't silently lose work."""
    own = db is None
    db = db or SessionLocal()
    try:
        job = lease_next(db)
        if job is None:
            return False
        # We use `run` for the agent path's error-marking; extraction jobs
        # don't have a run row.
        run: Run | None = None
        try:
            if job.kind == "run_agent":
                run = db.get(Run, job.payload.get("run_id"))
                if run is None:
                    raise RuntimeError(f"Run {job.payload.get('run_id')} not found")
                # PK lookup; verify the job and run agree on tenant before any work.
                assert_same_org(run, job.org_id)
                process_run(db, run)
            elif job.kind == "extract_document":
                doc_id = (job.payload or {}).get("product_document_id")
                if not doc_id:
                    raise RuntimeError("extract_document job missing product_document_id")
                process_extraction(db, doc_id, job.org_id)
            else:
                raise RuntimeError(f"Unknown job kind {job.kind!r}")
            complete(db, job)
        except Exception as exc:  # graceful failure: mark run + job, never crash loop
            db.rollback()
            if run is not None:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = _now()
                _audit(db, run.org_id, "system", "run.failed", "run", run.id, {"error": str(exc)})
                db.commit()
            complete(db, job, error=str(exc))
        return True
    finally:
        if own:
            db.close()


def run_forever() -> None:
    settings = get_settings()
    print(f"[worker] polling every {settings.worker_poll_seconds}s "
          f"(env={settings.app_env}, db={'sqlite' if settings.is_sqlite else 'postgres'})")
    while True:
        worked = run_once()
        if not worked:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_forever()
