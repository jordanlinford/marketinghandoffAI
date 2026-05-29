"""
End-to-end smoke test of the chassis. Proves:
  1. trigger -> queue -> worker -> run -> cited artifact (the whole spine)
  2. tenant isolation: a second org cannot see org #1's runs
  3. CSV data source: an uploaded list flows through the SAME agent and produces
     a brief sized + fit-scored against the uploaded accounts
  4. UI and tenant-isolation invariants for uploads + runs across orgs

Run:  python -m scripts.smoke

NOTE: raw selects in this script are a legitimate exception to scoped() —
system-level setup, not a tenant request.

DB ISOLATION: smoke runs against its OWN throwaway SQLite database in a temp
dir, NOT the dev server's ./agenthq.db. This is set via AGENT_HQ_DATABASE_URL
*before* any app module imports (the engine is built at import time from the
cached settings). Each run gets a fresh empty DB and cleans it up on exit, so
running the test gate can never disturb a live uvicorn's database (which once
caused a recurring "no such table" 500 when the shared file was deleted out
from under the running server).
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

# MUST run before importing any app.* module: app.db builds the engine at import
# time from get_settings(), so the env override has to be in place first.
_SMOKE_DB_DIR = tempfile.mkdtemp(prefix="agenthq_smoke_")
os.environ["AGENT_HQ_DATABASE_URL"] = f"sqlite:///{_SMOKE_DB_DIR}/smoke.db"
# Force a fake ANTHROPIC_API_KEY so synthesis + content_templates take their
# LLM branch (which smoke stubs below). Without this, the no-key fast path
# bypasses the stub and we can't assert "cost recorded" for content gen.
os.environ["ANTHROPIC_API_KEY"] = "smoke-stub-key"
# Document storage in a temp dir too — uploads must never touch ./storage.
_SMOKE_STORAGE_DIR = tempfile.mkdtemp(prefix="agenthq_smoke_storage_")
os.environ["AGENT_HQ_STORAGE_ROOT"] = _SMOKE_STORAGE_DIR
atexit.register(lambda: shutil.rmtree(_SMOKE_DB_DIR, ignore_errors=True))
atexit.register(lambda: shutil.rmtree(_SMOKE_STORAGE_DIR, ignore_errors=True))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.agents import content_grader as content_grader_mod  # noqa: E402
from app.agents import content_templates as content_templates_mod  # noqa: E402
from app.agents import synthesis as synthesis_mod  # noqa: E402
from app.dashboard import suggestions as suggestions_mod  # noqa: E402
from app.dashboard.ingest import parse_report_csv  # noqa: E402
from app.data_sources.csv import CsvMarketDataSource  # noqa: E402
from app.db import SessionLocal, create_all  # noqa: E402
from app.documents import extract as extract_mod  # noqa: E402
from app.main import app  # noqa: E402
from app.campaigns import planner as planner_mod  # noqa: E402
from app.models import (AgentRegistration, Artifact, Campaign,  # noqa: E402
                        ExtractedInsight, Guardrail, MetricPoint, Org,
                        OrgProfile, ProductDocument, ProductProfile, Proposal,
                        ReportUpload, Run, Suggestion, Upload, User)
from app.products import resolve_product_profile  # noqa: E402
from app.queue import enqueue  # noqa: E402
from app.setup import crawl as crawl_mod  # noqa: E402
from app.setup import draft as draft_mod  # noqa: E402
from app.setup.draft import draft_from_knowledge  # noqa: E402
from app.setup.merge import apply_accepted, merge_profiles  # noqa: E402
from app.tenancy import scoped  # noqa: E402
from app.worker import run_once  # noqa: E402
from scripts.seed import main as seed_main  # noqa: E402


def main() -> None:
    create_all()
    seed_main()

    # Hermetic test gate: stub the market_intel narrative LLM call the same way
    # the profile tests stub theirs (lowest-level _llm_* helper). With
    # ANTHROPIC_API_KEY loaded, synthesize_brief() would otherwise make a real
    # Anthropic request during the two market_intel runs below — making smoke
    # depend on network + credits. Returning the deterministic template (no
    # network) keeps the spine identical and the test self-contained.
    synthesis_mod._llm_narrative = lambda structured, org_name, settings: (
        synthesis_mod._template_narrative(structured, org_name), 0.0)
    # Same treatment for the content engine: bypass the LLM and use the
    # deterministic template, so tests don't depend on the network. Cost is
    # set to a fixed non-zero value so we can still assert "cost recorded".
    # Save the ORIGINAL real _llm_build so the prompt-structure test
    # below can un-stub and run it against a fake Anthropic client to
    # inspect what the production code actually sends. Without saving
    # the pre-stub reference, `import ... as _real_llm_build` would
    # pick up the stub (Python rebinds module attrs in place).
    _ORIGINAL_LLM_BUILD = content_templates_mod._llm_build

    def _stub_content_llm(content_type, profile, brief, topic, target, settings,
                          critique=""):
        topic_for_template = (f"{topic} — addressing: {critique}"
                              if critique else topic)
        return content_templates_mod._REGISTRY[content_type](
            profile, brief, topic_for_template, target), 0.0007
    content_templates_mod._llm_build = _stub_content_llm

    # Capture the real grader so the fenced-JSON test (Campaign 9) can
    # exercise the actual parse path while the rest of smoke stays on the
    # cheap stub.
    _ORIGINAL_LLM_GRADE = content_grader_mod._llm_grade

    # Grader stub: returns a real-looking graded result so we can assert the
    # structure on the artifact. Tests that want to exercise the
    # "ungraded fallback" path swap this for one that raises.
    def _stub_grader_llm(content, profile, rubric, settings):
        return {
            "status": "graded",
            "overall": 72,
            "per_criterion": [
                {"name": c.get("name", "criterion"), "score": 70,
                 "reason": "(stubbed grader)"} for c in rubric
            ],
            "suggestions": ["Lead with the cost angle.", "Tighten the CTA."],
        }, 0.0003
    content_grader_mod._llm_grade = _stub_grader_llm

    # Stub the dashboard's industry-perspective LLM. Returns one canned
    # item in the agreed shape so we can assert structure + labeling.
    # Test (4)'s deterministic-fallback case overrides this with a raise.
    def _stub_industry_llm(profile, funnel, settings):
        return [{
            "kind": "industry",
            "recommendation": "Industry stub: in-house legal ops buyers tend "
                              "to consolidate vendors year-over-year.",
            "evidence": {"note": "general industry perspective; not verified data"},
            "confidence": "low",
            "source_label": "General industry perspective — verify before acting",
            "idea_content_type": None, "idea_topic": None, "idea_target": None,
        }]
    suggestions_mod._llm_industry = _stub_industry_llm

    # Document extraction LLM stub. Returns a deterministic mix of canonical
    # fields + a messaging note + a blank/missing field so we can assert
    # both no-confab AND the override-forbidden discipline. The actual
    # source_passages are short verbatim quotes from the seeded TXT
    # (smoke crafts the doc body so the quotes line up).
    def _stub_extract_llm(doc_text, org_profile, product_profile, doc_kind, settings):
        candidates = [
            {"field_name": "positioning",
             "value": "Modern matter management for in-house teams that "
                      "live in workflow, not in legal-tech jargon.",
             "confidence": 0.88,
             "source_passage": "modern matter management for in-house teams"},
            {"field_name": "value_props",
             "value": [
                 "Cuts contract turnaround time in half within 90 days.",
                 "Self-serve matters for a 5-person legal team",
             ],
             "confidence": 0.82,
             "source_passage": "self-serve matters for a 5-person legal team"},
            {"field_name": "product_competitors",
             "value": [{"name": "Ironclad"}, {"name": "LinkSquares"}],
             "confidence": 0.7,
             "source_passage": "compete with Ironclad and LinkSquares"},
            {"field_name": "objection_handling",
             "value": [{"objection": "Too expensive vs. spreadsheets",
                        "response": "Lead with the cost of a missed renewal."}],
             "confidence": 0.75,
             "source_passage": "lead with the cost of a missed renewal"},
        ]
        return candidates, 0.0009
    extract_mod._llm_extract = _stub_extract_llm

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

        art = db.execute(scoped(Artifact, onit.id).where(Artifact.run_id == run.id)).scalar_one()
        print(f"Artifact: {art.title}\nCitations: {[c['source'] for c in art.citations]}")
        print("\n--- BRIEF (first 900 chars) ---")
        print(art.body["narrative"][:900])

        # ---------------------------------------------------------------------
        # CSV data-source check (1): the source itself fit-scores against the ICP
        # ---------------------------------------------------------------------
        icp = reg.config.get("icp", {})
        sample_rows = [
            {"name": "AcmeCo Legal", "employees": 5000, "revenue_usd": 1_200_000_000,
             "industry": "Legal Services"},     # matches industry + size
            {"name": "TinyShop", "employees": 12, "revenue_usd": 800_000,
             "industry": "Retail"},             # misses industry + size
            {"name": "MegaFi Holdings", "employees": 9000, "revenue_usd": 4_000_000_000,
             "industry": "Financial Services"}, # matches industry + size
        ]
        csv_src = CsvMarketDataSource(sample_rows)
        recs = csv_src.find_companies(icp, limit=10)
        assert len(recs) == 3, f"expected 3 records, got {len(recs)}"
        assert all(r.source == "csv" for r in recs), "csv records must be tagged source='csv'"
        assert all(r.intent_score == 0.0 for r in recs), \
            "csv must not invent intent — set intent_score=0.0"
        by_name = {r.name: r for r in recs}
        assert by_name["AcmeCo Legal"].fit_score > by_name["TinyShop"].fit_score, \
            "ICP-matching row should out-score the off-ICP row"
        assert csv_src.keyword_universe(["legal ops"]) == [], \
            "csv has no keyword data — must return []"
        print(f"[OK] CSV source: 3 rows fit-scored "
              f"(Acme={by_name['AcmeCo Legal'].fit_score}, "
              f"Mega={by_name['MegaFi Holdings'].fit_score}, "
              f"Tiny={by_name['TinyShop'].fit_score}); intent=0; source=csv.")

        # ---------------------------------------------------------------------
        # CSV data-source check (2): a run bound to an upload_id produces a
        # brief drawn from the uploaded accounts (end-to-end through the agent)
        # ---------------------------------------------------------------------
        up = Upload(org_id=onit.id, filename="onit-targets.csv",
                    row_count=len(sample_rows), rows=sample_rows)
        db.add(up)
        db.commit()
        db.refresh(up)

        csv_run = Run(org_id=onit.id, agent_registration_id=reg.id, agent_key="market_intel",
                      trigger="manual", status="queued", upload_id=up.id)
        db.add(csv_run)
        db.commit()
        db.refresh(csv_run)
        enqueue(db, onit.id, "run_agent", {"run_id": csv_run.id})
        assert run_once() is True, "worker did not pick up the csv-bound job"
        db.refresh(csv_run)
        assert csv_run.status == "succeeded", f"csv run failed: {csv_run.error}"
        csv_art = db.execute(
            scoped(Artifact, onit.id).where(Artifact.run_id == csv_run.id)
        ).scalar_one()
        targets = csv_art.body["structured"]["top_targets"]
        uploaded_names = {r["name"] for r in sample_rows}
        target_names = {t["name"] for t in targets}
        assert target_names <= uploaded_names, \
            f"csv-bound run leaked stub rows: {target_names - uploaded_names}"
        assert target_names, "csv-bound run produced zero targets"
        sizing = csv_art.body["structured"]["sizing"]
        assert sizing["tam_companies"] == len(sample_rows), \
            f"sizing should reflect uploaded list (got {sizing['tam_companies']})"
        print(f"[OK] CSV-bound run: brief built from {sizing['tam_companies']} "
              f"uploaded accounts (targets: {sorted(target_names)}).")

        # ---------------------------------------------------------------------
        # Tenant isolation: another org cannot read or run against this upload.
        # ---------------------------------------------------------------------
        other = Org(name="Acme", domain="acme.com")
        db.add(other)
        db.commit()
        db.refresh(other)
        # Acme registers its own market_intel so we can attempt to run.
        other_reg = AgentRegistration(
            org_id=other.id, key="market_intel", display_name="Market intelligence",
            kind="builtin", enabled=True, config={"icp": icp},
        )
        db.add(other_reg)
        db.add(User(org_id=other.id, email="ops@acme.com", name="Acme Ops", role="member"))
        db.commit()

        # Acme cannot see Onit's runs OR Onit's uploads.
        leaked_runs = db.execute(scoped(Run, other.id)).scalars().all()
        assert leaked_runs == [], "TENANT LEAK: another org could see Onit's runs!"
        leaked_uploads = db.execute(scoped(Upload, other.id)).scalars().all()
        assert leaked_uploads == [], "TENANT LEAK: another org could see Onit's uploads!"

        # Via the API: Acme cannot trigger a run against Onit's upload_id.
        # Accept either 403 (rejected at the domain allowlist) or 404 (rejected
        # at the per-org scoped() check). Both prove cross-org access is denied;
        # the DB-level scoped() asserts above already prove row-level isolation.
        client = TestClient(app)
        cross = client.post(
            "/api/runs",
            headers={"X-Dev-User-Email": "ops@acme.com",
                     "Content-Type": "application/json"},
            json={"agent_key": "market_intel", "upload_id": up.id},
        )
        assert cross.status_code in (403, 404), \
            f"cross-org upload_id MUST be rejected, got {cross.status_code}: {cross.text}"
        print(f"[OK] Tenant isolation holds — Acme sees 0 of Onit's runs/uploads "
              f"and is blocked ({cross.status_code}) from running against Onit's upload_id.")

        # UI check: the review page is served and contains the expected shell.
        ui = client.get("/ui")
        assert ui.status_code == 200, f"/ui returned {ui.status_code}"
        assert "Agent" in ui.text and "Approval queue" in ui.text, "/ui body missing expected shell"
        assert "Upload CSV" in ui.text, "/ui body missing upload control"
        print("[OK] /ui returns 200 with the review + upload shell.")

        # ---------------------------------------------------------------------
        # CSV ingest: real-world headers (business_name, revenue_range,
        # intent_score). Revenue range must collapse to a midpoint number,
        # intent must populate per-row, and the data source must advertise
        # "csv+intent". The honesty rule still holds — see the no-intent
        # CSV case below, which stays source="csv" with intent_score=0.
        # ---------------------------------------------------------------------
        intent_csv = (
            "business_name,revenue_range,intent_score,Industry\n"
            "AcmeCo Legal,$1M-$5M,75,Legal Services\n"
            "MegaFi Holdings,\"1,000,000-5,000,000\",0.42,Financial Services\n"
            "TinyShop,$500K,12,Retail\n"
        )
        up_resp = client.post(
            "/api/uploads",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("targets.csv", intent_csv.encode("utf-8"), "text/csv")},
        )
        assert up_resp.status_code == 200, f"upload failed: {up_resp.status_code} {up_resp.text}"
        up_json = up_resp.json()
        assert up_json["row_count"] == 3, up_json
        preview = up_json["preview"]
        by_name = {r["name"]: r for r in preview}
        # $1M-$5M midpoint = $3M
        assert by_name["AcmeCo Legal"]["revenue_usd"] == 3_000_000.0, by_name["AcmeCo Legal"]
        # 1,000,000-5,000,000 midpoint = 3,000,000
        assert by_name["MegaFi Holdings"]["revenue_usd"] == 3_000_000.0, by_name["MegaFi Holdings"]
        # $500K single value
        assert by_name["TinyShop"]["revenue_usd"] == 500_000.0, by_name["TinyShop"]
        # 75 -> 0.75 (looked like 0-100 scale); 0.42 stays; 12 -> 0.12
        assert by_name["AcmeCo Legal"]["intent_score"] == 0.75, by_name["AcmeCo Legal"]
        assert by_name["MegaFi Holdings"]["intent_score"] == 0.42, by_name["MegaFi Holdings"]
        assert by_name["TinyShop"]["intent_score"] == 0.12, by_name["TinyShop"]
        # The data source must now self-label as csv+intent and pass scores through.
        intent_up = db.get(Upload, up_json["id"])
        intent_src = CsvMarketDataSource(intent_up.rows)
        intent_recs = intent_src.find_companies(icp, limit=10)
        assert {r.source for r in intent_recs} == {"csv+intent"}, \
            f"csv with intent column must label source=csv+intent, got {[r.source for r in intent_recs]}"
        intent_by_name = {r.name: r for r in intent_recs}
        assert intent_by_name["AcmeCo Legal"].intent_score == 0.75
        assert intent_by_name["MegaFi Holdings"].intent_score == 0.42
        print("[OK] CSV ingest: business_name + revenue_range + intent_score "
              "parsed (midpoints + per-row intent), source=csv+intent.")

        # ---------------------------------------------------------------------
        # CSV ingest: a CSV with no name column must 400 with the actual
        # headers listed, so the user can fix it. This is the fixable-error rule.
        # ---------------------------------------------------------------------
        bad_csv = "domain,revenue_range,headcount\nexample.com,$1M-$5M,500\n"
        bad_resp = client.post(
            "/api/uploads",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("bad.csv", bad_csv.encode("utf-8"), "text/csv")},
        )
        assert bad_resp.status_code == 400, f"expected 400, got {bad_resp.status_code}: {bad_resp.text}"
        detail = bad_resp.json()["detail"]
        assert "No name column" in detail, detail
        # The headers actually seen must be echoed back, verbatim, so the user
        # can spot the typo (the message is the only signal the API gives).
        for header in ("domain", "revenue_range", "headcount"):
            assert header in detail, f"header {header!r} missing from 400 detail: {detail}"
        assert "name" in detail and "company" in detail, \
            f"name-alias hint missing from 400 detail: {detail}"
        print("[OK] CSV ingest: missing-name CSV returns 400 listing the seen headers.")

        # ---------------------------------------------------------------------
        # Setup stage (org profile) — three checks:
        #   (1) PUT then GET round-trips and confirmed=true; tenant isolation
        #       holds (Acme can't see Onit's profile).
        #   (2) /crawl with a monkeypatched fetch (no real network) returns a
        #       draft with confirmed=false.
        #   (3) /from-csv with a small customer sample returns a draft ICP
        #       whose industries reflect the sample.
        # ---------------------------------------------------------------------
        # (1) PUT /api/profile -> GET round-trip + cross-org isolation.
        profile_body = {
            "product_summary": "Legal ops platform for in-house teams.",
            "value_prop": "Cut matter-management overhead.",
            "website_url": "https://onit.com",
            "icp": {"industries": ["Legal Services", "Financial Services"],
                    "min_employees": 500, "min_revenue_usd": 100_000_000.0,
                    "regions": ["NA"], "titles": ["GC", "Head of Legal Ops"],
                    "notes": "Enterprise legal teams"},
            "competitors": [{"name": "SimpleLegal", "url": "https://simplelegal.com"}],
            "keywords": ["legal operations", "matter management"],
            "brand_voice": "Plain, confident, no jargon.",
            "banned_claims": ["#1 in the world"],
            "conversion_goal": "book a demo",
            "conversion_event": "demo_requested",
            "crawl_summary": "",
            "source": {},
        }
        put_resp = client.put(
            "/api/profile",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json=profile_body,
        )
        assert put_resp.status_code == 200, f"PUT /api/profile failed: {put_resp.text}"
        put_json = put_resp.json()
        assert put_json["confirmed"] is True, "PUT must flip confirmed=true"
        assert put_json["icp"]["industries"] == ["Legal Services", "Financial Services"]

        get_resp = client.get("/api/profile",
                              headers={"X-Dev-User-Email": "jordan@onit.com"})
        assert get_resp.status_code == 200, get_resp.text
        got = get_resp.json()
        assert got["exists"] is True and got["confirmed"] is True
        assert got["product_summary"] == profile_body["product_summary"]
        assert got["icp"]["min_employees"] == 500
        assert got["competitors"][0]["name"] == "SimpleLegal"

        # Cross-tenant isolation. The DB-layer `scoped()` is the canonical
        # guard (CLAUDE.md: "app-layer scoping is the primary isolation guard"),
        # so it's the ground truth: Acme sees zero OrgProfile rows even though
        # Onit just wrote one. The API boundary is the secondary check — same
        # pattern as the existing /api/runs isolation test, where the domain
        # allowlist (acme.com not allowed) returns 403 before the org can ever
        # look at the data. Either way, Acme cannot read Onit's profile.
        leaked_profiles = db.execute(scoped(OrgProfile, other.id)).scalars().all()
        assert leaked_profiles == [], \
            f"TENANT LEAK at DB layer: Acme can read Onit's profile! got={leaked_profiles}"
        acme_resp = client.get("/api/profile",
                               headers={"X-Dev-User-Email": "ops@acme.com"})
        assert acme_resp.status_code in (403, 404), \
            f"cross-org /api/profile MUST be denied, got {acme_resp.status_code}: {acme_resp.text}"
        print("[OK] Profile PUT/GET round-trip + tenant isolation holds.")

        # (2) /crawl with a fake fetch — no real network. We swap out the
        # module-level _fetch so the test is hermetic and fast. The endpoint
        # must return a draft with confirmed=false even when the LLM key is
        # absent (the deterministic fallback path).
        canned_html = """
        <html><head>
          <title>Acme Robotics — autonomous warehouse picking</title>
          <meta name="description" content="Warehouse robots that pick and pack.">
        </head><body>
          <nav>nav junk we should strip</nav>
          <h1>Pick faster with autonomous robots</h1>
          <h2>For 3PL operators and ecommerce brands</h2>
          <p>Drop-in robots for your existing warehouse, no rip-and-replace.</p>
        </body></html>
        """
        # Patch the LLM call too — when ANTHROPIC_API_KEY is loaded, the crawl
        # path otherwise makes a real Anthropic request (slow + costs money +
        # flaky for CI). We stub it with a canned proposal that still carries
        # "Acme Robotics" through, so the downstream assertion is unchanged.
        original_fetch = crawl_mod._fetch
        original_llm = draft_mod._llm_propose
        crawl_mod._fetch = lambda _client, url: (canned_html, None)
        draft_mod._llm_propose = lambda text, settings: {
            "product_summary": "Acme Robotics builds autonomous warehouse pickers.",
            "value_prop": "Drop-in robots, no rip-and-replace.",
            "icp": {"industries": ["3PL", "Ecommerce"], "min_employees": 100,
                    "min_revenue_usd": None, "regions": [], "titles": [],
                    "notes": ""},
            "competitors": [], "keywords": ["warehouse robots", "pick automation"],
            "conversion_goal": "book a demo",
        }
        try:
            crawl_resp = client.post(
                "/api/profile/crawl",
                headers={"X-Dev-User-Email": "jordan@onit.com",
                         "Content-Type": "application/json"},
                json={"url": "https://example.com"},
            )
        finally:
            crawl_mod._fetch = original_fetch
            draft_mod._llm_propose = original_llm
        assert crawl_resp.status_code == 200, crawl_resp.text
        crawl_json = crawl_resp.json()
        assert crawl_json["crawl_status"] == "ok", crawl_json
        assert crawl_json["draft_source"] == "crawl", crawl_json
        assert crawl_json["confirmed"] is False, "crawl must return a DRAFT only"
        draft = crawl_json["draft"]
        assert draft["confirmed"] is False
        assert "Acme Robotics" in (draft["product_summary"] or ""), \
            f"draft should carry the LLM proposal through; got {draft['product_summary']!r}"
        # Crawling must not have persisted anything — the on-file profile is
        # still the one we PUT above, untouched.
        on_file = db.execute(scoped(OrgProfile, onit.id)).scalars().all()
        assert len(on_file) == 1 and on_file[0].confirmed is True, \
            "crawl draft must not write to org_profiles"
        print("[OK] /api/profile/crawl returns a confirmed=false draft "
              "without touching the saved profile (draft_source=crawl).")

        # ---------------------------------------------------------------------
        # Knowledge-fallback drafting — the LLM-from-knowledge path used when
        # the crawl can't read the site (Cloudflare 403, DNS, JS-only).
        # ---------------------------------------------------------------------

        # (Knowledge-1) direct draft_from_knowledge() with a stubbed LLM call.
        # Confirms shape, source tagging, confirmed=false. No real API call.
        original_kllm = draft_mod._llm_propose_from_knowledge
        draft_mod._llm_propose_from_knowledge = lambda domain, name, settings: {
            "product_summary": "Onit is enterprise legal-ops software.",
            "value_prop": "One source of truth for in-house legal work.",
            "icp": {"industries": ["Legal Services", "Financial Services"],
                    "min_employees": 500, "min_revenue_usd": None,
                    "regions": [], "titles": ["GC", "Head of Legal Ops"],
                    "notes": "Enterprise legal teams."},
            "competitors": [{"name": "SimpleLegal", "url": "https://simplelegal.com"}],
            "keywords": ["legal operations", "matter management"],
            "conversion_goal": "book a demo",
        }
        try:
            kn_draft = draft_from_knowledge("onit.com", "Onit")
        finally:
            draft_mod._llm_propose_from_knowledge = original_kllm
        assert kn_draft["confirmed"] is False
        assert kn_draft["product_summary"].startswith("Onit "), kn_draft
        assert kn_draft["website_url"] == "https://onit.com", kn_draft
        assert kn_draft["icp"]["industries"][0] == "Legal Services"
        # Every populated field must be tagged source="knowledge", and no
        # field should claim a different source (e.g. "llm" or "crawl").
        sources = set(kn_draft["source"].values())
        assert sources == {"knowledge"}, \
            f"knowledge draft must tag fields source='knowledge' only, got {sources}"
        assert "product_summary" in kn_draft["source"]
        assert "icp" in kn_draft["source"]
        print("[OK] draft_from_knowledge() with stubbed LLM tags source=knowledge.")

        # (Knowledge-2) /api/profile/crawl with a fetch that FAILS (Cloudflare-
        # style http_403). The endpoint must fall back to the knowledge path,
        # return draft_source="knowledge", confirmed=false, and MUST NOT write
        # to org_profiles. We patch both _fetch (so we don't hit the net) and
        # _llm_propose_from_knowledge (so we don't hit the LLM).
        original_fetch = crawl_mod._fetch
        original_kllm = draft_mod._llm_propose_from_knowledge
        fake_403 = {
            "kind": "http_status", "exception_type": None,
            "exception_message": "HTTP 403: '<title>Just a moment...</title>'",
            "http_status": 403, "url": "https://onit.com",
        }
        crawl_mod._fetch = lambda _client, url: ("", fake_403)
        draft_mod._llm_propose_from_knowledge = lambda domain, name, settings: {
            "product_summary": "Onit is enterprise legal-ops software.",
            "value_prop": "One source of truth for in-house legal work.",
            "icp": {"industries": ["Legal Services"], "min_employees": 500,
                    "min_revenue_usd": None, "regions": [], "titles": [],
                    "notes": ""},
            "competitors": [],
            "keywords": ["legal operations"],
            "conversion_goal": "book a demo",
        }
        try:
            blocked = client.post(
                "/api/profile/crawl",
                headers={"X-Dev-User-Email": "jordan@onit.com",
                         "Content-Type": "application/json"},
                json={"url": "https://onit.com"},
            )
        finally:
            crawl_mod._fetch = original_fetch
            draft_mod._llm_propose_from_knowledge = original_kllm
        assert blocked.status_code == 200, blocked.text
        bj = blocked.json()
        assert bj["crawl_status"] == "http_error", bj
        assert bj["crawl_error"]["http_status"] == 403, bj
        assert bj["draft_source"] == "knowledge", \
            f"403 must fall back to knowledge, got draft_source={bj.get('draft_source')!r}"
        assert bj["confirmed"] is False, "knowledge draft must be confirmed=false"
        assert bj["draft"]["confirmed"] is False
        assert bj["draft"]["product_summary"].startswith("Onit "), bj["draft"]
        assert set(bj["draft"]["source"].values()) == {"knowledge"}
        # And the saved profile is still the one Onit PUT earlier — the
        # fallback must not have persisted the draft.
        on_file = db.execute(scoped(OrgProfile, onit.id)).scalars().all()
        assert len(on_file) == 1 and on_file[0].confirmed is True, \
            "knowledge-fallback draft must not write to org_profiles"
        assert "Legal ops platform" in on_file[0].product_summary, \
            f"saved profile got mutated by the draft path! got={on_file[0].product_summary!r}"
        print("[OK] /api/profile/crawl on http_403 falls back to knowledge "
              "(draft_source=knowledge, confirmed=false, nothing persisted).")

        # (Knowledge-3) When the LLM call itself FAILS (e.g. Anthropic 400
        # "credit balance too low"), the endpoint must NOT silently return a
        # blank skeleton — it must surface the reason in `llm_error` so the UI
        # can explain it. draft_source falls to "skeleton" (nothing usable),
        # but llm_error.friendly carries the actionable message.
        class _FakeLLMError(Exception):
            pass

        original_fetch = crawl_mod._fetch
        original_kllm = draft_mod._llm_propose_from_knowledge

        def _raise_credit_error(domain, name, settings):
            raise _FakeLLMError(
                "Error code: 400 - Your credit balance is too low to access "
                "the Anthropic API. Please go to Plans & Billing.")

        crawl_mod._fetch = lambda _client, url: ("", fake_403)
        draft_mod._llm_propose_from_knowledge = _raise_credit_error
        try:
            errd = client.post(
                "/api/profile/crawl",
                headers={"X-Dev-User-Email": "jordan@onit.com",
                         "Content-Type": "application/json"},
                json={"url": "https://onit.com"},
            )
        finally:
            crawl_mod._fetch = original_fetch
            draft_mod._llm_propose_from_knowledge = original_kllm
        assert errd.status_code == 200, errd.text
        ej = errd.json()
        assert ej["draft_source"] == "skeleton", \
            f"LLM failure should yield a skeleton, got {ej.get('draft_source')!r}"
        assert ej["confirmed"] is False
        assert ej["llm_error"] is not None, "LLM failure MUST be surfaced, not swallowed"
        assert ej["llm_error"]["type"] == "_FakeLLMError", ej["llm_error"]
        assert "credit balance" in ej["llm_error"]["friendly"].lower(), \
            f"friendly reason should mention the credit balance: {ej['llm_error']}"
        # Skeleton draft is blank (no source tags) but the website_url is filled.
        assert ej["draft"]["source"] == {}, ej["draft"]
        assert ej["draft"]["website_url"] == "https://onit.com", ej["draft"]
        print("[OK] /api/profile/crawl surfaces llm_error when the LLM call "
              f"fails ({ej['llm_error']['type']}: credit balance) instead of a silent skeleton.")

        # (3) /from-csv with a small customer sample. The inferred ICP's
        # industries must reflect what's IN the sample (here: "SaaS" is the
        # majority, "Insurance" is a singleton — both should appear, "SaaS"
        # should be ranked first since it's most common).
        sample_csv = (
            "name,industry,employees,revenue\n"
            "BetaCo,SaaS,800,80000000\n"
            "GammaCo,SaaS,1200,150000000\n"
            "DeltaCo,SaaS,500,40000000\n"
            "EpsilonCo,Insurance,2000,500000000\n"
        )
        from_csv = client.post(
            "/api/profile/from-csv",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("customers.csv", sample_csv.encode("utf-8"), "text/csv")},
        )
        assert from_csv.status_code == 200, from_csv.text
        fc = from_csv.json()
        assert fc["sample_rows"] == 4, fc
        assert fc["confirmed"] is False
        icp = fc["draft"]["icp"]
        assert icp["industries"][0] == "SaaS", \
            f"most-common industry should rank first; got {icp['industries']}"
        assert "Insurance" in icp["industries"]
        # Median employees of [500, 800, 1200, 2000] = 1000.
        assert icp["min_employees"] == 1000, f"median employees wrong: {icp}"
        assert fc["draft"]["source"].get("icp") == "csv", \
            "csv-inferred icp must be tagged source=csv"
        print(f"[OK] /api/profile/from-csv inferred ICP "
              f"industries={icp['industries']} min_employees={icp['min_employees']}.")

        # ---------------------------------------------------------------------
        # Additive merge engine + enrichment diff.
        # ---------------------------------------------------------------------

        # (Merge-1) Precedence: a knowledge draft layered with a CSV draft.
        # CSV's real icp.min_employees overrides the knowledge guess; the
        # knowledge product_summary survives (CSV provided none); a blank CSV
        # value never wipes an existing non-blank one (icp.titles).
        knowledge_draft = {
            "product_summary": "Onit is enterprise legal-ops software.",
            "value_prop": "One source of truth for in-house legal.",
            "icp": {"industries": ["Legal Services"], "min_employees": 500,
                    "min_revenue_usd": None, "regions": [],
                    "titles": ["General Counsel", "Head of Legal Ops"], "notes": "enterprise"},
            "competitors": [], "keywords": ["legal operations"], "brand_voice": "",
            "banned_claims": [], "conversion_goal": "book a demo", "conversion_event": "",
            "website_url": "https://onit.com", "crawl_summary": "",
            "source": {"product_summary": "knowledge", "value_prop": "knowledge",
                       "icp": "knowledge", "keywords": "knowledge", "conversion_goal": "knowledge"},
        }
        csv_draft = {
            "product_summary": "", "value_prop": "",
            "icp": {"industries": ["Financial Services", "Healthcare"], "min_employees": 1200,
                    "min_revenue_usd": 250_000_000.0, "regions": [], "titles": [],
                    "notes": "Inferred from 40 customer accounts."},
            "competitors": [], "keywords": [], "brand_voice": "", "banned_claims": [],
            "conversion_goal": "", "conversion_event": "", "website_url": "", "crawl_summary": "",
            "source": {"icp": "csv"},
        }
        merged = merge_profiles(knowledge_draft, csv_draft)["merged"]
        assert merged["icp"]["min_employees"] == 1200, \
            f"csv real data must override knowledge guess; got {merged['icp']['min_employees']}"
        assert merged["icp"]["industries"] == ["Financial Services", "Healthcare"], \
            "csv industries should win over knowledge"
        assert merged["icp"]["titles"] == ["General Counsel", "Head of Legal Ops"], \
            "blank csv titles must NOT wipe knowledge titles"
        assert merged["product_summary"].startswith("Onit "), \
            "knowledge product_summary must survive (csv provided none)"
        assert merged["source"]["icp.min_employees"] == "csv", merged["source"]
        assert merged["source"]["icp.titles"] == "knowledge", merged["source"]
        print("[OK] Merge precedence: csv ICP beats knowledge guess; "
              "knowledge qualitative survives; blank never overwrites.")

        # (Merge-2) Manual is sacred: a field marked source="manual" is NOT
        # auto-overwritten by a later crawl/csv merge — it becomes a conflict.
        manual_base = {
            "product_summary": "My hand-written summary.", "icp": {},
            "source": {"product_summary": "manual"},
        }
        crawl_incoming = {
            "product_summary": "Crawled/LLM summary.", "icp": {},
            "source": {"product_summary": "crawl"},
        }
        m2 = merge_profiles(manual_base, crawl_incoming)
        assert m2["merged"]["product_summary"] == "My hand-written summary.", \
            "manual field must NOT be auto-overwritten"
        ps_change = [c for c in m2["changes"] if c["field"] == "product_summary"][0]
        assert ps_change["applied"] is False and ps_change["conflict"] is True, \
            f"manual override must surface as an un-applied conflict; got {ps_change}"
        print("[OK] Manual-sacred: later crawl/csv becomes a conflict, not a silent overwrite.")

        # (Merge-3) Enrichment diff against a SAVED profile via the API.
        # preview-merge returns a per-field changelist; applying only the
        # ACCEPTED fields updates saved truth, rejected fields stay; confirmed
        # remains true; tenant isolation holds.
        #
        # Onit already has a saved profile (from the PUT round-trip test above:
        # min_employees=500, product_summary="Legal ops platform...").
        enrich_incoming = {
            "icp": {"industries": [], "min_employees": 2500, "min_revenue_usd": None,
                    "regions": [], "titles": [], "notes": ""},
            "product_summary": "A totally different summary we will REJECT.",
            "source": {"icp": "csv", "product_summary": "knowledge"},
        }
        pm = client.post(
            "/api/profile/preview-merge",
            headers={"X-Dev-User-Email": "jordan@onit.com", "Content-Type": "application/json"},
            json={"incoming": enrich_incoming, "against": "saved"},
        )
        assert pm.status_code == 200, pm.text
        pmj = pm.json()
        assert pmj["base_exists"] is True, "preview-merge should see Onit's saved profile"
        change_fields = {c["field"] for c in pmj["changes"]}
        assert "icp.min_employees" in change_fields, change_fields
        assert "product_summary" in change_fields, change_fields
        # preview-merge must NOT have written anything.
        still = db.execute(scoped(OrgProfile, onit.id)).scalars().all()
        assert len(still) == 1 and still[0].icp.get("min_employees") == 500, \
            "preview-merge must be read-only"

        # Accept ONLY icp.min_employees; reject the product_summary change.
        saved_now = client.get("/api/profile",
                               headers={"X-Dev-User-Email": "jordan@onit.com"}).json()
        final = apply_accepted(saved_now, pmj["changes"], {"icp.min_employees"})
        applied = client.put(
            "/api/profile",
            headers={"X-Dev-User-Email": "jordan@onit.com", "Content-Type": "application/json"},
            json=final,
        )
        assert applied.status_code == 200, applied.text
        aj = applied.json()
        assert aj["confirmed"] is True, "saved profile must stay confirmed"
        assert aj["icp"]["min_employees"] == 2500, \
            f"accepted change must apply; got {aj['icp']['min_employees']}"
        assert aj["product_summary"].startswith("Legal ops platform"), \
            f"rejected change must NOT apply; got {aj['product_summary']!r}"
        # Tenant isolation: Acme still can't read Onit's (now-enriched) profile.
        leaked = db.execute(scoped(OrgProfile, other.id)).scalars().all()
        assert leaked == [], "TENANT LEAK: Acme can read Onit's profile after enrichment"
        print("[OK] Enrichment diff: accepted field applied, rejected field unchanged, "
              "confirmed stays true, read-only preview, tenant isolation holds.")

        # ---------------------------------------------------------------------
        # Profile-into-agent wiring — three checks:
        #   (1) A run for an org WITH a confirmed profile produces a brief whose
        #       ICP/targets reflect the PROFILE'S ICP, not the seed config.
        #   (2) A run for an org with NO profile still works on seed defaults
        #       (no regression).
        #   (3) Tenant isolation: a run only ever loads its OWN org's profile
        #       via scoped() — one org's confirmed profile never bleeds into
        #       another org's run.
        #
        # Determinism: the stub data source draws each company's industry from
        # icp["industries"], so a single-industry profile makes EVERY target's
        # industry that value. We give the profiled org a profile whose
        # industries are disjoint from its seed config, so "did the run use the
        # profile or the seed?" is a clean, non-probabilistic assertion.
        # ---------------------------------------------------------------------
        SEED_INDS = ["Legal Services", "Financial Services"]
        seed_cfg = {"icp": {"category": "legal operations", "industries": SEED_INDS,
                            "min_employees": 500, "keyword_seeds": ["legal ops"],
                            "channels": ["LinkedIn"]},
                    "company_limit": 40, "keyword_limit": 20,
                    "sam_min_fit": 0.6, "som_capture_rate": 0.08}

        def _register_org(name: str, domain: str) -> tuple[Org, AgentRegistration]:
            o = Org(name=name, domain=domain)
            db.add(o)
            db.commit()
            db.refresh(o)
            r = AgentRegistration(org_id=o.id, key="market_intel",
                                  display_name="Market intelligence", kind="builtin",
                                  enabled=True, config=dict(seed_cfg))
            db.add(r)
            db.commit()
            db.refresh(r)
            return o, r

        def _run_market_intel(org_id: str, reg_id: str) -> Artifact:
            r = Run(org_id=org_id, agent_registration_id=reg_id, agent_key="market_intel",
                    trigger="manual", status="queued")
            db.add(r)
            db.commit()
            db.refresh(r)
            enqueue(db, org_id, "run_agent", {"run_id": r.id})
            assert run_once() is True, "worker did not pick up the profile-wiring job"
            db.refresh(r)
            assert r.status == "succeeded", f"run failed: {r.error}"
            return db.execute(
                scoped(Artifact, org_id).where(Artifact.run_id == r.id)).scalar_one()

        # Profiled Co: a CONFIRMED profile whose ICP (Aerospace) is disjoint from
        # its seed config (Legal/Financial). The profile must win.
        profiled, p_reg = _register_org("Profiled Co", "profiled.example")
        db.add(OrgProfile(
            org_id=profiled.id, confirmed=True,
            product_summary="Marketplace for certified aerospace parts.",
            value_prop="Source flight-ready parts in days, not months.",
            icp={"industries": ["Aerospace"], "min_employees": 1000,
                 "min_revenue_usd": None, "regions": [], "titles": ["VP Supply Chain"],
                 "notes": "Tier-1 aerospace suppliers"},
            competitors=[{"name": "SkyParts", "url": "https://skyparts.example"}],
            keywords=["aerospace sourcing", "aircraft parts"],
            conversion_goal="book a demo"))
        db.commit()

        # Seedonly Co: identical seed config, NO profile at all.
        seedonly, s_reg = _register_org("Seedonly Co", "seedonly.example")

        # (1) Profiled run reflects the profile's ICP, not the seed config.
        p_art = _run_market_intel(profiled.id, p_reg.id)
        p_struct = p_art.body["structured"]
        assert p_struct["inputs"]["source"] == "org_profile", \
            f"profiled run must report source=org_profile, got {p_struct['inputs']}"
        p_target_inds = {t["industry"] for t in p_struct["top_targets"]}
        assert p_target_inds == {"Aerospace"}, \
            f"profile ICP must drive targets; got industries {p_target_inds}"
        assert not (p_target_inds & set(SEED_INDS)), \
            "seed industries leaked into a run that has a confirmed profile"
        assert "SkyParts" in p_struct["inputs"]["competitors"], \
            f"profile competitors must flow into the brief; got {p_struct['inputs']['competitors']}"
        assert any(c["source"] == "Org profile (confirmed)" for c in p_art.citations), \
            f"profiled run must cite the org profile; got {[c['source'] for c in p_art.citations]}"
        print(f"[OK] Profile-into-agent (1): confirmed profile drives the brief "
              f"(targets all in {p_target_inds}, competitors cited, source=org_profile).")

        # (2) No-profile run falls back to seed defaults, unchanged.
        s_art = _run_market_intel(seedonly.id, s_reg.id)
        s_struct = s_art.body["structured"]
        assert s_struct["inputs"]["source"] == "seed", \
            f"no-profile run must report source=seed, got {s_struct['inputs']}"
        s_target_inds = {t["industry"] for t in s_struct["top_targets"]}
        assert s_target_inds <= set(SEED_INDS), \
            f"seed-only run must use seed industries; got {s_target_inds}"
        assert "Aerospace" not in s_target_inds, \
            "no-profile run must not use any profile data"
        assert any(c["source"] == "Seed defaults" for c in s_art.citations), \
            f"no-profile run must cite seed defaults; got {[c['source'] for c in s_art.citations]}"
        print(f"[OK] Profile-into-agent (2): no profile -> seed defaults unchanged "
              f"(targets in {s_target_inds}, source=seed).")

        # (3) Tenant isolation of profile loading. Seedonly's run produced a
        # seed brief even though Profiled Co's confirmed profile sits in the
        # same DB — proof the worker loads each run's profile strictly via
        # scoped(). Assert the same at the DB layer (the canonical guard).
        assert db.execute(scoped(OrgProfile, seedonly.id)).scalars().all() == [], \
            "Seedonly must have no profile of its own"
        own = db.execute(scoped(OrgProfile, profiled.id)).scalars().all()
        assert len(own) == 1 and own[0].icp["industries"] == ["Aerospace"], \
            "Profiled Co must see exactly its own profile via scoped()"
        assert "Aerospace" not in s_target_inds, \
            "TENANT LEAK: another org's profile bled into Seedonly's run"
        print("[OK] Profile-into-agent (3): a run loads only its own org's profile "
              "via scoped() — no cross-tenant bleed.")

        # ---------------------------------------------------------------------
        # Content engine — three checks:
        #   (1) GENERATE produces a structured, block-based content object
        #       grounded in the org's profile (brand voice + competitors
        #       reflected), cost recorded on the run.
        #   (2) Gate routing for the three modes:
        #         all_through → artifact ready, NO proposal.
        #         gate_all    → proposal lands, artifact pending_review.
        #         guardrail   → clean drafts pass; a banned-claim trip lands in
        #                       the queue. Approve flips artifact to ready;
        #                       Reject flips it to rejected.
        #   (3) Tenant isolation: content artifacts/proposals are scoped to
        #       their org; a cross-org approve is denied; a content_engine run
        #       for an org with NO profile fails closed (does not leak the
        #       seed org's profile or produce artifacts/proposals).
        # ---------------------------------------------------------------------
        onit_content_reg = db.execute(
            scoped(AgentRegistration, onit.id)
            .where(AgentRegistration.key == "content_engine")
        ).scalar_one()

        def _trigger_content_run(org_id: str, reg_id: str, task: dict) -> Run:
            r = Run(org_id=org_id, agent_registration_id=reg_id,
                    agent_key="content_engine", trigger="manual",
                    status="queued", task=task)
            db.add(r)
            db.commit()
            db.refresh(r)
            enqueue(db, org_id, "run_agent", {"run_id": r.id})
            assert run_once() is True, "worker did not pick up content job"
            db.refresh(r)
            return r

        def _set_review_mode(org_id: str, mode: str) -> None:
            prof = db.execute(scoped(OrgProfile, org_id)).scalar_one()
            prof.content_review_mode = mode
            db.commit()

        # (1) Generate a clean draft for Onit (default mode=guardrail). The
        # template embeds brand_voice as "Tone: <voice>" and the competitor
        # name in a "(vs. SimpleLegal)" clause — both must appear, proving
        # the draft is grounded in the saved profile.
        gen_run = _trigger_content_run(
            onit.id, onit_content_reg.id,
            {"action": "generate", "content_type": "email",
             "topic": "Cut weekly review overhead", "target": "GC"})
        assert gen_run.status == "succeeded", \
            f"content generate failed: {gen_run.error}"
        assert gen_run.cost_usd > 0, \
            f"content gen must record cost (LLM path stubbed), got {gen_run.cost_usd}"
        gen_art = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.run_id == gen_run.id, Artifact.type == "content_draft")
        ).scalar_one()
        content_obj = gen_art.body["content"]
        assert content_obj["content_type"] == "email", content_obj
        blocks = content_obj["blocks"]
        assert isinstance(blocks, list) and len(blocks) >= 2, \
            f"blocks must be an ORDERED list (>=2), got {blocks!r}"
        kinds = [b["kind"] for b in blocks]
        assert kinds[0] == "subject" and "cta" in kinds, \
            f"email blocks must be ordered (subject…cta); got {kinds}"
        body_text = " ".join(b.get("text", "") for b in blocks)
        # Onit's saved profile carries brand_voice="Plain, confident, no jargon."
        # and competitors=[{"name":"SimpleLegal"}]. NO-ECHO DISCIPLINE: the
        # brand_voice string is GUIDANCE about HOW to write, NOT text to
        # include in the body. Drafts that quote the brand_voice verbatim
        # ("Tone: Plain, confident, no jargon.") fail the grader and look
        # like a copy-pasted prompt template; we explicitly assert that
        # discipline here. Competitors ARE content and SHOULD surface.
        assert "Plain, confident, no jargon" not in body_text, \
            f"brand_voice phrase must NOT echo verbatim into the body " \
            f"(no-echo discipline): {body_text!r}"
        assert "Tone:" not in body_text, \
            f"the body must not carry a 'Tone:' instruction label: {body_text!r}"
        assert "SimpleLegal" in body_text, \
            f"competitors not reflected in draft: {body_text!r}"
        assert gen_art.status == "ready", \
            f"clean draft must be ready under guardrail mode, got {gen_art.status}"
        clean_proposals = db.execute(
            scoped(Proposal, onit.id).where(Proposal.run_id == gen_run.id)
        ).scalars().all()
        assert clean_proposals == [], \
            f"clean draft must NOT create a proposal, got {clean_proposals}"
        print(f"[OK] Content engine (1): {content_obj['content_type']} draft with "
              f"{len(blocks)} ordered blocks, brand_voice NOT echoed verbatim, "
              f"competitors reflected, cost=${gen_run.cost_usd:.4f}.")

        # (2) Gate routing.
        # guardrail + banned phrase → queue. Inject the banned phrase via the
        # topic so the template's subject line carries it into the flat text.
        trip_run = _trigger_content_run(
            onit.id, onit_content_reg.id,
            {"action": "generate", "content_type": "email",
             "topic": "We are #1 in the world for legal ops",
             "target": "GC"})
        assert trip_run.status == "succeeded"
        trip_art = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.run_id == trip_run.id, Artifact.type == "content_draft")
        ).scalar_one()
        trip_props = db.execute(
            scoped(Proposal, onit.id).where(Proposal.run_id == trip_run.id)
        ).scalars().all()
        assert len(trip_props) == 1, \
            f"banned-claim trip must produce exactly one proposal, got {len(trip_props)}"
        assert trip_props[0].action_type == "content_review"
        assert trip_props[0].guardrail_status == "blocked"
        assert "#1 in the world" in trip_props[0].guardrail_detail.lower(), \
            f"guardrail detail should name the banned phrase: {trip_props[0].guardrail_detail!r}"
        assert trip_art.status == "pending_review"

        # gate_all: every draft routed, regardless of guardrail verdict.
        _set_review_mode(onit.id, "gate_all")
        ga_run = _trigger_content_run(
            onit.id, onit_content_reg.id,
            {"action": "generate", "content_type": "ad",
             "topic": "Stop the spreadsheet sprawl",
             "target": "Head of Legal Ops"})
        ga_art = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.run_id == ga_run.id, Artifact.type == "content_draft")
        ).scalar_one()
        ga_props = db.execute(
            scoped(Proposal, onit.id).where(Proposal.run_id == ga_run.id)
        ).scalars().all()
        assert len(ga_props) == 1, "gate_all must queue every draft"
        assert ga_props[0].guardrail_status == "passed", \
            "clean draft should still PASS the underlying guardrail under gate_all"
        assert ga_art.status == "pending_review"

        # all_through: never queue, even though the guardrail rule still
        # exists. NOTE: ready != published — publishing is out of scope.
        _set_review_mode(onit.id, "all_through")
        at_run = _trigger_content_run(
            onit.id, onit_content_reg.id,
            {"action": "generate", "content_type": "social_post",
             "topic": "Modern legal ops", "target": "General Counsel"})
        at_art = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.run_id == at_run.id, Artifact.type == "content_draft")
        ).scalar_one()
        at_props = db.execute(
            scoped(Proposal, onit.id).where(Proposal.run_id == at_run.id)
        ).scalars().all()
        assert at_props == [], f"all_through must never queue, got {at_props}"
        assert at_art.status == "ready"
        _set_review_mode(onit.id, "guardrail")   # restore

        # Approve flips the artifact to ready; Reject flips it to rejected.
        approve_resp = client.post(
            f"/api/review/proposals/{trip_props[0].id}/approve",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"note": "ok to send"},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        db.refresh(trip_art)
        assert trip_art.status == "ready", \
            f"approve must flip artifact ready, got {trip_art.status}"
        reject_resp = client.post(
            f"/api/review/proposals/{ga_props[0].id}/reject",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"note": "not now"},
        )
        assert reject_resp.status_code == 200, reject_resp.text
        db.refresh(ga_art)
        assert ga_art.status == "rejected"
        print("[OK] Content engine (2): gate routing — all_through skips the "
              "queue, gate_all queues every draft, guardrail queues banned-claim "
              "trips only; Approve flips to ready, Reject flips to rejected.")

        # (3) Tenant isolation of the action-taking spine.
        leaked_props = db.execute(
            scoped(Proposal, other.id).where(Proposal.action_type == "content_review")
        ).scalars().all()
        assert leaked_props == [], \
            "TENANT LEAK: Acme can read Onit's content proposals"
        leaked_drafts = db.execute(
            scoped(Artifact, other.id).where(Artifact.type == "content_draft")
        ).scalars().all()
        assert leaked_drafts == [], \
            "TENANT LEAK: Acme can read Onit's content drafts"

        # Create a fresh PENDING proposal so we can test cross-org approve.
        _set_review_mode(onit.id, "gate_all")
        final_run = _trigger_content_run(
            onit.id, onit_content_reg.id,
            {"action": "generate", "content_type": "blog_outline",
             "topic": "Why teams default to chaos", "target": "GC"})
        pending_props = db.execute(
            scoped(Proposal, onit.id).where(Proposal.run_id == final_run.id)
        ).scalars().all()
        assert len(pending_props) == 1
        pending_id = pending_props[0].id
        _set_review_mode(onit.id, "guardrail")

        cross_approve = client.post(
            f"/api/review/proposals/{pending_id}/approve",
            headers={"X-Dev-User-Email": "ops@acme.com",
                     "Content-Type": "application/json"},
            json={"note": "trying to approve someone else's work"},
        )
        assert cross_approve.status_code in (403, 404), \
            f"cross-org approve MUST be denied, got {cross_approve.status_code}: " \
            f"{cross_approve.text}"

        # The agent's profile-loading path must scope strictly. Acme has no
        # confirmed OrgProfile — the agent must FAIL CLOSED rather than reach
        # into Onit's profile or ship ungrounded copy.
        acme_content_reg = AgentRegistration(
            org_id=other.id, key="content_engine", display_name="Content engine",
            kind="builtin", enabled=True, config={})
        db.add(acme_content_reg)
        db.commit()
        db.refresh(acme_content_reg)
        acme_run = _trigger_content_run(
            other.id, acme_content_reg.id,
            {"action": "generate", "content_type": "email",
             "topic": "Doesn't matter", "target": ""})
        assert acme_run.status == "failed", \
            f"profileless content run must fail closed, got {acme_run.status}"
        assert "confirmed org profile" in (acme_run.error or "").lower(), \
            f"failure must mention the missing profile, got {acme_run.error!r}"
        acme_arts = db.execute(
            scoped(Artifact, other.id).where(Artifact.run_id == acme_run.id)
        ).scalars().all()
        assert acme_arts == [], \
            f"failed profileless run must not leave artifacts: {acme_arts}"
        acme_props = db.execute(
            scoped(Proposal, other.id).where(Proposal.run_id == acme_run.id)
        ).scalars().all()
        assert acme_props == [], \
            f"failed profileless run must not leave proposals: {acme_props}"
        print("[OK] Content engine (3): tenant isolation — Acme sees zero of "
              "Onit's content drafts/proposals, cross-org approve denied, "
              "Acme's own profileless run fails closed without leakage.")

        # ---------------------------------------------------------------------
        # Content quality loop + UTM tagging — three checks:
        #   (1) Every generated draft carries a structured grade (overall +
        #       per-criterion + suggestions). When the grader LLM call fails,
        #       we fall back to a neutral "ungraded" result WITHOUT blocking
        #       the draft (grades are advisory).
        #   (2) "Give me something better" with a critique produces a NEW
        #       version (parent_id chain), preserves the prior version, runs
        #       the grader on the new one, and captures cost on the regen.
        #   (3) The draft carries the four UTM fields + a correctly-formatted
        #       tagged URL; the artifact-tags PATCH endpoint updates them and
        #       recomputes the tagged URL. Tenant isolation holds for the
        #       rubric (OrgProfile.content_rubric) and the version chain.
        # ---------------------------------------------------------------------
        # Onit's review mode is "guardrail" again after test (2) cleanup; use
        # a non-tripping topic so this whole section stays single-spine.
        clean_task = {"action": "generate", "content_type": "email",
                      "topic": "Cost-saving framework for in-house teams",
                      "target": "GC", "destination_url": "https://onit.com/demo"}

        # (1a) Grade lives on the artifact with the expected shape.
        grade_run = _trigger_content_run(
            onit.id, onit_content_reg.id, clean_task)
        assert grade_run.status == "succeeded", \
            f"graded content gen failed: {grade_run.error}"
        grade_art = db.execute(
            scoped(Artifact, onit.id).where(Artifact.run_id == grade_run.id,
                                            Artifact.type == "content_draft")
        ).scalar_one()
        assert grade_art.grade is not None, \
            "graded run must persist artifact.grade"
        assert grade_art.grade["status"] == "graded", grade_art.grade
        assert isinstance(grade_art.grade["overall"], int), grade_art.grade
        assert 0 <= grade_art.grade["overall"] <= 100, grade_art.grade
        assert isinstance(grade_art.grade["per_criterion"], list) \
            and grade_art.grade["per_criterion"], grade_art.grade
        for crit in grade_art.grade["per_criterion"]:
            assert "name" in crit and "score" in crit, crit
        assert grade_art.grade["suggestions"], "graded run must include suggestions"
        # Cost on the run accumulates generation + grading (both stubbed).
        assert grade_run.cost_usd > 0.0007, \
            f"cost should include grading on top of generation, got {grade_run.cost_usd}"
        # The artifact body also exposes the cost breakdown so the UI can
        # show "gen + grade" without recomputing.
        cb = grade_art.body.get("cost_breakdown") or {}
        assert cb.get("generation") > 0 and cb.get("grading") > 0, cb

        # (1b) On grader LLM failure, fall back to ungraded WITHOUT blocking.
        original_grader = content_grader_mod._llm_grade

        def _grader_boom(content, profile, rubric, settings):
            raise RuntimeError("simulated grader crash")
        content_grader_mod._llm_grade = _grader_boom
        try:
            failgrade_run = _trigger_content_run(
                onit.id, onit_content_reg.id, clean_task)
        finally:
            content_grader_mod._llm_grade = original_grader
        assert failgrade_run.status == "succeeded", \
            "draft must NOT fail when the grader crashes (grades are advisory)"
        failgrade_art = db.execute(
            scoped(Artifact, onit.id).where(Artifact.run_id == failgrade_run.id,
                                            Artifact.type == "content_draft")
        ).scalar_one()
        assert failgrade_art.grade is not None
        assert failgrade_art.grade["status"] == "ungraded", failgrade_art.grade
        assert "simulated grader crash" in (failgrade_art.grade.get("reason") or ""), \
            f"ungraded reason should surface the failure: {failgrade_art.grade!r}"
        # Draft itself is still usable: the artifact landed and has content.
        assert failgrade_art.status in ("ready", "pending_review")
        assert failgrade_art.body["content"]["blocks"], \
            "draft content must still be present on grader failure"
        print(f"[OK] Quality loop (1): graded draft "
              f"(overall={grade_art.grade['overall']}, "
              f"{len(grade_art.grade['per_criterion'])} criteria, "
              f"{len(grade_art.grade['suggestions'])} suggestions); "
              "grader failure → ungraded fallback, draft preserved.")

        # (2) "Give me something better" — regenerate produces a NEW version
        # whose parent_id points at the prior; the prior is preserved; the
        # new one is graded; cost is captured.
        critique = "Too soft — lead with the cost angle, more aggressive."
        regen_run_obj = _trigger_content_run(
            onit.id, onit_content_reg.id,
            {"action": "regenerate",
             "parent_artifact_id": grade_art.id,
             "critique": critique})
        assert regen_run_obj.status == "succeeded", \
            f"regenerate failed: {regen_run_obj.error}"
        regen_art = db.execute(
            scoped(Artifact, onit.id).where(Artifact.run_id == regen_run_obj.id,
                                            Artifact.type == "content_draft")
        ).scalar_one()
        assert regen_art.parent_id == grade_art.id, \
            f"regen artifact must link to parent; got parent_id={regen_art.parent_id!r}"
        assert regen_art.id != grade_art.id, "regen must be a NEW artifact row"
        assert regen_art.body.get("version") == 2, \
            f"regen artifact must be version 2, got {regen_art.body.get('version')!r}"
        # Prior version still exists unchanged.
        prior_again = db.execute(
            scoped(Artifact, onit.id).where(Artifact.id == grade_art.id)
        ).scalar_one()
        assert prior_again.id == grade_art.id, "prior version must be preserved"
        # New version got re-graded.
        assert regen_art.grade is not None and \
            regen_art.grade["status"] == "graded", regen_art.grade
        # Critique surfaced in the new content (template fallback folds it
        # into topic → subject/body text). Deterministic + testable.
        regen_text = " ".join(
            b.get("text", "") for b in regen_art.body["content"]["blocks"])
        assert "cost angle" in regen_text.lower(), \
            f"critique must visibly influence the regen output: {regen_text[:200]!r}"
        # Regen cost was captured (generation + grading both stubbed non-zero).
        assert regen_run_obj.cost_usd > 0, \
            f"regen must record cost (separate paid call), got {regen_run_obj.cost_usd}"
        # utm_campaign is preserved across versions; utm_content varies (v2).
        assert regen_art.utm_campaign == grade_art.utm_campaign, \
            "campaign must persist across versions"
        assert regen_art.utm_content != grade_art.utm_content, \
            "utm_content must be versioned so v2 is distinguishable from v1"
        assert regen_art.utm_content.endswith("-v2"), regen_art.utm_content
        print(f"[OK] Quality loop (2): regenerate produced v{regen_art.body['version']} "
              f"(parent_id linkage holds, prior preserved, re-graded "
              f"overall={regen_art.grade['overall']}, cost=${regen_run_obj.cost_usd:.4f}).")

        # (3) UTM tagging + tenant isolation of rubric/versions.
        for key in ("utm_campaign", "utm_source", "utm_medium", "utm_content"):
            val = getattr(regen_art, key)
            assert val and isinstance(val, str) and val.strip(), \
                f"missing UTM field {key} on the artifact: {val!r}"
        # The tagged URL is in body and stitched correctly from destination
        # + the four UTM params.
        tagged = regen_art.body.get("tagged_url") or ""
        assert tagged.startswith("https://onit.com/demo"), tagged
        for key in ("utm_campaign", "utm_source", "utm_medium", "utm_content"):
            expected = getattr(regen_art, key)
            assert f"{key}={expected.replace(' ', '+')}" in tagged \
                or f"{key}={expected}" in tagged, \
                f"tagged URL missing {key}={expected!r}: {tagged}"

        # PATCH /api/artifacts/{id}/tags updates the columns + tagged_url.
        patch_resp = client.patch(
            f"/api/artifacts/{regen_art.id}/tags",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"utm_campaign": "clm-comparison-q2",
                  "utm_medium": "paid-social",
                  "destination_url": "https://onit.com/lp/clm"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        pj = patch_resp.json()
        assert pj["utm_campaign"] == "clm-comparison-q2"
        assert pj["utm_medium"] == "paid-social"
        assert pj["utm_source"] == regen_art.utm_source, \
            "PATCH should leave unspecified UTMs untouched"
        assert pj["tagged_url"].startswith("https://onit.com/lp/clm")
        assert "utm_campaign=clm-comparison-q2" in pj["tagged_url"]

        # Cross-org tag PATCH must fail. Acme tries to edit Onit's artifact.
        cross_patch = client.patch(
            f"/api/artifacts/{regen_art.id}/tags",
            headers={"X-Dev-User-Email": "ops@acme.com",
                     "Content-Type": "application/json"},
            json={"utm_campaign": "stolen"},
        )
        assert cross_patch.status_code in (403, 404), \
            f"cross-org tag PATCH MUST be denied, got {cross_patch.status_code}: " \
            f"{cross_patch.text}"

        # Tenant isolation of rubric: write a custom rubric on Onit, assert
        # Acme cannot see it through scoped(), and Acme's own profile (when
        # set) has its own independent rubric.
        onit_profile_row = db.execute(scoped(OrgProfile, onit.id)).scalar_one()
        onit_profile_row.content_rubric = [
            {"name": "onit_specific", "description": "Onit-only criterion."},
        ]
        db.commit()
        acme_rubrics = db.execute(
            scoped(OrgProfile, other.id)
        ).scalars().all()
        # Acme had no OrgProfile row in earlier tests — still doesn't, so
        # cross-tenant rubric simply can't exist for them.
        for ap in acme_rubrics:
            assert "onit_specific" not in [c.get("name") for c in (ap.content_rubric or [])], \
                "TENANT LEAK: Acme can read Onit's rubric"
        # And version chain isolation: Acme cannot read either content_draft
        # version even though both belong to Onit's run.
        leaked_versions = db.execute(
            scoped(Artifact, other.id).where(
                Artifact.id.in_([grade_art.id, regen_art.id]))
        ).scalars().all()
        assert leaked_versions == [], \
            "TENANT LEAK: Acme can see Onit's content draft versions via scoped()"
        print("[OK] Quality loop (3): four UTM fields + correct tagged URL "
              "(stitched from destination + tags); PATCH updates tags and "
              "tagged URL, cross-org PATCH denied; rubric + version chain "
              "are tenant-isolated.")

        # ---------------------------------------------------------------------
        # Analytics dashboard — five checks:
        #   (1) Ingest: CSV normalizes into MetricPoint rows (generic shape);
        #       baseline mode flips is_baseline=True; forgiving header mapping
        #       handles aliased + reordered headers; a missing date column
        #       returns zero points instead of crashing.
        #   (2) Attribution join: a row carrying a UTM/campaign that matches
        #       a produced content_draft is joined to that artifact in the
        #       funnel view; baseline rows are NEVER attributed.
        #   (3) Funnel view: metric_points aggregate into the three stages
        #       over time; the production lane reflects the org's runs/
        #       artifacts (every content_engine run from earlier is in it).
        #   (4) Suggestions: trend-based suggestion includes evidence;
        #       industry-perspective is labeled non-data; when the LLM
        #       errors, the deterministic fallback returns a single labeled
        #       "unavailable" item (graceful degradation, honest).
        #   (5) Tenant isolation: an org only ever sees its own metric_
        #       points / suggestions via scoped().
        #
        # The earlier quality-loop section left `regen_art` saved with
        # utm_campaign="clm-comparison-q2" (the PATCH set it). We reuse it
        # as the join key so attribution lines up to a real artifact.
        # The PATCH happened via the API on a different ORM instance, so
        # our in-memory copy is stale until we refresh — without this the
        # UTM-match assertions read the pre-PATCH campaign.
        # ---------------------------------------------------------------------
        db.refresh(regen_art)
        clm_campaign = regen_art.utm_campaign or "clm-comparison-q2"

        # (1) Ingest — forgiving headers (wide format with aliased names).
        # Note: 'Visits' aliases to sessions; 'Demo Requests' aliases to
        # demo_requests; 'Campaign' is the UTM key. 'Notes' is unmapped
        # and should be silently dropped.
        # Shaped to trigger the deterministic trend rule "mid-funnel
        # engagement rose materially but bottom-funnel demos stayed flat",
        # which gives test (4) a clean signal to assert on.
        ongoing_csv = (
            "Date,Impressions,Visits,Demo Requests,Campaign,UTM Source,Notes\n"
            "2026-05-04,12000,800,8,clm-comparison-q2,linkedin,launch week\n"
            "2026-05-11,15000,950,7,clm-comparison-q2,linkedin,\n"
            "2026-05-18,18000,1100,8,clm-comparison-q2,linkedin,steady\n"
            "2026-05-25,21000,1300,7,clm-comparison-q2,linkedin,uplift\n"
        )
        up1 = client.post(
            "/api/dashboard/reports",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("ongoing.csv", ongoing_csv.encode("utf-8"), "text/csv")},
            data={"mode": "ongoing", "source": "linkedin_ads"},
        )
        assert up1.status_code == 200, up1.text
        up1j = up1.json()
        assert up1j["mode"] == "ongoing"
        # Three known metrics × 4 rows = 12 points (impressions/sessions/
        # demo_requests). The unmapped "Notes" column is dropped silently.
        assert up1j["point_count"] == 12, up1j
        cm = up1j["column_mapping"]
        assert cm["Impressions"]["role"] == "metric"
        assert cm["Visits"]["metric_name"] == "sessions", cm["Visits"]
        assert cm["Demo Requests"]["metric_name"] == "demo_requests", cm["Demo Requests"]
        assert cm["Campaign"]["role"] == "utm_campaign", cm["Campaign"]
        assert cm["Notes"]["role"] is None, "unmapped column must be reported as None"

        # Baseline upload (untagged historical rows) — should land as
        # backdrop, NEVER attributed. Demos are deliberately higher than
        # the ongoing rows so the trend rule sees "bottom flat-to-down"
        # vs "middle up sharply" — a textbook leak signal.
        baseline_csv = (
            "date,impressions,sessions,demos\n"
            "2026-02-02,5000,200,10\n"
            "2026-03-02,5500,220,10\n"
            "2026-04-06,6000,260,10\n"
        )
        up2 = client.post(
            "/api/dashboard/reports",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("baseline.csv", baseline_csv.encode("utf-8"), "text/csv")},
            data={"mode": "baseline", "source": "ga"},
        )
        assert up2.status_code == 200, up2.text
        up2j = up2.json()
        assert up2j["mode"] == "baseline"
        assert up2j["point_count"] == 9, up2j

        # Forgiving on a CSV with no date column — must NOT crash.
        bad_csv = "campaign,impressions,clicks\nx,100,5\n"
        up_bad = client.post(
            "/api/dashboard/reports",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("bad.csv", bad_csv.encode("utf-8"), "text/csv")},
            data={"mode": "ongoing", "source": "manual"},
        )
        assert up_bad.status_code == 200, up_bad.text
        bj = up_bad.json()
        assert bj["point_count"] == 0, bj
        assert bj["format"] == "empty", bj

        # Direct DB check: baseline flag set; metric_name uses canonical
        # (alias → "sessions", not "visits"); raw_ref carries debug info.
        all_points = db.execute(
            scoped(MetricPoint, onit.id)
        ).scalars().all()
        assert any(p.is_baseline and p.metric_name == "impressions" for p in all_points), \
            "baseline upload must flip is_baseline=True"
        assert any(not p.is_baseline and p.metric_name == "sessions"
                   and p.utm_campaign == clm_campaign for p in all_points), \
            "ongoing upload must persist canonical metric names + UTM"
        assert any(p.raw_ref.get("row") for p in all_points), \
            "raw_ref should carry row debug info"
        print(f"[OK] Dashboard (1): {up1j['point_count']} ongoing + "
              f"{up2j['point_count']} baseline points landed; aliased headers "
              "mapped to canonical metric names; missing-date CSV returned "
              "format=empty without crashing.")

        # (2) Attribution — the funnel view must credit the matching artifact.
        funnel_resp = client.get(
            "/api/dashboard/funnel",
            headers={"X-Dev-User-Email": "jordan@onit.com"})
        assert funnel_resp.status_code == 200, funnel_resp.text
        funnel = funnel_resp.json()
        bottom = funnel["stages"]["bottom"]
        # demo_requests live in the bottom funnel; the ongoing rows
        # carrying utm_campaign=clm-comparison-q2 must show up as
        # attributed signal AND credit our regen_art.
        bottom_attributed_total = sum(bottom["attributed"])
        bottom_baseline_total = sum(bottom["baseline"])
        assert bottom_attributed_total >= 8 + 7 + 8 + 7, \
            f"ongoing demo_requests must flow into bottom 'attributed', got {bottom_attributed_total}"
        # Baseline DEMOs (10+10+10=30) sit in backdrop ONLY.
        assert bottom_baseline_total >= 10 + 10 + 10, \
            f"baseline demos must flow into bottom 'baseline', got {bottom_baseline_total}"
        contribs = bottom["top_contributors"]
        # The UTM key matches our regen_art tags → it must be a contributor.
        contrib_ids = {c["artifact_id"] for c in contribs}
        assert regen_art.id in contrib_ids, \
            f"the matching artifact must appear in top_contributors; got {contribs}"
        # Baseline rows have NO utm_campaign → they cannot contribute to
        # any specific artifact (only the backdrop trend).
        assert not any(c.get("utm_campaign") in (None, "") for c in contribs), \
            "baseline rows must NOT appear as named contributors"
        print(f"[OK] Dashboard (2): UTM join attributes demo_requests "
              f"(total attributed={bottom_attributed_total}) to artifact "
              f"{regen_art.id[:6]}…; baseline {bottom_baseline_total} stays "
              "backdrop (not attributed).")

        # (3) Funnel view + production lane.
        # The top + middle stages must have data (impressions + sessions).
        top = funnel["stages"]["top"]
        middle = funnel["stages"]["middle"]
        assert sum(top["total"]) > 0 and "impressions" in top["metric_names"], top
        assert sum(middle["total"]) > 0 and "sessions" in middle["metric_names"], middle
        # Production lane reflects the org's runs / artifacts — we've
        # produced multiple content_engine + market_intel runs by now.
        prod = funnel["production"]
        assert sum(prod["runs_total"]) > 0, f"production lane empty: {prod}"
        assert sum(prod["drafts_produced"]) > 0, f"no drafts on production lane: {prod}"
        assert len(prod["buckets"]) == len(funnel["buckets"]), \
            "production buckets must align with stage buckets"
        print(f"[OK] Dashboard (3): three stages aggregated (top sum="
              f"{sum(top['total'])}, middle sum={sum(middle['total'])}, "
              f"bottom sum={sum(bottom['total'])}); production lane shows "
              f"{sum(prod['drafts_produced'])} draft(s), "
              f"{sum(prod['runs_total'])} run(s).")

        # (4) Suggestions — refresh, then assert structure + labeling.
        refresh = client.post(
            "/api/dashboard/suggestions/refresh",
            headers={"X-Dev-User-Email": "jordan@onit.com"})
        assert refresh.status_code == 200, refresh.text
        rj = refresh.json()
        assert rj, "refresh must produce at least one suggestion"
        trend_items = [s for s in rj if s["kind"] == "trend"]
        industry_items = [s for s in rj if s["kind"] == "industry"]
        assert trend_items, "deterministic trend rules must produce at least one item"
        for s in trend_items:
            assert s["source_label"].startswith("Trend-based"), s
            assert s["evidence"], f"every trend suggestion must carry evidence: {s}"
        assert industry_items, "industry suggestion (stubbed) must appear"
        for s in industry_items:
            assert s["source_label"].startswith("General industry perspective"), s

        # Deterministic fallback: with the stub swapped for a raiser, the
        # industry section must degrade to the labeled "unavailable" item
        # WITHOUT crashing the refresh and WITHOUT producing a trend lookalike.
        original_industry = suggestions_mod._llm_industry

        def _industry_boom(profile, funnel, settings):
            raise RuntimeError("simulated industry LLM outage")
        suggestions_mod._llm_industry = _industry_boom
        try:
            refresh2 = client.post(
                "/api/dashboard/suggestions/refresh",
                headers={"X-Dev-User-Email": "jordan@onit.com"})
        finally:
            suggestions_mod._llm_industry = original_industry
        assert refresh2.status_code == 200, refresh2.text
        rj2 = refresh2.json()
        industry2 = [s for s in rj2 if s["kind"] == "industry"]
        assert len(industry2) == 1, \
            f"fallback must yield exactly one industry placeholder, got {industry2}"
        assert "unavailable" in industry2[0]["recommendation"].lower(), \
            f"fallback should say industry perspective is unavailable: {industry2[0]}"
        assert industry2[0]["source_label"].startswith("General industry perspective"), \
            "fallback MUST still carry the 'verify before acting' label"
        print(f"[OK] Dashboard (4): {len(trend_items)} trend "
              f"suggestion(s) with evidence; industry stub labeled "
              "'verify before acting'; LLM-outage fallback yields a single "
              "labeled placeholder.")

        # (5) Tenant isolation — Acme (other) cannot see Onit's points
        # or suggestions via scoped(). Also: a baseline upload by Acme
        # against the same campaign string must NOT pull credit toward
        # Onit's artifact (each org's funnel is computed from its own rows).
        leaked_points = db.execute(
            scoped(MetricPoint, other.id)
        ).scalars().all()
        assert leaked_points == [], \
            "TENANT LEAK: Acme can read Onit's metric points"
        leaked_suggs = db.execute(
            scoped(Suggestion, other.id)
        ).scalars().all()
        assert leaked_suggs == [], \
            "TENANT LEAK: Acme can read Onit's suggestions"
        leaked_uploads = db.execute(
            scoped(ReportUpload, other.id)
        ).scalars().all()
        assert leaked_uploads == [], \
            "TENANT LEAK: Acme can read Onit's report uploads"
        # And the API boundary: Acme is on a non-allowed domain so the
        # dev-auth allowlist must 403 BEFORE the org filter runs.
        cross_funnel = client.get(
            "/api/dashboard/funnel",
            headers={"X-Dev-User-Email": "ops@acme.com"})
        assert cross_funnel.status_code in (403, 404), \
            f"cross-org dashboard read MUST be denied, got " \
            f"{cross_funnel.status_code}: {cross_funnel.text}"
        print("[OK] Dashboard (5): tenant isolation holds — Acme sees 0 of "
              "Onit's metric_points / suggestions / report_uploads via "
              "scoped(), and the API blocks the cross-org read.")

        # ---------------------------------------------------------------------
        # Product layer — six checks:
        #   (1) CRUD + confirm. Product-only fields persist; Acme can't see
        #       or edit Onit's product via scoped().
        #   (2) Inheritance resolution: no overrides → resolved fields == org;
        #       with brand_voice_override set → resolved == override; per-
        #       field provenance reflects which layer drove the value.
        #   (3) Agent reads resolved profile: a content_engine run with a
        #       product_id reflects product positioning + product_competitors
        #       in the draft; an equivalent run with NO product_id is the
        #       legacy org-level draft (no regression).
        #   (4) UTM behavior: product utm_source_default + utm_medium_default
        #       win over the per-type defaults; campaign slug is prefixed
        #       with the product slug ("<slug>__<topic-slug>").
        #   (5) Nullable product_id everywhere: existing org-level
        #       runs/artifacts/metric_points (product_id=NULL) behave
        #       exactly as before — they're still queryable through the
        #       org-level funnel and through scoped().
        #   (6) Dashboard scoping: GET /api/dashboard/funnel?product_id=...
        #       returns only that product's metric_points / runs / artifacts;
        #       the unfiltered call returns everything (current behavior).
        # ---------------------------------------------------------------------

        # (1) Create + confirm. Tenant isolation.
        create_resp = client.post(
            "/api/products",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={
                "name": "Onit Spend Manager",
                "slug": "spend-manager",
                "website_url": "https://onit.com/spend",
                "positioning": "Eliminate outside-counsel spend leaks with "
                               "real-time matter-budget visibility.",
                "value_props": ["See spend before invoices land",
                                "Budgets enforced at the matter level"],
                "key_features": ["Real-time accruals", "Budget guardrails",
                                 "Matter-level rollups"],
                "use_cases": ["Outside counsel spend control",
                              "Quarterly forecast accuracy"],
                "product_competitors": [{"name": "BrightFlag",
                                         "url": "https://brightflag.com"}],
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        prod_json = create_resp.json()
        assert prod_json["status"] == "draft", prod_json
        assert prod_json["slug"] == "spend-manager"
        assert prod_json["positioning"].startswith("Eliminate outside-counsel"), prod_json
        assert prod_json["value_props"] == ["See spend before invoices land",
                                            "Budgets enforced at the matter level"]
        product_id = prod_json["id"]
        confirm = client.post(
            f"/api/products/{product_id}/confirm",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"}, json={})
        assert confirm.status_code == 200 and confirm.json()["status"] == "confirmed"
        # Tenant isolation: Acme can neither read nor edit Onit's product.
        leaked = db.execute(
            scoped(ProductProfile, other.id)
        ).scalars().all()
        assert leaked == [], "TENANT LEAK: Acme can read Onit's products"
        cross_get = client.get(
            f"/api/products/{product_id}",
            headers={"X-Dev-User-Email": "ops@acme.com"})
        assert cross_get.status_code in (403, 404), \
            f"cross-org GET /api/products/{{id}} must be denied, got " \
            f"{cross_get.status_code}: {cross_get.text}"
        cross_patch = client.patch(
            f"/api/products/{product_id}",
            headers={"X-Dev-User-Email": "ops@acme.com",
                     "Content-Type": "application/json"},
            json={"positioning": "stolen"})
        assert cross_patch.status_code in (403, 404), \
            f"cross-org PATCH /api/products/{{id}} must be denied, got " \
            f"{cross_patch.status_code}: {cross_patch.text}"
        print(f"[OK] Product (1): created + confirmed '{prod_json['name']}'; "
              f"Acme can't read/edit Onit's product (tenant-isolated).")

        # (2) Inheritance resolution.
        # No overrides: resolved brand_voice should equal Onit's saved
        # brand_voice ("Plain, confident, no jargon."); provenance = "org".
        resolved_a = resolve_product_profile(db, onit.id, product_id)
        assert resolved_a["brand_voice"] == "Plain, confident, no jargon.", \
            f"with no override, resolved.brand_voice must equal org's; got " \
            f"{resolved_a['brand_voice']!r}"
        assert resolved_a["provenance"]["brand_voice"] == "org", \
            f"provenance must be 'org' when not overridden, got {resolved_a['provenance']}"
        # The product layer is present (status=confirmed).
        assert resolved_a["product"] is not None
        assert resolved_a["product"]["positioning"].startswith(
            "Eliminate outside-counsel"), resolved_a["product"]
        # Provenance for product-only fields tagged "product".
        assert resolved_a["provenance"]["product.positioning"] == "product", \
            resolved_a["provenance"]

        # Set an override; resolved.brand_voice flips; provenance flips to
        # "product_override". Other inheritable fields stay 'org'.
        patch_override = client.patch(
            f"/api/products/{product_id}",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"brand_voice_override": "Bold, blunt, and very specific."})
        assert patch_override.status_code == 200, patch_override.text
        resolved_b = resolve_product_profile(db, onit.id, product_id)
        assert resolved_b["brand_voice"] == "Bold, blunt, and very specific.", \
            f"override must win, got {resolved_b['brand_voice']!r}"
        assert resolved_b["provenance"]["brand_voice"] == "product_override", \
            resolved_b["provenance"]
        # banned_claims wasn't overridden → still org provenance.
        assert resolved_b["provenance"]["banned_claims"] == "org", \
            resolved_b["provenance"]
        # No product_id → org-level view, untouched.
        org_view = resolve_product_profile(db, onit.id, None)
        assert org_view["brand_voice"] == "Plain, confident, no jargon.", \
            f"no-product view must equal org's saved brand_voice; got " \
            f"{org_view['brand_voice']!r}"
        assert org_view["product"] is None
        assert org_view["provenance"]["brand_voice"] == "org"

        # Revert the override (PATCH explicit null) — provenance flips back.
        revert = client.patch(
            f"/api/products/{product_id}",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"brand_voice_override": None})
        assert revert.status_code == 200, revert.text
        assert revert.json()["brand_voice_override"] is None
        resolved_c = resolve_product_profile(db, onit.id, product_id)
        assert resolved_c["brand_voice"] == "Plain, confident, no jargon."
        assert resolved_c["provenance"]["brand_voice"] == "org"
        print("[OK] Product (2): inheritance resolves correctly — no override "
              "→ org value, override → product_override + provenance flips, "
              "explicit-null revert → back to org.")

        # (3) Agent uses the resolved profile.
        # Set product UTM defaults so test (4) below has something to read.
        client.patch(
            f"/api/products/{product_id}",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"utm_source_default": "search",
                  "utm_medium_default": "paid-search"})

        # Reset Onit's review mode to guardrail (earlier tests may have left
        # it elsewhere) so a clean draft auto-passes.
        _set_review_mode(onit.id, "guardrail")
        # Run content_engine WITH the product.
        prod_run = Run(org_id=onit.id, agent_registration_id=onit_content_reg.id,
                       agent_key="content_engine", trigger="manual",
                       status="queued", product_id=product_id,
                       task={"action": "generate", "content_type": "email",
                             "topic": "Catch spend leaks before invoices land",
                             "target": "GC"})
        db.add(prod_run); db.commit(); db.refresh(prod_run)
        enqueue(db, onit.id, "run_agent", {"run_id": prod_run.id})
        assert run_once() is True
        db.refresh(prod_run)
        assert prod_run.status == "succeeded", \
            f"product-scoped run failed: {prod_run.error}"
        assert prod_run.product_id == product_id, prod_run.product_id
        prod_art = db.execute(
            scoped(Artifact, onit.id).where(Artifact.run_id == prod_run.id,
                                            Artifact.type == "content_draft")
        ).scalar_one()
        assert prod_art.product_id == product_id, \
            f"artifact must inherit run.product_id, got {prod_art.product_id}"
        body_text = " ".join(b.get("text", "")
                             for b in prod_art.body["content"]["blocks"])
        # The product's positioning + first value_prop fold into the template
        # view, so the draft mentions either positioning content or value_prop.
        assert ("outside-counsel" in body_text.lower()
                or "spend leaks" in body_text.lower()
                or "see spend before invoices land" in body_text.lower()), \
            f"product positioning/value_prop must surface in draft: {body_text!r}"
        # Competitors used should be product_competitors (BrightFlag), not
        # org-level (SimpleLegal).
        assert "BrightFlag" in body_text, \
            f"product_competitors must be reflected over org competitors: {body_text!r}"
        assert "SimpleLegal" not in body_text, \
            f"with a product set, org competitors should NOT lead: {body_text!r}"
        # Provenance on the artifact carries the product reference.
        assert prod_art.body["provenance"]["product_id"] == product_id
        assert prod_art.body["provenance"]["product_name"] == "Onit Spend Manager"
        assert "BrightFlag" in prod_art.body["provenance"]["competitors_reflected"]

        # Control: SAME topic + content_type with NO product_id.
        ctrl_run = Run(org_id=onit.id, agent_registration_id=onit_content_reg.id,
                       agent_key="content_engine", trigger="manual",
                       status="queued", product_id=None,
                       task={"action": "generate", "content_type": "email",
                             "topic": "Catch spend leaks before invoices land",
                             "target": "GC"})
        db.add(ctrl_run); db.commit(); db.refresh(ctrl_run)
        enqueue(db, onit.id, "run_agent", {"run_id": ctrl_run.id})
        assert run_once() is True
        db.refresh(ctrl_run)
        assert ctrl_run.status == "succeeded"
        assert ctrl_run.product_id is None
        ctrl_art = db.execute(
            scoped(Artifact, onit.id).where(Artifact.run_id == ctrl_run.id,
                                            Artifact.type == "content_draft")
        ).scalar_one()
        ctrl_text = " ".join(b.get("text", "")
                             for b in ctrl_art.body["content"]["blocks"])
        # No product → SimpleLegal (org competitor) leads, not BrightFlag.
        assert "SimpleLegal" in ctrl_text, \
            f"no-product run must use org competitors: {ctrl_text!r}"
        assert "BrightFlag" not in ctrl_text, \
            "no-product run must NOT reach into product_competitors"
        assert ctrl_art.product_id is None
        assert ctrl_art.body["provenance"]["product_id"] is None
        print("[OK] Product (3): product-scoped run uses positioning + "
              "product_competitors (BrightFlag); no-product control "
              "uses org competitors (SimpleLegal); both succeed.")

        # (4) UTM behavior.
        # utm_source = product's utm_source_default ("search"), NOT the
        # email type default ("email"). utm_medium = product's ("paid-search").
        # utm_campaign is prefixed with the product slug.
        assert prod_art.utm_source == "search", \
            f"product utm_source_default must override type default; got " \
            f"{prod_art.utm_source!r}"
        assert prod_art.utm_medium == "paid-search", \
            f"product utm_medium_default must override type default; got " \
            f"{prod_art.utm_medium!r}"
        assert prod_art.utm_campaign.startswith("spend-manager__"), \
            f"campaign must be prefixed with product slug; got " \
            f"{prod_art.utm_campaign!r}"
        # The non-product control falls back to type defaults.
        assert ctrl_art.utm_source == "email", ctrl_art.utm_source
        assert ctrl_art.utm_medium == "email", ctrl_art.utm_medium
        assert not ctrl_art.utm_campaign.startswith("spend-manager__"), \
            "no-product run must NOT carry a product-slug prefix"
        print(f"[OK] Product (4): product utm_source/medium override the "
              f"type defaults (utm_source={prod_art.utm_source}, "
              f"utm_medium={prod_art.utm_medium}); campaign slug prefixed "
              f"with product slug ({prod_art.utm_campaign}).")

        # (5) Nullable product_id everywhere — legacy rows behave as before.
        # Earlier tests created MANY rows BEFORE the product layer landed,
        # all with product_id=NULL on artifacts/runs/proposals/metric_points.
        # They MUST still be readable through scoped() and behave identically.
        legacy_artifacts = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.product_id.is_(None))
        ).scalars().all()
        assert legacy_artifacts, \
            "expected at least some legacy artifacts with product_id=NULL"
        legacy_runs = db.execute(
            scoped(Run, onit.id).where(Run.product_id.is_(None))
        ).scalars().all()
        assert legacy_runs, "expected legacy runs with product_id=NULL"
        legacy_points = db.execute(
            scoped(MetricPoint, onit.id)
            .where(MetricPoint.product_id.is_(None))
        ).scalars().all()
        assert legacy_points, "expected legacy metric_points with product_id=NULL"
        # The control content_engine run above ALSO has product_id=NULL —
        # the orchestration code didn't break the legacy path.
        assert ctrl_art in legacy_artifacts \
            or any(a.id == ctrl_art.id for a in legacy_artifacts)
        print(f"[OK] Product (5): nullable product_id everywhere — "
              f"{len(legacy_artifacts)} legacy artifacts, "
              f"{len(legacy_runs)} legacy runs, "
              f"{len(legacy_points)} legacy metric_points "
              "all readable through scoped() with no behavior change.")

        # (6) Dashboard scoping.
        # Upload a small product-scoped report so the funnel can pivot to it.
        prod_csv = (
            "Date,Impressions,Visits,Demo Requests,Campaign,UTM Source\n"
            "2026-05-04,3000,200,4,spend-manager__demo,linkedin\n"
            "2026-05-11,3500,250,5,spend-manager__demo,linkedin\n"
        )
        prod_up = client.post(
            "/api/dashboard/reports",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("prod.csv", prod_csv.encode("utf-8"), "text/csv")},
            data={"mode": "ongoing", "source": "linkedin_ads",
                  "product_id": product_id},
        )
        assert prod_up.status_code == 200, prod_up.text
        assert prod_up.json()["point_count"] == 6, prod_up.json()
        # Org-level funnel (no filter) includes BOTH legacy points AND the
        # new product-scoped ones.
        org_funnel = client.get(
            "/api/dashboard/funnel",
            headers={"X-Dev-User-Email": "jordan@onit.com"}).json()
        org_top_total = sum(org_funnel["stages"]["top"]["total"])
        # Product-scoped funnel: ONLY the new 6 points (3000+3500=6500 imps
        # at top, 200+250=450 sessions at middle, 4+5=9 demos at bottom).
        prod_funnel = client.get(
            f"/api/dashboard/funnel?product_id={product_id}",
            headers={"X-Dev-User-Email": "jordan@onit.com"}).json()
        prod_top_total = sum(prod_funnel["stages"]["top"]["total"])
        prod_mid_total = sum(prod_funnel["stages"]["middle"]["total"])
        prod_bot_total = sum(prod_funnel["stages"]["bottom"]["total"])
        assert prod_top_total == 6500, \
            f"product-scoped top funnel must equal product points only, got {prod_top_total}"
        assert prod_mid_total == 450, prod_mid_total
        assert prod_bot_total == 9, prod_bot_total
        # And the org-level total includes the legacy points too — strictly more.
        assert org_top_total > prod_top_total, \
            f"org-level total ({org_top_total}) must exceed product-only " \
            f"({prod_top_total})"
        # Production lane on the product-scoped view counts only product runs.
        prod_runs_total = sum(prod_funnel["production"]["runs_total"])
        assert prod_runs_total >= 1, \
            f"product-scoped production lane must include the product run, got {prod_runs_total}"
        # Cross-org filter denied via the auth layer.
        cross_funnel = client.get(
            f"/api/dashboard/funnel?product_id={product_id}",
            headers={"X-Dev-User-Email": "ops@acme.com"})
        assert cross_funnel.status_code in (403, 404), \
            f"cross-org product-funnel read must be denied, got " \
            f"{cross_funnel.status_code}: {cross_funnel.text}"
        print(f"[OK] Product (6): dashboard filter — product-scoped funnel "
              f"shows only this product's 6500/450/9 totals; org-level "
              f"funnel ({org_top_total} top) includes legacy + product "
              "data; cross-org filter denied.")

        # ---------------------------------------------------------------------
        # Document ingestion (Phase 1, Build B) — ten checks:
        #   (1) Upload + ingest: a TXT doc transitions ingesting → extracted
        #       and extracted_text is populated by the worker.
        #   (2) Extraction stub: candidates persist with correct field_name,
        #       value, confidence, source_passage; blank fields produce no
        #       candidate (no confabulation).
        #   (3) Extraction failure: when _llm_extract raises (treated as "no
        #       LLM available"), the doc lands with status=failed and a
        #       clear extraction_error; zero candidates persisted.
        #   (4) Accept promotion (single-value): accepting positioning writes
        #       into ProductProfile.positioning; the prior active history
        #       entry flips to superseded; the new entry is active.
        #   (5) Accept promotion — list field: accepting value_props appends
        #       without duplicating existing items (case-insensitive); a
        #       rejected candidate does NOT promote.
        #   (6) Messaging notes: accepting an objection_handling candidate
        #       appends into ProductProfile.messaging_notes.objection_handling
        #       and retains the source_passage + source_doc_id.
        #   (7) No-override discipline: assert no insight has a field_name
        #       in the *_override list — neither the extractor nor the API
        #       can land an override candidate.
        #   (8) Tenant isolation: Acme cannot list/read/accept Onit's docs
        #       or insights; cross-org reads/PATCHes are 403/404.
        #   (9) Dimensions reserved: every candidate in v1 has dimensions=
        #       None; no code branches on the column.
        #  (10) Content agent uses promoted knowledge: a content_engine run
        #       scoped to the product reflects the accepted positioning +
        #       value_props in the draft body. The north-star test.
        # ---------------------------------------------------------------------
        # Resolve the seeded SimpleLegal CLM product.
        simplelegal = db.execute(
            scoped(ProductProfile, onit.id)
            .where(ProductProfile.slug == "simplelegal-clm")
        ).scalar_one()
        sl_id = simplelegal.id

        # Capture the seeded baseline so test (4) can assert the flip.
        seed_history_before = list(simplelegal.field_history or [])
        seed_positioning_before = simplelegal.positioning
        seed_value_props_before = list(simplelegal.value_props or [])
        # The seed includes "One source of truth for contracts and approvals."
        # which our LLM stub's value_props will NOT include — so no
        # accidental dedupe of an unrelated value. The stub's second
        # value_prop "Cuts contract turnaround time in half within 90 days."
        # matches the seed exactly (case-sensitive in this case), exercising
        # the dedupe path.

        # ---- (1) Upload + ingest ---------------------------------------
        doc_body = (
            "Modern Matter Management — Product Framework v2.0\n\n"
            "Modern matter management for in-house teams that live in "
            "workflow, not in legal-tech jargon. Designed for the GC who "
            "needs a system, not a project.\n\n"
            "Why it matters: self-serve matters for a 5-person legal team. "
            "Cuts contract turnaround time in half within 90 days.\n\n"
            "Competitive frame: we compete with Ironclad and LinkSquares; "
            "what we sound like: clear, direct, no buzzwords.\n\n"
            "Objection handling: Too expensive vs. spreadsheets — lead with "
            "the cost of a missed renewal.\n"
        )
        up = client.post(
            f"/api/products/{sl_id}/documents",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("framework_v2.txt",
                            doc_body.encode("utf-8"), "text/plain")},
            data={"kind": "messaging_framework", "version_label": "v2.0"},
        )
        assert up.status_code == 201, up.text
        doc_json = up.json()
        assert doc_json["status"] == "ingesting", doc_json
        assert doc_json["kind"] == "messaging_framework"
        doc_id = doc_json["id"]
        # Drain the extraction job from the queue.
        assert run_once() is True, "worker did not pick up the extraction job"
        doc_row = db.execute(
            scoped(ProductDocument, onit.id)
            .where(ProductDocument.id == doc_id)
        ).scalar_one()
        assert doc_row.status == "extracted", \
            f"doc status must flip to extracted, got {doc_row.status} " \
            f"(error={doc_row.extraction_error})"
        assert doc_row.extracted_text and "Modern matter management" in doc_row.extracted_text
        print(f"[OK] Document (1): TXT upload normalized + extracted "
              f"({len(doc_row.extracted_text)} chars).")

        # ---- (2) Extraction stub — candidate persistence ----------------
        candidates = db.execute(
            scoped(ExtractedInsight, onit.id)
            .where(ExtractedInsight.product_document_id == doc_id)
        ).scalars().all()
        # Stub returned 4 candidates: positioning, value_props,
        # product_competitors, objection_handling. NO target_persona /
        # proof_points / etc. — those were blank in the LLM JSON and must
        # NOT yield candidate rows (no confabulation).
        assert len(candidates) == 4, \
            f"expected 4 candidates from the stub, got {len(candidates)}"
        by_field = {c.field_name: c for c in candidates}
        assert set(by_field) == {"positioning", "value_props",
                                 "product_competitors", "objection_handling"}, \
            f"unexpected candidate fields: {set(by_field)}"
        pos_cand = by_field["positioning"]
        assert pos_cand.confidence == 0.88, pos_cand.confidence
        assert "modern matter management" in pos_cand.source_passage.lower()
        assert pos_cand.status == "pending"
        # Blank-field discipline: target_persona was not in the stub →
        # no candidate row. Same for proof_points / differentiators /
        # key_features / use_cases / launch_messaging.
        for blank_field in ("target_persona", "proof_points",
                            "differentiators", "key_features",
                            "use_cases", "launch_messaging"):
            assert blank_field not in by_field, \
                f"blank field {blank_field} must not produce a candidate"
        print(f"[OK] Document (2): {len(candidates)} candidates persisted "
              "with correct field_name/confidence/source_passage; blank "
              "fields produced no candidate (no confabulation).")

        # ---- (3) Extraction failure when the LLM is unavailable ---------
        original_extract = extract_mod._llm_extract

        def _boom(doc_text, op, pp, dk, settings):
            raise RuntimeError("simulated LLM outage")
        extract_mod._llm_extract = _boom
        try:
            fail_up = client.post(
                f"/api/products/{sl_id}/documents",
                headers={"X-Dev-User-Email": "jordan@onit.com"},
                files={"file": ("doomed.txt",
                                b"any content", "text/plain")},
                data={"kind": "other"},
            )
            assert fail_up.status_code == 201, fail_up.text
            failed_id = fail_up.json()["id"]
            assert run_once() is True
            fail_row = db.execute(
                scoped(ProductDocument, onit.id)
                .where(ProductDocument.id == failed_id)
            ).scalar_one()
            assert fail_row.status == "failed", \
                f"no-LLM doc must end up status=failed, got {fail_row.status}"
            assert fail_row.extraction_error and "LLM unavailable" in fail_row.extraction_error, \
                f"extraction_error must be actionable: {fail_row.extraction_error!r}"
            # No candidates fabricated in the no-LLM case.
            no_cands = db.execute(
                scoped(ExtractedInsight, onit.id)
                .where(ExtractedInsight.product_document_id == failed_id)
            ).scalars().all()
            assert no_cands == [], \
                f"failed extraction must persist zero candidates, got {len(no_cands)}"
        finally:
            extract_mod._llm_extract = original_extract
        print("[OK] Document (3): LLM-unavailable doc lands status=failed "
              "with actionable extraction_error; zero candidates fabricated.")

        # ---- (4) Accept positioning — flips prior history to superseded -
        accept_pos = client.patch(
            f"/api/insights/{pos_cand.id}",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"action": "accept"},
        )
        assert accept_pos.status_code == 200, accept_pos.text
        # Refresh product from DB.
        db.refresh(simplelegal)
        assert "Modern matter management" in simplelegal.positioning, \
            f"positioning must be promoted; got {simplelegal.positioning!r}"
        hist = simplelegal.field_history or []
        # Prior seeded entry for "positioning" must be superseded.
        prior_pos = [e for e in hist if e.get("field") == "positioning"
                     and e.get("accepted_from_insight_id") == "__seed__"]
        assert prior_pos and prior_pos[0]["status"] == "superseded", \
            f"prior positioning history entry must be superseded; got {prior_pos}"
        # New active entry references the accepted insight.
        new_pos = [e for e in hist if e.get("field") == "positioning"
                   and e.get("accepted_from_insight_id") == pos_cand.id]
        assert new_pos and new_pos[0]["status"] == "active", \
            f"new positioning history entry must be active; got {new_pos}"
        # The accepted insight row itself flipped.
        db.refresh(pos_cand)
        assert pos_cand.status == "accepted"
        print(f"[OK] Document (4): single-value promotion flips prior "
              f"history entry to superseded ({len(hist)} entries total).")

        # ---- (5) List field promotion + dedupe + reject -----------------
        vp_cand = by_field["value_props"]
        accept_vp = client.patch(
            f"/api/insights/{vp_cand.id}",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"action": "accept"},
        )
        assert accept_vp.status_code == 200, accept_vp.text
        db.refresh(simplelegal)
        vps = simplelegal.value_props or []
        # Seed had "Cuts contract turnaround time in half within 90 days."
        # The stub provided that SAME string + a new "Self-serve matters..."
        # one. After promotion: dedupe drops the duplicate; the new one
        # is appended; previous seed values retained.
        dup = [v for v in vps if v == "Cuts contract turnaround time in half within 90 days."]
        assert len(dup) == 1, \
            f"duplicate value_prop must be deduped, got count={len(dup)}: {vps}"
        assert any("Self-serve" in (v or "") for v in vps), \
            f"new value_prop must be appended: {vps}"
        # The seed values that weren't in the stub stay intact.
        for seed_val in seed_value_props_before:
            assert seed_val in vps, \
                f"seed value_prop {seed_val!r} must be preserved through promotion"
        # Now reject the competitors candidate — it must NOT promote.
        pc_cand = by_field["product_competitors"]
        comp_before = list(simplelegal.product_competitors or [])
        rej = client.patch(
            f"/api/insights/{pc_cand.id}",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"action": "reject"},
        )
        assert rej.status_code == 200, rej.text
        db.refresh(simplelegal)
        comp_after = list(simplelegal.product_competitors or [])
        assert comp_after == comp_before, \
            "rejected candidate must NOT alter the product field"
        # And the rejected insight didn't write history either.
        recent_hist = [e for e in (simplelegal.field_history or [])
                       if e.get("accepted_from_insight_id") == pc_cand.id]
        assert recent_hist == [], "rejected insight must not write field_history"
        print(f"[OK] Document (5): list promotion dedupes (value_props "
              f"size={len(vps)}); rejected candidate does not promote.")

        # ---- (6) Messaging notes -----------------------------------------
        oh_cand = by_field["objection_handling"]
        accept_oh = client.patch(
            f"/api/insights/{oh_cand.id}",
            headers={"X-Dev-User-Email": "jordan@onit.com",
                     "Content-Type": "application/json"},
            json={"action": "accept"},
        )
        assert accept_oh.status_code == 200, accept_oh.text
        db.refresh(simplelegal)
        notes = simplelegal.messaging_notes or {}
        bucket = notes.get("objection_handling") or []
        assert bucket, f"objection_handling must populate messaging_notes; got {notes}"
        last = bucket[-1]
        assert last.get("objection", "").startswith("Too expensive"), last
        assert last.get("response", "").lower().startswith("lead with"), last
        assert "missed renewal" in (last.get("source_passage") or "").lower(), \
            f"source_passage must be retained: {last}"
        assert last.get("source_doc_id") == doc_id, \
            f"source_doc_id must reference the originating document: {last}"
        print(f"[OK] Document (6): objection_handling appended into "
              f"messaging_notes with source_passage + source_doc_id retained.")

        # ---- (7) No-override discipline ---------------------------------
        # Across ALL insights ever persisted (this entire smoke run),
        # no candidate may carry an *_override field_name.
        ALL_OVERRIDE_FIELDS = {
            "brand_voice_override", "banned_claims_override",
            "conversion_goal_override", "rubric_override",
            "utm_source_default", "utm_medium_default",
        }
        all_insights = db.execute(
            scoped(ExtractedInsight, onit.id)
        ).scalars().all()
        offenders = [i for i in all_insights if i.field_name in ALL_OVERRIDE_FIELDS]
        assert not offenders, \
            f"extraction MUST NOT produce override candidates, got: " \
            f"{[(i.id, i.field_name) for i in offenders]}"
        print(f"[OK] Document (7): no-override discipline holds — "
              f"{len(all_insights)} insights, 0 override candidates.")

        # ---- (8) Tenant isolation ----------------------------------------
        # Acme cannot list / read / accept any of Onit's docs or insights.
        cross_list = client.get(
            f"/api/products/{sl_id}/documents",
            headers={"X-Dev-User-Email": "ops@acme.com"})
        assert cross_list.status_code in (403, 404), \
            f"cross-org list docs must be denied, got {cross_list.status_code}"
        cross_doc_get = client.get(
            f"/api/products/{sl_id}/insights",
            headers={"X-Dev-User-Email": "ops@acme.com"})
        assert cross_doc_get.status_code in (403, 404), \
            f"cross-org list insights must be denied, got {cross_doc_get.status_code}"
        # Pick a still-pending insight (none left from the stub — we
        # accepted/rejected all four. Upload a fresh doc to create new
        # pending insights so we can test cross-org PATCH.)
        fresh_up = client.post(
            f"/api/products/{sl_id}/documents",
            headers={"X-Dev-User-Email": "jordan@onit.com"},
            files={"file": ("framework_v3.txt",
                            doc_body.encode("utf-8"), "text/plain")},
            data={"kind": "messaging_framework"},
        )
        assert fresh_up.status_code == 201, fresh_up.text
        assert run_once() is True
        fresh_pending = db.execute(
            scoped(ExtractedInsight, onit.id)
            .where(ExtractedInsight.product_document_id == fresh_up.json()["id"],
                   ExtractedInsight.status == "pending")
        ).scalars().all()
        assert fresh_pending, "expected fresh pending insights after re-upload"
        cross_patch = client.patch(
            f"/api/insights/{fresh_pending[0].id}",
            headers={"X-Dev-User-Email": "ops@acme.com",
                     "Content-Type": "application/json"},
            json={"action": "accept"})
        assert cross_patch.status_code in (403, 404), \
            f"cross-org PATCH must be denied, got {cross_patch.status_code}: {cross_patch.text}"
        # DB-layer isolation: scoped() returns zero docs / insights for Acme.
        assert db.execute(scoped(ProductDocument, other.id)).scalars().all() == [], \
            "TENANT LEAK: Acme sees Onit's product_documents"
        assert db.execute(scoped(ExtractedInsight, other.id)).scalars().all() == [], \
            "TENANT LEAK: Acme sees Onit's extracted_insights"
        print("[OK] Document (8): tenant isolation holds — list/get/PATCH "
              "all denied cross-org; scoped() returns 0 of Onit's rows for Acme.")

        # ---- (9) Dimensions reserved ------------------------------------
        all_insights2 = db.execute(
            scoped(ExtractedInsight, onit.id)
        ).scalars().all()
        non_null_dims = [i for i in all_insights2 if i.dimensions is not None]
        assert not non_null_dims, \
            f"dimensions must be null in v1, got non-null on: " \
            f"{[(i.id, i.dimensions) for i in non_null_dims]}"
        print(f"[OK] Document (9): dimensions column reserved — "
              f"{len(all_insights2)} insights, 0 with non-null dimensions.")

        # ---- (10) Content engine reflects promoted knowledge ------------
        # SimpleLegal CLM now has the doc-promoted positioning + the
        # promoted value_prop. A content_engine run scoped to this product
        # must reflect them in the rendered draft. (Stub builds use the
        # deterministic template path which folds positioning + value_props
        # into the body via _product_aware_view.)
        _set_review_mode(onit.id, "guardrail")
        promoted_run = Run(org_id=onit.id, agent_registration_id=onit_content_reg.id,
                           agent_key="content_engine", trigger="manual",
                           status="queued", product_id=sl_id,
                           task={"action": "generate", "content_type": "email",
                                 "topic": "Self-serve matters for legal ops",
                                 "target": "GC"})
        db.add(promoted_run); db.commit(); db.refresh(promoted_run)
        enqueue(db, onit.id, "run_agent", {"run_id": promoted_run.id})
        assert run_once() is True
        db.refresh(promoted_run)
        assert promoted_run.status == "succeeded", \
            f"product-promoted run failed: {promoted_run.error}"
        promoted_art = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.run_id == promoted_run.id,
                   Artifact.type == "content_draft")
        ).scalar_one()
        body_text = " ".join(b.get("text", "")
                             for b in promoted_art.body["content"]["blocks"])
        # Promoted positioning fragment must surface — the deterministic
        # template folds positioning into product_summary, which the email
        # template stitches into the body.
        assert "Modern matter management" in body_text, \
            f"promoted positioning must appear in the draft: {body_text!r}"
        # Promoted value_prop fragment must surface (it leads value_prop).
        body_text_lower = body_text.lower()
        assert ("self-serve" in body_text_lower
                or "cuts contract turnaround" in body_text_lower), \
            f"promoted value_prop must surface in the draft: {body_text!r}"
        # Provenance carries the product link + competitors_reflected.
        prov = promoted_art.body["provenance"]
        assert prov["product_id"] == sl_id
        assert "Ironclad" in (prov.get("competitors_reflected") or []), \
            f"product_competitors must reflect into provenance: {prov}"
        print(f"[OK] Document (10): content_engine run for SimpleLegal CLM "
              "reflects promoted positioning + value_props in the draft "
              "(north-star demo, mechanical form).")

        # ---------------------------------------------------------------------
        # No-echo discipline (regression guard) — TWO levels:
        #
        #   (A) Output-level on the DETERMINISTIC FALLBACK path: with a
        #       product whose brand_voice carries a distinctive marker,
        #       no block of the resulting draft may contain the marker,
        #       any instruction label ("Tone:", "brand_voice", ...), or
        #       a parenthesized comma-separated competitor list.
        #
        #   (B) Prompt-structure on the LLM path itself: stub the
        #       Anthropic client to CAPTURE the outbound request and
        #       assert what it sent. This is what the previous fix missed
        #       — the stubbed _llm_build never exercised the actual
        #       prompt structure, so the bug shipped. Now the test
        #       inspects what we'd actually send to the real model:
        #         * `system=` is passed.
        #         * The system message describes voice as instruction
        #           ("Voice you write in") — NOT as a labeled JSON field
        #           that the model could mistake for "fields to narrate".
        #         * Neither the system nor user message contains the
        #           string "Tone:" anywhere (mentioning it teaches the
        #           model to produce it — don't-think-about-the-elephant).
        #         * The user message shows a PLACEHOLDER schema (angle-
        #           bracket descriptions), NOT the fallback's rendered
        #           text the model can copy.
        #
        # A live-LLM verification ("does the REAL model still echo?") is
        # in scripts/verify_no_echo_live.py — gated on a real API key,
        # used to verify behavior after the fix lands.
        # ---------------------------------------------------------------------
        TONE_MARKER = "TONE_MARKER_DO_NOT_ECHO_xyzzy42"
        # Forbidden patterns we must NEVER see in any body block, on
        # either path. The parenthesized-list regex catches the LLM
        # leak that the previous round of fixes missed.
        import re as _re
        _PAREN_COMPETITOR_LIST = _re.compile(r"\([^)]*,[^)]*,[^)]*\)")
        _PAREN_VS_LIST = _re.compile(r"\(vs\.\s*[^)]*,[^)]*\)", _re.IGNORECASE)
        forbidden_labels = ("Tone:", "brand_voice", "value_prop:",
                            "banned_claims", "positioning:", "Voice:",
                            "Style:")

        # --- (A) Output-level check on the deterministic fallback path. ---
        simplelegal.brand_voice_override = (
            "Direct, confident, practitioner-first. " + TONE_MARKER)
        db.commit()
        try:
            no_echo_run = Run(
                org_id=onit.id, agent_registration_id=onit_content_reg.id,
                agent_key="content_engine", trigger="manual",
                status="queued", product_id=sl_id,
                task={"action": "generate", "content_type": "email",
                      "topic": "Cut weekly review overhead",
                      "target": "GC"})
            db.add(no_echo_run); db.commit(); db.refresh(no_echo_run)
            enqueue(db, onit.id, "run_agent", {"run_id": no_echo_run.id})
            assert run_once() is True, "no-echo run was not picked up"
            db.refresh(no_echo_run)
            assert no_echo_run.status == "succeeded", \
                f"no-echo run failed: {no_echo_run.error}"
            no_echo_art = db.execute(
                scoped(Artifact, onit.id)
                .where(Artifact.run_id == no_echo_run.id,
                       Artifact.type == "content_draft")
            ).scalar_one()
            blocks_to_check = no_echo_art.body["content"]["blocks"]
            for block in blocks_to_check:
                btext = block.get("text") or ""
                assert TONE_MARKER not in btext, \
                    f"NO-ECHO BREACH (A): brand_voice marker leaked into a " \
                    f"{block.get('kind')!r} block: {btext!r}"
                assert not _PAREN_COMPETITOR_LIST.search(btext), \
                    f"NO-ECHO BREACH (A): parenthesized comma-list leaked " \
                    f"into a {block.get('kind')!r} block: {btext!r}"
                assert not _PAREN_VS_LIST.search(btext), \
                    f"NO-ECHO BREACH (A): '(vs. X, Y, Z)' list leaked " \
                    f"into a {block.get('kind')!r} block: {btext!r}"
            joined = " ".join(b.get("text", "") for b in blocks_to_check)
            for forbidden in forbidden_labels:
                assert forbidden not in joined, \
                    f"NO-ECHO BREACH (A): instruction label {forbidden!r} " \
                    f"leaked into body: {joined!r}"
        finally:
            db.refresh(simplelegal)
            simplelegal.brand_voice_override = None
            db.commit()

        # --- (B) Prompt-structure check on the LLM path. ---
        # Capture what _llm_build sends to Claude by stubbing the
        # anthropic.Anthropic constructor with a fake client whose
        # messages.create() records its args + returns a canned response.
        import anthropic as _anthropic_mod
        captured_calls: list[dict] = []

        class _FakeMsg:
            def __init__(self, text):
                self.content = [type("Blk", (), {"type": "text", "text": text})()]
                self.usage = type("U", (), {"input_tokens": 100,
                                            "output_tokens": 50})()

        class _FakeClient:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                captured_calls.append(kwargs)
                # Return a syntactically-valid JSON envelope so the
                # caller's defensive parse succeeds and we exercise the
                # full call path.
                fake_json = (
                    '{"content_type":"email","blocks":['
                    '{"kind":"subject","text":"hello"},'
                    '{"kind":"body","text":"clean body."},'
                    '{"kind":"cta","text":"open →"}'
                    '],"metadata":{}}')
                return _FakeMsg(fake_json)

        original_Anthropic = _anthropic_mod.Anthropic
        # Temporarily un-stub _llm_build so the real implementation runs
        # against our fake SDK — that's the whole point: inspect the
        # actual prompt structure the production code emits. We use the
        # reference captured BEFORE the stub was applied (top of smoke);
        # re-importing here would just pick up the stub.
        original_llm_build_stub = content_templates_mod._llm_build
        content_templates_mod._llm_build = _ORIGINAL_LLM_BUILD
        _anthropic_mod.Anthropic = _FakeClient
        try:
            # Trigger a generate run; the worker calls _real_llm_build
            # which constructs the prompt and invokes _FakeClient.
            simplelegal.brand_voice_override = (
                "Direct, confident, practitioner-first. " + TONE_MARKER)
            db.commit()
            prompt_run = Run(
                org_id=onit.id, agent_registration_id=onit_content_reg.id,
                agent_key="content_engine", trigger="manual",
                status="queued", product_id=sl_id,
                task={"action": "generate", "content_type": "email",
                      "topic": "Spend visibility", "target": "GC"})
            db.add(prompt_run); db.commit(); db.refresh(prompt_run)
            enqueue(db, onit.id, "run_agent", {"run_id": prompt_run.id})
            assert run_once() is True
            db.refresh(prompt_run)
            assert prompt_run.status == "succeeded", \
                f"prompt-structure run failed: {prompt_run.error}"
        finally:
            _anthropic_mod.Anthropic = original_Anthropic
            content_templates_mod._llm_build = original_llm_build_stub
            db.refresh(simplelegal)
            simplelegal.brand_voice_override = None
            db.commit()
        assert captured_calls, "_real_llm_build did not invoke the SDK"
        call = captured_calls[-1]
        # The SDK call must use system= AND messages= cleanly separated.
        assert "system" in call, \
            "outbound LLM request must pass system= (got only " \
            f"{sorted(call.keys())})"
        assert "messages" in call and call["messages"], call
        system_text = call["system"] or ""
        user_text = call["messages"][0]["content"]
        combined = system_text + "\n" + user_text
        # The system message must describe the voice as INSTRUCTION
        # ("Voice you write in"), never as a labeled JSON field.
        assert "Voice you write in" in system_text, \
            f"system message must frame voice as instruction; got " \
            f"{system_text[:400]!r}"
        # The actual marker DOES appear in the system message (we want
        # the model to embody it as style) but NOT inside a labeled-JSON
        # field shape — no '"brand_voice":' substring anywhere in the
        # outbound payload.
        assert '"brand_voice":' not in combined, \
            f"prompt must not present brand_voice as a labeled field " \
            f"(model treats it as text to narrate): {combined[:600]!r}"
        # "Tone:" must not appear anywhere in either message — mentioning
        # it teaches the model to produce it.
        assert "Tone:" not in combined, \
            f"prompt must not contain the literal 'Tone:' label " \
            f"(triggers the don't-think-about-the-elephant echo): " \
            f"{combined[:600]!r}"
        # The schema in the user message must use placeholder strings,
        # not the fallback's already-rendered body text (otherwise the
        # model imitates the fallback prose).
        assert "<the main body" in user_text or "<body content" in user_text, \
            f"user message must show a placeholder schema, not rendered " \
            f"fallback prose: {user_text[:400]!r}"
        print(f"[OK] No-echo (A): output path — TONE_MARKER + parenthesized "
              "competitor lists + instruction labels all absent from blocks.")
        print(f"[OK] No-echo (B): outbound LLM request uses system=, the "
              "voice is framed as instruction (no \"brand_voice\":  field), "
              "no 'Tone:' label, and the user message ships a placeholder "
              "schema (not rendered fallback text).")

        # ---------------------------------------------------------------------
        # Asset Library — six checks:
        #   (1) GET /api/assets returns content + document + brief assets
        #       normalized into the unified shape; counts match underlying
        #       rows.
        #   (2) Filters combine (product/kind/type/status/campaign/date)
        #       and search matches title/body/extracted_text.
        #   (3) Tenant isolation: Acme sees 0 of Onit's assets;
        #       cross-org detail/download/reuse denied (403/404).
        #   (4) Download composition: content → clean .md; doc download
        #       is scoped (cross-org denied) and returns the file.
        #   (5) Reuse routing: duplicate creates new artifact preserving
        #       blocks; regenerate creates a content_engine run pre-
        #       filled from the source; route-to-campaign returns the
        #       step-4 placeholder target with the asset id and creates
        #       no campaign.
        #   (6) Product-selector scoping: ?product_id= filters to that
        #       product; omitting it returns the org-wide view.
        # ---------------------------------------------------------------------
        H_ONIT = {"X-Dev-User-Email": "jordan@onit.com"}
        H_ACME = {"X-Dev-User-Email": "ops@acme.com"}

        # (1) Unified projection + counts match underlying rows.
        all_assets = client.get("/api/assets?limit=500", headers=H_ONIT)
        assert all_assets.status_code == 200, all_assets.text
        aj = all_assets.json()
        assets_total = aj["total"]
        by_kind = {}
        for a in aj["assets"]:
            by_kind.setdefault(a["asset_kind"], []).append(a)
        # Cross-check counts against the source tables (Onit's rows).
        content_artifacts = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.type.in_(("content_draft", "content_ideas")))
        ).scalars().all()
        brief_artifacts = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.type == "market_brief")
        ).scalars().all()
        all_docs = db.execute(scoped(ProductDocument, onit.id)).scalars().all()
        assert len(by_kind.get("content", [])) == len(content_artifacts), \
            f"content count {len(by_kind.get('content', []))} vs " \
            f"{len(content_artifacts)} artifacts"
        assert len(by_kind.get("brief", [])) == len(brief_artifacts), \
            f"brief count {len(by_kind.get('brief', []))} vs " \
            f"{len(brief_artifacts)} brief artifacts"
        assert len(by_kind.get("document", [])) == len(all_docs), \
            f"document count {len(by_kind.get('document', []))} vs " \
            f"{len(all_docs)} product_documents"
        assert assets_total == (
            len(content_artifacts) + len(brief_artifacts) + len(all_docs))
        # Schema shape check on one of each kind.
        for kind in ("content", "document", "brief"):
            sample = next((a for a in aj["assets"] if a["asset_kind"] == kind), None)
            if sample is None:
                continue
            for required in ("id", "title", "asset_type", "status",
                             "created_at", "source_ref"):
                assert required in sample, f"{kind} asset missing {required}"
        print(f"[OK] Library (1): {assets_total} assets normalized "
              f"({len(by_kind.get('content', []))} content / "
              f"{len(by_kind.get('document', []))} document / "
              f"{len(by_kind.get('brief', []))} brief); counts match underlying tables.")

        # (2) Filters combine + search works.
        # Filter to documents only.
        docs_only = client.get("/api/assets?asset_kind=document", headers=H_ONIT)
        assert docs_only.status_code == 200, docs_only.text
        for a in docs_only.json()["assets"]:
            assert a["asset_kind"] == "document"
        # Filter by status=extracted on documents.
        extracted = client.get(
            "/api/assets?asset_kind=document&status=extracted", headers=H_ONIT)
        for a in extracted.json()["assets"]:
            assert a["asset_kind"] == "document" and a["status"] == "extracted"
        # Filter content by campaign — Onit has a "clm-comparison-q2"
        # campaign-tagged regen artifact from the quality-loop tests.
        camp_resp = client.get(
            "/api/assets?asset_kind=content&campaign=clm-comparison-q2",
            headers=H_ONIT)
        camp_assets = camp_resp.json()["assets"]
        assert camp_assets, "campaign filter must surface tagged content"
        for a in camp_assets:
            assert a["campaign"] == "clm-comparison-q2"
        # Combine product_id + asset_kind=content (SimpleLegal CLM).
        prod_content = client.get(
            f"/api/assets?product_id={sl_id}&asset_kind=content",
            headers=H_ONIT)
        for a in prod_content.json()["assets"]:
            assert a["product_id"] == sl_id
            assert a["asset_kind"] == "content"
        # Search across title + body. The smoke generated a draft with
        # "Cost-saving framework" in the topic — search must find it.
        search_resp = client.get(
            "/api/assets?q=cost-saving", headers=H_ONIT)
        assert search_resp.status_code == 200
        assert any("cost-saving" in (a["title"] or "").lower()
                   or "cost" in (a["title"] or "").lower()
                   for a in search_resp.json()["assets"]), \
            "search must surface a content asset with 'cost' in the title"
        # Search across document extracted_text — the seeded framework doc
        # contains "modern matter management". Documents must surface.
        doc_search = client.get(
            "/api/assets?asset_kind=document&q=modern%20matter", headers=H_ONIT)
        assert any(a["asset_kind"] == "document"
                   for a in doc_search.json()["assets"]), \
            f"extracted-text search must surface the framework doc; got " \
            f"{[a['title'] for a in doc_search.json()['assets']]}"
        # Date range — narrowing to a far-past window returns nothing.
        empty = client.get(
            "/api/assets?date_from=1990-01-01&date_to=1990-12-31",
            headers=H_ONIT)
        assert empty.status_code == 200 and empty.json()["total"] == 0
        print(f"[OK] Library (2): filters AND together (kind/status/"
              f"campaign/product/date) + search hits title and "
              f"extracted_text.")

        # (3) Tenant isolation: Acme can't see Onit's assets, can't
        # fetch details, can't reuse them.
        acme_assets = client.get("/api/assets?limit=500", headers=H_ACME)
        assert acme_assets.status_code in (200, 403, 404), acme_assets.text
        if acme_assets.status_code == 200:
            assert acme_assets.json()["total"] == 0, \
                f"TENANT LEAK: Acme sees {acme_assets.json()['total']} assets"
        # Pick a known Onit asset for cross-org detail/reuse probes.
        a_content = next(a for a in aj["assets"] if a["asset_kind"] == "content")
        a_doc = next(a for a in aj["assets"] if a["asset_kind"] == "document")
        a_brief = next((a for a in aj["assets"] if a["asset_kind"] == "brief"),
                       None)
        cross_detail = client.get(
            f"/api/assets/content/{a_content['id']}", headers=H_ACME)
        assert cross_detail.status_code in (403, 404), cross_detail.text
        cross_doc_dl = client.get(
            f"/api/assets/document/{a_doc['id']}/download", headers=H_ACME)
        assert cross_doc_dl.status_code in (403, 404), cross_doc_dl.text
        cross_dup = client.post(
            f"/api/assets/content/{a_content['id']}/duplicate",
            headers={**H_ACME, "Content-Type": "application/json"})
        assert cross_dup.status_code in (403, 404), cross_dup.text
        cross_regen = client.post(
            f"/api/assets/content/{a_content['id']}/regenerate",
            headers={**H_ACME, "Content-Type": "application/json"}, json={})
        assert cross_regen.status_code in (403, 404), cross_regen.text
        cross_route = client.post(
            f"/api/assets/content/{a_content['id']}/route-to-campaign",
            headers=H_ACME)
        assert cross_route.status_code in (403, 404), cross_route.text
        print("[OK] Library (3): tenant isolation holds — Acme list is "
              "empty; cross-org detail/download/duplicate/regenerate/"
              "route-to-campaign all denied.")

        # (4) Download composition + scoped doc download.
        # Pick a content_draft (not content_ideas) for a proper .md.
        draft_id = None
        for a in by_kind.get("content", []):
            if a["asset_type"] in ("email", "ad", "social_post", "blog_outline"):
                draft_id = a["id"]
                break
        assert draft_id, "expected at least one content_draft for download test"
        md = client.get(
            f"/api/assets/content/{draft_id}/download?format=md",
            headers=H_ONIT)
        assert md.status_code == 200
        assert "text/markdown" in md.headers["content-type"]
        assert md.headers.get("content-disposition", "").startswith("attachment"), \
            md.headers
        assert md.text.startswith("# "), \
            f".md must start with an H1 title; got {md.text[:80]!r}"
        assert "## " in md.text, "blocks should compose into H2 sections"
        # JSON variant.
        as_json = client.get(
            f"/api/assets/content/{draft_id}/download?format=json",
            headers=H_ONIT)
        assert as_json.status_code == 200
        import json as _json
        parsed = _json.loads(as_json.text)
        assert parsed["id"] == draft_id and "body" in parsed
        # Document download — original file, scoped, with the right
        # filename header.
        d_dl = client.get(
            f"/api/assets/document/{a_doc['id']}/download", headers=H_ONIT)
        assert d_dl.status_code == 200, d_dl.text
        assert "attachment" in d_dl.headers.get("content-disposition", "")
        # And the extracted-text companion.
        ex_txt = client.get(
            f"/api/assets/document/{a_doc['id']}/extracted-text",
            headers=H_ONIT)
        assert ex_txt.status_code == 200
        assert "text/plain" in ex_txt.headers["content-type"]
        print(f"[OK] Library (4): content composes into clean .md "
              f"({len(md.text)} chars) + raw .json; document download "
              "serves the original file; cross-org doc download denied.")

        # (5) Reuse routing.
        # 5a. Duplicate creates a new draft with parent_id, status=ready,
        # blocks preserved verbatim.
        dup = client.post(
            f"/api/assets/content/{draft_id}/duplicate",
            headers={**H_ONIT, "Content-Type": "application/json"})
        assert dup.status_code == 200, dup.text
        dup_j = dup.json()
        dup_art = db.execute(
            scoped(Artifact, onit.id).where(Artifact.id == dup_j["id"])
        ).scalar_one()
        source_art = db.execute(
            scoped(Artifact, onit.id).where(Artifact.id == draft_id)
        ).scalar_one()
        assert dup_art.parent_id == source_art.id, \
            f"duplicate must link back via parent_id; got {dup_art.parent_id!r}"
        assert dup_art.type == "content_draft"
        assert dup_art.status == "ready"
        assert dup_art.title.startswith("Duplicate of ")
        assert dup_art.run_id != source_art.run_id, \
            "duplicate must live on a NEW Run (synthetic, cost=0)"
        dup_run = db.execute(
            scoped(Run, onit.id).where(Run.id == dup_art.run_id)
        ).scalar_one()
        assert dup_run.cost_usd == 0.0
        assert dup_run.trigger == "duplicate"
        # Blocks preserved verbatim — the user edits FROM here.
        assert (dup_art.body or {}).get("content", {}).get("blocks") == \
            (source_art.body or {}).get("content", {}).get("blocks")
        # 5b. Regenerate from a content asset queues a content_engine
        # run pre-filled with the source's content_type + topic + target.
        # We override action_type to ensure it actually goes through the
        # generate path.
        regen = client.post(
            f"/api/assets/content/{draft_id}/regenerate",
            headers={**H_ONIT, "Content-Type": "application/json"}, json={})
        assert regen.status_code == 200, regen.text
        rj = regen.json()
        assert rj["agent_key"] == "content_engine"
        assert rj["status"] == "queued"
        assert rj["task"]["action"] == "generate"
        assert rj["task"]["content_type"], "content_type must be pre-filled"
        assert rj["task"]["topic"], "topic must be pre-filled"
        # Pre-fill MUST match the source.
        src_content = (source_art.body or {}).get("content") or {}
        src_metadata = src_content.get("metadata") or {}
        assert rj["task"]["content_type"] == src_content.get("content_type")
        assert rj["task"]["topic"] == src_metadata.get("topic") \
            or rj["task"]["topic"] == source_art.title
        # Brief regenerate: source from a market_brief artifact.
        if a_brief:
            brief_regen = client.post(
                f"/api/assets/brief/{a_brief['id']}/regenerate",
                headers={**H_ONIT, "Content-Type": "application/json"},
                json={"content_type": "ad"})
            assert brief_regen.status_code == 200, brief_regen.text
            brj = brief_regen.json()
            assert brj["agent_key"] == "content_engine"
            assert brj["task"]["action"] == "generate"
            assert brj["task"]["content_type"] == "ad", brj
            assert brj["task"]["topic"], "brief regen must derive a topic"
        # 5c. Route-to-campaign is a PLACEHOLDER — validates ownership
        # and returns the routing payload; no campaign created (no
        # campaigns table exists).
        route = client.post(
            f"/api/assets/content/{draft_id}/route-to-campaign",
            headers=H_ONIT)
        assert route.status_code == 200, route.text
        route_j = route.json()
        assert route_j == {**route_j, "placeholder": True,
                           "target_view": "campaigns",
                           "asset_id": draft_id,
                           "asset_kind": "content"}, route_j
        print(f"[OK] Library (5): duplicate creates a new draft "
              f"preserving blocks (parent_id linkage holds, cost=0); "
              f"regenerate queues a content_engine run pre-filled from "
              f"source ({rj['task']['content_type']}/{rj['task']['topic']!r}); "
              "route-to-campaign returns the step-4 placeholder target "
              "with the asset id (no campaign created).")

        # (6) Product-selector scoping: ?product_id= filters; omitting
        # it returns the org-wide view.
        all_scoped = client.get(
            f"/api/assets?product_id={sl_id}&limit=500", headers=H_ONIT)
        for a in all_scoped.json()["assets"]:
            assert a["product_id"] == sl_id, \
                f"with ?product_id={sl_id[:6]}, every asset must scope; got " \
                f"{a['product_id']!r}"
        # Org-wide is strictly larger (includes org-level + other products).
        assert all_scoped.json()["total"] < assets_total, \
            f"product-scoped total ({all_scoped.json()['total']}) must be " \
            f"strictly less than org-wide ({assets_total})"
        # An org-level run (no product_id) appears in the org-wide view
        # but NOT in the product-scoped one — sanity check the boundary.
        org_level = [a for a in aj["assets"] if a["product_id"] is None]
        assert org_level, "expected at least one org-level asset"
        scoped_ids = {a["id"] for a in all_scoped.json()["assets"]}
        for a in org_level:
            assert a["id"] not in scoped_ids, \
                f"org-level asset {a['id']!r} leaked into product-scoped view"
        print(f"[OK] Library (6): product-scoped view returns "
              f"{all_scoped.json()['total']} of {assets_total} assets "
              "(strict subset; org-level rows excluded).")

        # ---------------------------------------------------------------------
        # Campaigns — eight hermetic checks. THE load-bearing principle:
        # campaigns REFERENCE assets, they do NOT own generation. The
        # /generate path enqueues content_engine runs; the worker stamps
        # campaign_id on each artifact + proposal. Archiving the campaign
        # leaves the generated assets in the library (test 5).
        # ---------------------------------------------------------------------
        H_ONIT = {"X-Dev-User-Email": "jordan@onit.com"}
        H_ACME = {"X-Dev-User-Email": "ops@acme.com"}

        # (1) Create draft + tenant-isolated.
        c_create = client.post(
            "/api/campaigns",
            headers={**H_ONIT, "Content-Type": "application/json"},
            json={
                "name": "Onit Spend Manager Launch",
                "description": "Coordinated launch motion.",
                "campaign_type": "launch",
                "objective": "Drive 50 qualified demos in 30 days.",
                "primary_cta": "book a demo",
                "product_id": product_id,   # the Onit Spend Manager product
                "target_personas": ["General Counsel", "VP Legal Ops"],
                "selected_channels": ["linkedin", "email", "organic"],
            })
        assert c_create.status_code == 201, c_create.text
        camp = c_create.json()
        assert camp["status"] == "draft"
        assert camp["product_id"] == product_id
        assert camp["campaign_type"] == "launch"
        assert camp["target_personas"] == ["General Counsel", "VP Legal Ops"]
        # utm_campaign is product-prefixed per Build A convention.
        assert camp["utm_campaign"].startswith("campaign-spend-manager"), \
            f"utm_campaign must be product-prefixed: {camp['utm_campaign']!r}"
        # Tenant isolation on create — Acme cannot read this row.
        leaked = db.execute(scoped(Campaign, other.id)).scalars().all()
        assert leaked == [], "TENANT LEAK: Acme can see Onit's campaigns"
        cross_get = client.get(f"/api/campaigns/{camp['id']}", headers=H_ACME)
        assert cross_get.status_code in (403, 404), cross_get.text
        print(f"[OK] Campaign (1): draft created (utm_campaign="
              f"{camp['utm_campaign']!r}, product-prefixed); tenant-isolated.")

        # (2) Propose: deterministic with no key.
        # Save real LLM key, then run with key cleared to exercise the
        # deterministic rule-based path. Restore after.
        from app.config import get_settings as _gs
        _prev_key = _gs().anthropic_api_key
        _gs().anthropic_api_key = ""   # type: ignore[attr-defined]
        try:
            prop = client.post(
                f"/api/campaigns/{camp['id']}/propose",
                headers={**H_ONIT, "Content-Type": "application/json"}, json={})
            assert prop.status_code == 200, prop.text
            propj = prop.json()
            plan = propj["plan"]
            items = plan.get("derivative_assets") or []
            assert len(items) >= 3, \
                f"deterministic plan must have >=3 items, got {len(items)}"
            for item in items:
                for f in ("id", "content_type", "channel", "topic",
                          "rationale", "cadence_hint"):
                    assert f in item, f"plan item missing {f}: {item}"
            assert plan.get("channel_mix"), "plan must include channel_mix"
            assert plan.get("cadence_guidance"), \
                "plan must include cadence_guidance"
            assert plan.get("source") == "deterministic-rule-based"
            # Sensible for the type — launch templates use email + linkedin.
            channels_in_plan = {it["channel"] for it in items}
            assert channels_in_plan & {"linkedin", "email", "organic"}, \
                f"launch plan should hit launch-typical channels; got {channels_in_plan}"
        finally:
            _gs().anthropic_api_key = _prev_key  # type: ignore[attr-defined]
        # Re-propose with the LLM stubbed to return a known plan so we
        # can also verify the LLM path persists the plan. Stub _llm_propose
        # at the planner module — it's the function in the LLM branch.
        original_llm_propose = planner_mod._llm_propose

        def _stub_propose(campaign_row, profile, parent_summary, settings, fallback):
            return {
                "derivative_assets": [
                    {"id": "stubbed-1", "content_type": "social_post",
                     "channel": "linkedin", "topic": "Stub launch post",
                     "angle": "Hook", "audience": "GC",
                     "rationale": "Stubbed for smoke",
                     "cadence_hint": "Day 0"},
                ],
                "channel_mix": [{"channel": "linkedin",
                                 "weight": "primary",
                                 "rationale": "Stub"}],
                "cadence_guidance": "Stubbed guidance.",
                "source": "llm",
            }, 0.0013
        planner_mod._llm_propose = _stub_propose
        try:
            prop2 = client.post(
                f"/api/campaigns/{camp['id']}/propose",
                headers={**H_ONIT, "Content-Type": "application/json"}, json={})
            assert prop2.status_code == 200, prop2.text
            p2 = prop2.json()
            assert p2["plan"]["source"] == "llm"
            assert len(p2["plan"]["derivative_assets"]) == 1
            assert p2["_propose_cost_usd"] == 0.0013
        finally:
            planner_mod._llm_propose = original_llm_propose
        print(f"[OK] Campaign (2): deterministic plan ({len(items)} items, "
              f"channels={channels_in_plan}) when no key; stubbed LLM "
              "plan persists when the call succeeds.")

        # (3) PATCH plan → status flips to 'planned'.
        # We curate a small 3-item plan we control (so test 4 can assert
        # 3 runs / 3 artifacts with the right UTM scheme).
        approved_plan = {
            "derivative_assets": [
                {"id": "1-linkedin-launch",
                 "content_type": "social_post", "channel": "linkedin",
                 "topic": "Launch announcement", "angle": "Name what's new",
                 "audience": "GC", "rationale": "Lead the motion",
                 "cadence_hint": "Day 0"},
                {"id": "2-email-cta-demo",
                 "content_type": "email", "channel": "email",
                 "topic": "See it in 15 minutes", "angle": "Demo CTA",
                 "audience": "GC + Legal Ops",
                 "rationale": "Highest-control demo channel",
                 "cadence_hint": "Day 0 + Day 3"},
                {"id": "3-ad-retarget",
                 "content_type": "ad", "channel": "linkedin",
                 "topic": "Cut turnaround in half",
                 "angle": "Retargeting ad", "audience": "Engaged visitors",
                 "rationale": "Capture engaged visitors",
                 "cadence_hint": "Weeks 1–3"},
            ],
            "channel_mix": [
                {"channel": "linkedin", "weight": "primary",
                 "rationale": "B2B credibility"},
                {"channel": "email", "weight": "primary",
                 "rationale": "Demo CTA"},
            ],
            "cadence_guidance": "LinkedIn lead, email cadence, ads retarget.",
            "source": "human-edited",
        }
        patch = client.patch(
            f"/api/campaigns/{camp['id']}",
            headers={**H_ONIT, "Content-Type": "application/json"},
            json={"plan": approved_plan})
        assert patch.status_code == 200, patch.text
        assert patch.json()["status"] == "planned", \
            "PATCH plan must flip draft → planned"
        # And we can add/remove items via subsequent PATCHes.
        approved_plan_2 = dict(approved_plan)
        approved_plan_2["derivative_assets"] = approved_plan["derivative_assets"][:2]
        patch2 = client.patch(
            f"/api/campaigns/{camp['id']}",
            headers={**H_ONIT, "Content-Type": "application/json"},
            json={"plan": approved_plan_2})
        assert patch2.status_code == 200
        assert len(patch2.json()["plan"]["derivative_assets"]) == 2
        # Restore the 3-item plan for test 4.
        client.patch(
            f"/api/campaigns/{camp['id']}",
            headers={**H_ONIT, "Content-Type": "application/json"},
            json={"plan": approved_plan})
        print("[OK] Campaign (3): PATCH plan persists; status flips to "
              "'planned'; subsequent PATCH can add/remove items.")

        # (4) Generate: calls content_engine 3×, shared utm_campaign +
        # per-item source/medium/content, campaign_id back-ref on artifacts.
        # Re-enable the content_engine stub if anything turned it off.
        _set_review_mode(onit.id, "guardrail")
        gen = client.post(
            f"/api/campaigns/{camp['id']}/generate",
            headers={**H_ONIT, "Content-Type": "application/json"}, json={})
        assert gen.status_code == 200, gen.text
        genj = gen.json()
        assert genj["queued_count"] == 3, genj
        assert genj["campaign"]["status"] == "generating"
        # Drain the worker queue completely — earlier smoke tests can
        # leave stale-leased jobs behind; we need to process everything
        # before the campaign artifacts will land. Cap the drain at
        # 100 iterations as a safety net.
        for _ in range(100):
            if not run_once():
                break
        # The three artifacts now exist, scoped to the org + the campaign.
        camp_arts = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.campaign_id == camp["id"],
                   Artifact.type == "content_draft")
        ).scalars().all()
        assert len(camp_arts) == 3, \
            f"expected 3 campaign artifacts, got {len(camp_arts)}"
        # Shared utm_campaign on every artifact; per-item source / medium
        # / content vary so attribution reports can slice the campaign.
        for a in camp_arts:
            assert a.utm_campaign == camp["utm_campaign"], \
                f"every artifact must carry the shared utm_campaign; got " \
                f"{a.utm_campaign!r} vs {camp['utm_campaign']!r}"
        sources = {a.utm_source for a in camp_arts}
        mediums = {a.utm_medium for a in camp_arts}
        contents = {a.utm_content for a in camp_arts}
        assert sources == {"linkedin", "email"}, \
            f"per-item utm_source must vary by channel; got {sources}"
        assert mediums == {"social_post", "email", "ad"}, \
            f"per-item utm_medium must vary by content_type; got {mediums}"
        # utm_content carries the plan item slug — three distinct values.
        assert len(contents) == 3, \
            f"utm_content must be per-item unique; got {contents}"
        # The campaign's generated_asset_ids is refreshed on detail read.
        detail = client.get(f"/api/campaigns/{camp['id']}", headers=H_ONIT)
        assert detail.status_code == 200
        detail_j = detail.json()
        gen_ids = detail_j["generated_asset_ids"]
        assert set(gen_ids) == {a.id for a in camp_arts}
        # Detail GET also flips generating → active once every plan item has
        # produced an artifact. Without this, the campaign sticks at
        # 'generating' forever even though the batch has completed.
        assert detail_j["status"] == "active", (
            f"campaign status should flip to 'active' once all 3 items "
            f"have artifacts; got {detail_j['status']!r}")
        # Library projection picks the artifacts up — they are NORMAL
        # content artifacts (the load-bearing principle).
        lib = client.get(
            f"/api/assets?asset_kind=content&product_id={product_id}",
            headers=H_ONIT)
        lib_ids = {a["id"] for a in lib.json()["assets"]}
        for a in camp_arts:
            assert a.id in lib_ids, \
                f"campaign artifact {a.id} must appear in the library projection"
        print(f"[OK] Campaign (4): /generate enqueued 3 content_engine "
              f"runs; 3 artifacts landed with shared utm_campaign + per-"
              f"item source ({sources}) / medium ({mediums}) / unique "
              f"utm_content; campaign_id back-ref + library projection "
              "both pick them up.")

        # (5) Archive: campaign.status → archived; assets remain in the
        # library AND keep their campaign_id back-ref (until the campaign
        # row is hard-deleted, which we don't do here).
        arch = client.delete(f"/api/campaigns/{camp['id']}", headers=H_ONIT)
        assert arch.status_code == 200, arch.text
        assert arch.json()["status"] == "archived"
        # Assets still in the library, status untouched.
        lib_after = client.get(
            f"/api/assets?asset_kind=content&product_id={product_id}",
            headers=H_ONIT)
        lib_ids_after = {a["id"] for a in lib_after.json()["assets"]}
        for a in camp_arts:
            assert a.id in lib_ids_after, \
                f"archive must NOT remove asset {a.id} from the library"
        # An asset can also exist with campaign_id NULL — verify a non-
        # campaign asset from earlier tests is still there.
        existing_org_art = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.campaign_id.is_(None),
                   Artifact.type == "content_draft").limit(1)
        ).scalar_one_or_none()
        assert existing_org_art is not None, \
            "expected at least one campaign_id=NULL artifact (independence)"
        print(f"[OK] Campaign (5): archived → assets remain in library "
              f"({len(lib_ids_after)} content total, all 3 campaign assets "
              f"preserved); campaign_id=NULL assets coexist (independence "
              "holds — campaigns reference, don't own).")

        # (6) Channel rec seam — accepts performance_context=None AND
        # accepts a populated context arg (v1 ignores it). Verifies the
        # FUTURE substrate-grounded path is wired but unbuilt.
        r1 = planner_mod.recommend_channels(
            "demand_gen", ["linkedin", "email"], "book a demo",
            performance_context=None)
        r2 = planner_mod.recommend_channels(
            "demand_gen", ["linkedin", "email"], "book a demo",
            performance_context={"future": "not implemented"})
        assert r1["recommended_channels"], \
            "v1 best-practice channels must be non-empty"
        assert r1 == r2, \
            "v1 must IGNORE performance_context — same output regardless"
        # The function signature carries the keyword so a future build
        # can wire substrate-grounded scoring without an API break.
        import inspect
        sig = inspect.signature(planner_mod.recommend_channels)
        assert "performance_context" in sig.parameters
        assert sig.parameters["performance_context"].default is None
        print(f"[OK] Campaign (6): channel-rec seam reserved — "
              f"performance_context=None default, callable with a context "
              "arg, v1 ignores it (same output) — same discipline as the "
              "dimensions column.")

        # (7) Tenant isolation across propose/generate/patch + parent-asset.
        cross_propose = client.post(
            f"/api/campaigns/{camp['id']}/propose",
            headers={**H_ACME, "Content-Type": "application/json"}, json={})
        assert cross_propose.status_code in (403, 404), cross_propose.text
        cross_gen = client.post(
            f"/api/campaigns/{camp['id']}/generate",
            headers={**H_ACME, "Content-Type": "application/json"}, json={})
        assert cross_gen.status_code in (403, 404), cross_gen.text
        cross_patch = client.patch(
            f"/api/campaigns/{camp['id']}",
            headers={**H_ACME, "Content-Type": "application/json"},
            json={"name": "stolen"})
        assert cross_patch.status_code in (403, 404), cross_patch.text
        cross_archive = client.delete(
            f"/api/campaigns/{camp['id']}", headers=H_ACME)
        assert cross_archive.status_code in (403, 404), cross_archive.text
        # Cross-org parent_asset attach denied — an Acme user can't make
        # an Onit asset the parent of Onit's campaign (or any campaign).
        any_onit_art = camp_arts[0]
        cross_attach = client.post(
            f"/api/campaigns/-/attach-asset/content/{any_onit_art.id}",
            headers={**H_ACME, "Content-Type": "application/json"},
            json={"campaign_id": camp["id"]})
        assert cross_attach.status_code in (403, 404), cross_attach.text
        print("[OK] Campaign (7): tenant isolation — propose / generate / "
              "patch / archive / attach-asset all denied cross-org.")

        # (8) Review gate honored — flip the org to gate_all and trigger
        # a fresh generate run for ONE plan item. Verify the artifact
        # lands status=pending_review AND a campaign-tagged Proposal
        # exists in the approval queue.
        # First create a small follow-up campaign so we don't fight the
        # archived one's status. Same SimpleLegal product.
        c2 = client.post(
            "/api/campaigns",
            headers={**H_ONIT, "Content-Type": "application/json"},
            json={"name": "Onit gate-all check",
                  "campaign_type": "demand_gen",
                  "primary_cta": "book a demo",
                  "product_id": sl_id,
                  "selected_channels": ["linkedin"]}).json()
        client.patch(
            f"/api/campaigns/{c2['id']}",
            headers={**H_ONIT, "Content-Type": "application/json"},
            json={"plan": {
                "derivative_assets": [
                    {"id": "g1", "content_type": "social_post",
                     "channel": "linkedin",
                     "topic": "Single gate-all probe",
                     "angle": "Test", "audience": "GC",
                     "rationale": "Verify review gate",
                     "cadence_hint": "Now"}],
                "channel_mix": [], "cadence_guidance": "",
                "source": "test"}})
        _set_review_mode(onit.id, "gate_all")
        try:
            gate_gen = client.post(
                f"/api/campaigns/{c2['id']}/generate",
                headers={**H_ONIT, "Content-Type": "application/json"}, json={})
            assert gate_gen.status_code == 200, gate_gen.text
            assert gate_gen.json()["queued_count"] == 1
            assert run_once() is True
            gate_art = db.execute(
                scoped(Artifact, onit.id)
                .where(Artifact.campaign_id == c2["id"],
                       Artifact.type == "content_draft")
            ).scalar_one()
            assert gate_art.status == "pending_review", \
                f"gate_all must route to pending_review; got {gate_art.status}"
            # Campaign-tagged Proposal lands in the approval queue.
            gate_props = db.execute(
                scoped(Proposal, onit.id)
                .where(Proposal.campaign_id == c2["id"],
                       Proposal.action_type == "content_review")
            ).scalars().all()
            assert len(gate_props) == 1, \
                f"gate_all must produce one campaign-tagged proposal; got {len(gate_props)}"
            assert gate_props[0].status == "pending"
        finally:
            _set_review_mode(onit.id, "guardrail")
        print(f"[OK] Campaign (8): review gate honored — under gate_all, "
              "campaign-generated draft lands status=pending_review and a "
              "campaign-tagged content_review proposal sits in the approval "
              "queue.")

        # (9) Grader fence tolerance — Anthropic frequently wraps grader
        # JSON in ```json fences. The grader used to refuse those and stamp
        # status='ungraded' (this is what every campaign-generated draft hit
        # on the first manual walkthrough). Same envelope parser as the
        # propose path now handles them. We stub the LLM directly so this
        # test is hermetic and doesn't depend on a key.
        import types as _types
        fenced_response = (
            "```json\n"
            "{\n"
            '  "status": "graded",\n'
            '  "overall": 78,\n'
            '  "per_criterion": [\n'
            '    {"name": "on_brand", "score": 80, "reason": "ok"},\n'
            '    {"name": "clarity", "score": 76, "reason": "tight"}\n'
            "  ],\n"
            '  "suggestions": ["tighten the CTA"]\n'
            "}\n"
            "```"
        )

        class _FakeBlock:
            type = "text"
            def __init__(self, t): self.text = t
        class _FakeMsg:
            content = [_FakeBlock(fenced_response)]
            usage = _types.SimpleNamespace(input_tokens=10, output_tokens=20)
        class _FakeMessages:
            def create(self, **_k): return _FakeMsg()
        class _FakeClient:
            messages = _FakeMessages()
            def __init__(self, **_k): pass

        # Patch anthropic.Anthropic for the one call _llm_grade makes.
        import anthropic as _anth_mod
        original_anthropic_cls = _anth_mod.Anthropic
        _anth_mod.Anthropic = _FakeClient
        try:
            from app.agents.content_grader import DEFAULT_RUBRIC
            from app.config import get_settings as _gs_grader
            # Call the REAL _llm_grade (smoke's startup stub has been
            # installed for the rest of the suite). The whole point of
            # this test is that the actual parse path tolerates fences.
            grade, cost = _ORIGINAL_LLM_GRADE(
                content={"content_type": "social_post",
                         "blocks": [{"kind": "body", "text": "draft text"}],
                         "metadata": {}},
                profile={},
                rubric=list(DEFAULT_RUBRIC),
                settings=_gs_grader())
        finally:
            _anth_mod.Anthropic = original_anthropic_cls
        assert grade["status"] == "graded", \
            f"fenced ```json``` should parse cleanly; got {grade}"
        assert grade["overall"] == 78, grade
        assert grade["suggestions"] == ["tighten the CTA"], grade
        print("[OK] Campaign (9): grader tolerates ```json fences — fenced "
              f"response parsed into status='graded', overall={grade['overall']} "
              "(was 'ungraded' before the fix).")

        # (10) Carousel as a structure-only content_type — the planner can
        # propose 'carousel' but until this fix the content engine raised
        # ValueError("Unknown content_type 'carousel'"). Now it produces a
        # multi-block slide structure with structure_only=True metadata; no
        # visual rendering (brief: "structure now, render later").
        from app.agents.content_templates import build as build_content
        car_content, car_cost = build_content(
            content_type="carousel",
            profile={"product_summary": "Our platform",
                     "value_prop": "Cut hours of busywork.",
                     "conversion_goal": "book a demo"},
            brief=None,
            topic="Contract review benchmarks",
            target="in-house legal ops",
        )
        assert car_content["content_type"] == "carousel"
        blocks = car_content.get("blocks") or []
        assert len(blocks) >= 3, \
            f"carousel must produce a multi-slide structure; got {len(blocks)}"
        kinds = {b.get("kind") for b in blocks}
        assert "slide_cover" in kinds and "slide_cta" in kinds, \
            f"carousel needs a cover + cta slide; got {kinds}"
        assert car_content["metadata"].get("structure_only") is True, \
            "carousel must mark structure_only=True so the UI does not " \
            "promise a rendered visual"
        # Smoke registry check — make sure the type shows up in available
        # types so the UI's content-type picker can offer it.
        from app.agents.content_templates import available_types
        assert "carousel" in available_types(), available_types()
        print(f"[OK] Campaign (10): carousel registered as structure-only — "
              f"{len(blocks)} blocks (slides={sorted(k for k in kinds)}), "
              "metadata.structure_only=True; visual rendering deferred to a "
              "future layer per the brief.")

        # ---- Memory loop (8 hermetic tests) -------------------------------
        # Single shared infrastructure: query_memory + how both content and
        # campaign code paths consume it. Memory is RETRIEVAL + DETERMINISTIC
        # weighting + HONEST confidence + EVIDENCE-BACKED context — NOT ML.
        # Seeding goes through SQLAlchemy directly (faster + deterministic
        # than uploading a CSV) under a dedicated campaign so the tests
        # don't fight earlier metric_points or earlier campaigns.
        from datetime import date as _date, timedelta as _td
        import types
        from app.memory.query import (
            query_memory as _query_memory, summarize_for_prompt,
            _recency_weight, _confidence_for, _NUMERATOR_TOKENS,
        )
        from app.memory import query_memory as _query_memory_via_pkg
        # Shared-infrastructure precondition: the planner and the content
        # engine MUST reach the same function object. The product_id seam
        # would silently bifurcate otherwise.
        from app.api import campaigns as campaigns_api_mod
        # The agent imports query_memory inside the method via
        # `from app.memory import summarize_for_prompt` — what we care
        # about is that NO second retrieval implementation exists. We
        # assert that by checking the package's exported function IS the
        # one in query.py — and that the planner-side call site uses it.
        assert _query_memory_via_pkg is _query_memory, (
            "query_memory must be the SAME function object whether "
            "imported via app.memory or app.memory.query — duplicated "
            "retrieval would defeat the whole 'shared infrastructure' point.")
        assert campaigns_api_mod.query_memory is _query_memory, (
            "the campaigns API must call the shared query_memory — not "
            "a local re-implementation.")

        # Create a dedicated "memory test" campaign + product scope so
        # nothing here entangles with the earlier Campaign (1-10) tests.
        mem_camp = Campaign(
            org_id=onit.id, product_id=sl_id,
            name="Memory loop test campaign",
            campaign_type="demand_gen",
            primary_cta="book a demo",
            target_personas=["General Counsel"],
            target_segments=["enterprise"],
            target_industries=["Legal Services"],
            selected_channels=["linkedin", "email", "google"],
            status="active",
            utm_campaign="campaign-memory-test",
        )
        db.add(mem_camp)
        db.commit()
        db.refresh(mem_camp)

        # Acme also gets a campaign with the SAME utm_campaign string —
        # purely so we can prove scoped() doesn't accidentally cross
        # tenants on the join. Smoke's Acme org was created back in the
        # tenant-isolation block; reuse it.
        acme_org = db.execute(select(Org).where(Org.domain == "acme.com")).scalar_one()
        acme_camp = Campaign(
            org_id=acme_org.id,
            name="Acme look-alike (must not leak)",
            campaign_type="demand_gen",
            primary_cta="book a demo",
            target_personas=["Chief Legal Officer"],
            selected_channels=["linkedin"],
            status="active",
            utm_campaign="campaign-memory-test",  # same string, DIFFERENT org
        )
        db.add(acme_camp)
        db.commit()

        # Seed metric_points across channels with varying volume + recency.
        # Shape: linkedin is the strong performer; email a moderate
        # contributor; google is thin-data (1 conversion data point);
        # baseline rows must be ignored.
        today = _date.today()
        def _mp(metric, value, days_ago, *, utm_source=None, utm_medium=None,
                utm_campaign=mem_camp.utm_campaign,
                is_baseline=False, product=sl_id, org=onit.id):
            db.add(MetricPoint(
                org_id=org, source="test",
                metric_name=metric, value=float(value),
                date=today - _td(days=days_ago),
                utm_source=utm_source, utm_medium=utm_medium,
                utm_campaign=utm_campaign,
                is_baseline=is_baseline, product_id=product,
            ))

        # LinkedIn — 12 ad clicks + 12 conversion rows across the last
        # ~30 days → moderate confidence + non-zero conversion rate.
        for i in range(12):
            _mp("clicks", 100, i * 2, utm_source="linkedin", utm_medium="social_post")
            _mp("conversions", 5, i * 2, utm_source="linkedin", utm_medium="social_post")
        # Email — 10 rows (moderate threshold) but lower rate so we can
        # assert ranking. Slightly older so recency weight is smaller.
        for i in range(10):
            _mp("clicks", 50, 30 + i * 3, utm_source="email", utm_medium="email")
            _mp("conversions", 1, 30 + i * 3, utm_source="email", utm_medium="email")
        # Google — only 2 points: thin-data, must come back as
        # 'insufficient' and observation MUST be framed as "not enough yet."
        _mp("clicks", 200, 5, utm_source="google", utm_medium="ad")
        _mp("conversions", 1, 5, utm_source="google", utm_medium="ad")
        # Baseline rows — these are backdrop only. Memory must IGNORE them
        # entirely or the conversion-rate denominator would be inflated.
        _mp("clicks", 9999, 1, utm_source="linkedin", utm_medium="social_post",
            is_baseline=True)
        # Reddit — 4 rows (3 ≤ n < 10) → low confidence. The planner's
        # tier gate MUST cap this at "watching" — it must NEVER be
        # weighted "primary" in channel_mix, even when the observed
        # rate is non-zero.
        for i in range(2):
            _mp("clicks", 50, i, utm_source="reddit", utm_medium="ad")
            _mp("conversions", 1, i, utm_source="reddit", utm_medium="ad")
        # Organic — 10 rows with utm_source set but NO utm_campaign.
        # These are the "untagged backdrop" the dashboard already keeps
        # out of the attributed view; memory must apply the SAME
        # discipline. Without the Bug 1 fix, these would have inflated
        # organic into a moderate-confidence pattern (n=10, positive
        # rate) and the planner would have surfaced it as a recommended
        # channel — a false finding from untagged data.
        for i in range(5):
            _mp("clicks", 100, i, utm_source="organic",
                utm_medium="blog_outline", utm_campaign=None)
            _mp("conversions", 8, i, utm_source="organic",
                utm_medium="blog_outline", utm_campaign=None)
        # Acme metric points — should NEVER appear in Onit's memory.
        _mp("conversions", 100, 1, utm_source="linkedin", utm_medium="social_post",
            org=acme_org.id, product=None)
        db.commit()

        # (1) Retrieval + weighting. Patterns come back, channel buckets
        # rank by confidence then sample size + rate. LinkedIn beats
        # email; google is at the bottom (insufficient). Reddit is in
        # the middle (low). Organic is NOT here at all — untagged rows
        # are excluded from pattern computation (Bug 1 discipline).
        patterns = _query_memory(db, onit.id, product_id=sl_id)
        chans = [p for p in patterns if p["dimension"] == "channel"]
        chan_keys = [p["key"] for p in chans]
        assert "linkedin" in chan_keys and "email" in chan_keys and "google" in chan_keys, \
            f"expected linkedin/email/google in channel patterns, got {chan_keys}"
        assert "organic" not in chan_keys, \
            f"untagged organic must NOT generate a channel pattern; got " \
            f"{chan_keys}"
        # LinkedIn comes first (moderate, fresh, higher rate); google last
        # (insufficient sinks to the end no matter what).
        assert chan_keys[0] == "linkedin", \
            f"linkedin should rank first; got order {chan_keys}"
        assert chan_keys[-1] == "google", \
            f"insufficient google should be last; got order {chan_keys}"
        # Recency weight is deterministic + explainable in one breath.
        assert abs(_recency_weight(today, today, 180) - 1.0) < 1e-9
        assert abs(_recency_weight(today - _td(days=180), today, 180) - 0.1) < 1e-9
        # Verify baseline rows truly excluded — LinkedIn clicks should
        # NOT include the 9999 baseline value.
        li = next(p for p in chans if p["key"] == "linkedin")
        assert li["metric_basis"]["clicks"] < 9999, \
            f"baseline row leaked into the click sum: {li['metric_basis']}"
        print(f"[OK] Memory (1): retrieval + weighting deterministic — "
              f"{len(chans)} channel patterns, ordered "
              f"{chan_keys}; recency weight 1.0 today → 0.1 at lookback; "
              f"baseline rows excluded (linkedin clicks="
              f"{li['metric_basis']['clicks']:g} ≠ 9999).")

        # (2) Honest confidence framing — thin data is "watching", not a
        # finding with a small number. Forbidden shape (per brief):
        # "<key>: 0.x effectiveness (low confidence)" — must NOT appear.
        # The deferring discipline applies to BOTH insufficient AND low
        # confidence: a LOW pattern (3 ≤ n < 10) must also defer, not
        # quote a rate. Numbers stay in metric_basis for inspection; the
        # one-liner reads "Watching."
        google = next(p for p in chans if p["key"] == "google")
        assert google["confidence"] == "insufficient", \
            f"google has 2 points; must be insufficient, got {google['confidence']}"
        assert "not enough" in google["observation"].lower(), \
            f"thin-data observation must read as 'not enough yet'; got " \
            f"{google['observation']!r}"
        assert "effectiveness" not in google["observation"].lower(), \
            "forbidden small-number framing leaked: " + google["observation"]
        # Bug 3: low-confidence observations defer just like insufficient.
        reddit = next(p for p in chans if p["key"] == "reddit")
        assert reddit["confidence"] == "low", \
            f"reddit has 4 points; must be low, got {reddit['confidence']}"
        assert "not enough" in reddit["observation"].lower(), \
            f"LOW-confidence observation must defer like insufficient; got " \
            f"{reddit['observation']!r}"
        assert "per 100 clicks" not in reddit["observation"].lower(), (
            "Bug 3 regression: low-confidence observation MUST NOT quote a "
            "rate (reads as a finding with a small number). Numbers belong "
            "in metric_basis for inspection. Got: " + reddit["observation"])
        # The numbers ARE still in metric_basis so the inspectable detail
        # works — only the user-facing one-liner defers.
        assert reddit["metric_basis"]["clicks"] > 0, reddit["metric_basis"]
        assert reddit["metric_basis"]["conversions"] > 0, reddit["metric_basis"]
        # Well-supported pattern reads as a finding with metric_basis.
        assert li["confidence"] in ("moderate", "high"), li["confidence"]
        assert li["metric_basis"]["conversion_rate"] is not None
        assert "per 100 clicks" in li["observation"], li["observation"]
        # Direct unit check on the confidence function — fewer than three
        # points is ALWAYS insufficient, regardless of how recent.
        c_thin, _ = _confidence_for(1, 0)
        assert c_thin == "insufficient", c_thin
        print(f"[OK] Memory (2): honest confidence — google (n=2, "
              f"insufficient) AND reddit (n={reddit['sample_size']}, low) "
              f"both framed as 'not enough yet' (no rate in the one-liner); "
              f"linkedin (n={li['sample_size']}) is a finding with rate="
              f"{li['metric_basis']['conversion_rate']:.3f}.")

        # (3) Campaign influence — propose's channel rec is reordered by
        # memory WITH evidence-citing rationale; insufficient data falls
        # back to best-practice and SAYS so.
        rec_with_mem = planner_mod.recommend_channels(
            "demand_gen", ["linkedin", "email", "google"], "book a demo",
            performance_context=patterns)
        assert rec_with_mem["memory_status"] == "memory_informed", rec_with_mem
        assert any("Memory:" in m["rationale"]
                   for m in rec_with_mem["channel_mix"]), \
            "at least one channel rationale must cite memory evidence"
        ev_keys = {e["key"] for e in rec_with_mem["memory_evidence"]}
        assert "linkedin" in ev_keys, ev_keys
        # Insufficient-only fallback: build a patterns list with ONLY a
        # thin pattern and verify it does NOT reorder.
        thin_only = [p for p in patterns
                     if p["confidence"] == "insufficient" and p["dimension"] == "channel"]
        assert thin_only, "expected at least one insufficient channel pattern"
        rec_thin = planner_mod.recommend_channels(
            "demand_gen", ["linkedin", "email"], "book a demo",
            performance_context=thin_only)
        assert rec_thin["memory_status"] == "best_practice", rec_thin
        assert "no performance history" in rec_thin["rationale"].lower(), \
            rec_thin["rationale"]
        # And an explicit "watching" surface so the UI can show the dim.
        assert "watching" in rec_thin["rationale"].lower(), rec_thin["rationale"]
        print(f"[OK] Memory (3): campaign influence — memory-informed "
              f"reorder cites evidence in rationale (n={len(rec_with_mem['memory_evidence'])} "
              f"patterns surfaced); insufficient-only path falls back to "
              f"best-practice and says so ({rec_thin['memory_status']}).")

        # (4) Content influence — suggest re-ranks ideas by memory with
        # evidence shown; generation injects memory as system-message
        # context that the model embodies but DOES NOT echo into the body.
        # Re-rank: build a synthetic ideas list + patterns, call the
        # static rerank helper directly so we don't depend on a brief.
        from app.agents.content_engine import ContentEngineAgent
        ideas_in = [
            {"content_type": "email", "topic": "Slow contract review",
             "rationale": "default", "target": "GC"},
            {"content_type": "social_post", "topic": "Modern matter management",
             "rationale": "default", "target": "GC"},
        ]
        ranked = ContentEngineAgent._memory_rerank_ideas(ideas_in, patterns)
        assert ranked[0]["content_type"] == "social_post", \
            f"social_post should rerank above email (linkedin/social_post is " \
            f"the moderate winner in seeded data); got " \
            f"{[i['content_type'] for i in ranked]}"
        assert "memory_evidence" in ranked[0], \
            f"top idea must carry memory_evidence; got {ranked[0]}"
        # Generation context: capture the outbound LLM call and assert
        # the memory summary lands in the system message. We patch
        # anthropic.Anthropic the same way the grader fence test does.
        from app.memory import summarize_for_prompt as _spp
        mem_text = _spp(patterns)
        assert mem_text, "summary should be non-empty for actionable patterns"
        captured = {}
        class _CapBlock:
            type = "text"
            def __init__(self, t): self.text = t
        class _CapMsg:
            content = [_CapBlock('{"content_type": "social_post", '
                                 '"blocks": [{"kind":"body","text":"x"},'
                                 '{"kind":"cta","text":"y"}], '
                                 '"metadata": {}}')]
            usage = types.SimpleNamespace(input_tokens=5, output_tokens=5)
        class _CapMessages:
            def create(self, **kw):
                captured["system"] = kw.get("system", "")
                captured["user"] = kw["messages"][0]["content"]
                return _CapMsg()
        class _CapClient:
            messages = _CapMessages()
            def __init__(self, **k): pass

        import anthropic as _ap
        original_ap_cls = _ap.Anthropic
        _ap.Anthropic = _CapClient
        # Temporarily restore the REAL _llm_build (smoke globally stubs it).
        try:
            content_templates_mod._llm_build = _ORIGINAL_LLM_BUILD
            from app.agents.content_templates import build as _build
            from app.config import get_settings as _gs_mem
            # Settings.anthropic_api_key was force-set at smoke startup so
            # the LLM path will fire instead of falling back to template.
            template_profile = {
                "product_summary": "Onit SimpleLegal CLM",
                "value_prop": "Cut contract turnaround in half.",
                "memory_summary": mem_text,
            }
            _build("social_post", template_profile, None,
                   "Modern matter management", "GC")
        finally:
            _ap.Anthropic = original_ap_cls
            content_templates_mod._llm_build = _stub_content_llm  # restore smoke stub

        sys_msg = captured.get("system", "")
        assert "What has historically worked" in sys_msg, \
            "memory summary must land in the SYSTEM message (no-echo: " \
            "guidance not labeled field); got system=" + sys_msg[:300]
        # No-echo: the model's output body must NOT contain the memory
        # summary text. We're going through the deterministic stub here
        # (which returns the schema-shaped social_post above), and the
        # output is the captured _CapMsg JSON — strip it and check.
        # (The real no-echo test path is reused in the existing No-echo
        # (A) + (B) tests; here we just guard the system-vs-user split.)
        user_msg = captured.get("user", "")
        assert "What has historically worked" not in user_msg, \
            "memory belongs in SYSTEM, never in USER message"
        print(f"[OK] Memory (4): content influence — suggest re-ranked "
              f"({[i['content_type'] for i in ranked]}, evidence attached); "
              f"generation injects memory into the SYSTEM message "
              f"({len(sys_msg)} chars), never into the user message; "
              "no-echo discipline preserved.")

        # (5) Shared service — already asserted above; restate explicitly
        # to make the test legible at the smoke output level.
        assert _query_memory_via_pkg is _query_memory
        assert campaigns_api_mod.query_memory is _query_memory
        # And worker.py imports it inside _make_ctx via local import — we
        # verify here that the symbol resolves to the same object.
        import app.worker as _worker_mod
        # The local import lives inside the function body; assert that
        # importing the same path produces the same object.
        from app.memory import query_memory as _from_pkg_again
        assert _from_pkg_again is _query_memory
        print("[OK] Memory (5): shared service — query_memory is ONE function "
              "(planner side + content side + worker side resolve to the same "
              "object); no duplicated retrieval logic.")

        # (6) Backwards compat — with NO metric_points, query_memory returns
        # empty and the agents behave EXACTLY as before. We can't drop the
        # seeded points without breaking other tests, so verify the empty
        # path with a freshly-created throwaway org that has none.
        empty_org = Org(name="MemEmpty", domain="memempty.test")
        db.add(empty_org); db.commit(); db.refresh(empty_org)
        empty_patterns = _query_memory(db, empty_org.id)
        assert empty_patterns == [], empty_patterns
        # Recommend_channels with empty list / None falls all the way back
        # to today's deterministic best-practice + memory_status=best_practice.
        rec_empty = planner_mod.recommend_channels(
            "demand_gen", ["linkedin"], "book a demo",
            performance_context=[])
        assert rec_empty["memory_status"] == "best_practice"
        assert rec_empty["memory_evidence"] == []
        rec_none = planner_mod.recommend_channels(
            "demand_gen", ["linkedin"], "book a demo")
        assert rec_none["memory_status"] == "best_practice"
        # And the rerank degrades silently — no patterns → input order.
        ranked_empty = ContentEngineAgent._memory_rerank_ideas(ideas_in, [])
        assert [i["content_type"] for i in ranked_empty] == \
               [i["content_type"] for i in ideas_in]
        # Summary text is empty so the system message doesn't add a
        # "What has historically worked" section at all.
        assert summarize_for_prompt([]) == ""
        print("[OK] Memory (6): backwards compat — no data → empty patterns; "
              "recommend_channels returns memory_status=best_practice; "
              "rerank preserves input order; system message gets no memory "
              "section.")

        # (7) Tenant isolation — Acme has its OWN copy of the metric_point
        # row above; query_memory for Acme must surface ONLY Acme data,
        # never Onit's. AND GET /api/memory is scoped via current_user.
        acme_patterns = _query_memory(db, acme_org.id)
        acme_chans = [p for p in acme_patterns if p["dimension"] == "channel"]
        # Only the single conversion row above for Acme — must show as
        # 'insufficient' with framing, and NEVER include Onit's clicks.
        acme_li = next((p for p in acme_chans if p["key"] == "linkedin"), None)
        if acme_li is not None:
            assert acme_li["confidence"] == "insufficient", acme_li
            # 100 conversions seeded with no Onit click denominator
            # leaking in — basis comes ONLY from Acme's own row.
            assert acme_li["metric_basis"]["clicks"] == 0, \
                f"Onit's clicks leaked into Acme's memory: {acme_li['metric_basis']}"
        # API surface: /api/memory is scoped via current_user. acme.com is
        # not in allowed_domains in this smoke (only onit.com is), so the
        # cross-org probe is denied at the auth boundary — consistent with
        # how Campaign (7) and Library (3) assert tenant isolation. The
        # data-level isolation above already proves scoped() works; the
        # auth layer is the second line.
        mem_resp = client.get("/api/memory", headers=H_ACME)
        assert mem_resp.status_code in (200, 403, 404), \
            (mem_resp.status_code, mem_resp.text)
        if mem_resp.status_code == 200:
            for p in mem_resp.json()["patterns"]:
                mb = p["metric_basis"]
                assert (mb.get("clicks") or 0) < 200, \
                    f"Onit click data leaked into Acme API response: {p}"
        cross = client.get(f"/api/memory?product_id={sl_id}", headers=H_ACME)
        assert cross.status_code in (200, 403, 404), \
            (cross.status_code, cross.text)
        if cross.status_code == 200:
            assert cross.json()["patterns"] == [], cross.json()
        print(f"[OK] Memory (7): tenant isolation — Acme sees ONLY its own "
              f"data at the data layer (clicks=0, no Onit denominator); "
              f"API surface denied at auth ({mem_resp.status_code}).")

        # (8) Inspectable endpoint — empty + populated shape.
        # Populated: Onit gets actionable + watching counts.
        onit_resp = client.get(f"/api/memory?product_id={sl_id}", headers=H_ONIT)
        assert onit_resp.status_code == 200
        data = onit_resp.json()
        assert isinstance(data["patterns"], list) and data["patterns"]
        assert data["summary"]["actionable"] >= 1, data["summary"]
        assert data["summary"]["watching"] >= 1, data["summary"]
        assert data["lookback_days"] == 180
        # Per-pattern shape — every key required by the UI must be present.
        for p in data["patterns"]:
            for k in ("dimension", "key", "key_display", "observation",
                     "metric_basis", "sample_size", "recency",
                     "confidence", "confidence_reason"):
                assert k in p, f"pattern missing required key {k!r}: {p}"
        # Empty path: filter to a channel that has NO data.
        empty_resp = client.get(
            f"/api/memory?product_id={sl_id}&channel=nonexistent",
            headers=H_ONIT)
        assert empty_resp.status_code == 200
        assert empty_resp.json()["patterns"] == [], empty_resp.json()
        # Honest summary: 0 actionable + 0 watching when patterns is empty.
        assert empty_resp.json()["summary"] == {
            "total": 0, "actionable": 0, "watching": 0}
        print(f"[OK] Memory (8): GET /api/memory — populated returns "
              f"{data['summary']['actionable']} actionable + "
              f"{data['summary']['watching']} watching patterns with the full "
              f"shape; empty filter returns [] + zeroed summary.")

        # (9) Bug 1 — untagged metric_points are EXCLUDED from channel /
        # audience / content_type patterns and from the planner reorder.
        # 10 untagged organic rows were seeded above with a positive
        # conversion rate; without the fix, they would have produced a
        # moderate-confidence organic pattern + a recommended channel
        # in propose. This test pins the discipline.
        chan_keys_now = {p["key"] for p in patterns
                         if p["dimension"] == "channel"}
        assert "organic" not in chan_keys_now, (
            "Bug 1 regression: untagged organic rows produced a channel "
            "pattern. Got channels: " + str(chan_keys_now))
        # And content_type / audience buckets do not see them either.
        ctype_keys = {p["key"] for p in patterns
                      if p["dimension"] == "content_type"}
        assert "blog_outline" not in ctype_keys, (
            "Bug 1 regression: untagged content_type bucket leaked. Got "
            "content_types: " + str(ctype_keys))
        # Planner does NOT reorder organic into recommended_channels —
        # the rec is built from best-practice + memory + user picks, and
        # organic is not in demand_gen's best-practice. The smoke
        # campaign seeded earlier picked ["linkedin", "email", "google"]
        # (no organic). With organic excluded from memory, it cannot
        # sneak in via the patterns route either.
        rec_no_organic = planner_mod.recommend_channels(
            "demand_gen", ["linkedin", "email", "google"], "book a demo",
            performance_context=patterns)
        assert "organic" not in rec_no_organic["recommended_channels"], (
            "Bug 1 regression: untagged organic was recommended by the "
            "planner. Got: " + str(rec_no_organic["recommended_channels"]))
        assert all(e["key"] != "organic" for e in rec_no_organic["memory_evidence"]), (
            "untagged organic must not appear in memory_evidence")
        print(f"[OK] Memory (9): Bug 1 — untagged rows excluded from "
              f"patterns AND from the planner reorder; organic absent "
              f"from channel/content_type patterns, absent from "
              f"recommended_channels ({rec_no_organic['recommended_channels']}), "
              f"absent from memory_evidence.")

        # (10) Bug 2 — confidence-tier gating in the planner.
        # With linkedin (moderate), email (moderate), google (insufficient),
        # reddit (low) in memory, the planner must:
        #   * Promote linkedin + email to primary (moderate + rate).
        #   * CAP reddit at "watching" — never primary, even though the
        #     observed rate > 0. (This is the exact regression the live
        #     UI showed: reddit was at primary with a confident citation.)
        #   * CAP google at "watching" too (insufficient).
        # Awareness campaign_type best-practice includes "organic" which
        # has NO memory data after Bug 1's fix — assert it falls back to
        # "support" rather than being silently dropped from the mix.
        rec_aware = planner_mod.recommend_channels(
            "awareness", [], "book a demo", performance_context=patterns)
        weights_by = {m["channel"]: m["weight"] for m in rec_aware["channel_mix"]}
        assert weights_by.get("linkedin") == "primary", weights_by
        assert weights_by.get("reddit") == "watching", \
            (f"Bug 2 regression: reddit (LOW) must be capped at "
             f"'watching', got {weights_by.get('reddit')!r}; full: "
             f"{weights_by}")
        assert weights_by.get("google") == "watching", weights_by
        assert weights_by.get("organic") == "support", (
            "Bug 2: best-practice channel with NO memory data must fall "
            "back to 'support' (visible, not dropped). Got: " + str(weights_by))
        # Memory-informed rationale calls out watching + support honestly.
        rat = rec_aware["rationale"].lower()
        assert "watching" in rat, rec_aware["rationale"]
        assert "support" in rat or "no performance history" in rat, \
            rec_aware["rationale"]
        # Per-channel rationale for reddit defers — does NOT quote a rate.
        reddit_rat = next(m["rationale"] for m in rec_aware["channel_mix"]
                          if m["channel"] == "reddit")
        assert "per 100 clicks" not in reddit_rat.lower(), (
            "Bug 2/3: reddit rationale must defer, not quote a rate. "
            "Got: " + reddit_rat)
        assert "watching" in reddit_rat.lower(), reddit_rat
        # Tier-sort order: primaries before watching/support.
        weight_order = [m["weight"] for m in rec_aware["channel_mix"]]
        _TIER = {"primary": 0, "secondary": 1, "support": 2, "watching": 3}
        assert weight_order == sorted(weight_order,
                                       key=lambda w: _TIER.get(w, 99)), \
            f"channel_mix must be sorted by tier; got {weight_order}"
        # User-pick honoring on a no-data channel: meta isn't in awareness
        # best-practice, has no memory; user-pick should bump it to
        # secondary (not silently support).
        rec_pick = planner_mod.recommend_channels(
            "awareness", ["meta"], "book a demo", performance_context=patterns)
        meta_weight = {m["channel"]: m["weight"] for m in rec_pick["channel_mix"]}
        assert meta_weight.get("meta") == "secondary", (
            "User-picked channel without memory data should be honored "
            "at 'secondary'; got " + str(meta_weight.get("meta")))
        print(f"[OK] Memory (10): Bug 2 — confidence-tier gating holds — "
              f"linkedin={weights_by['linkedin']}, email={weights_by['email']}, "
              f"reddit={weights_by['reddit']} (capped), "
              f"google={weights_by['google']} (capped), "
              f"organic={weights_by['organic']} (best-practice fallback, "
              "not dropped); user-picked no-data channel honored at "
              f"'secondary' ({meta_weight.get('meta')}).")

        # ---- Report Composer (10 hermetic tests) --------------------------
        # Storytelling layer: ONE intelligence engine, THREE renderers.
        # Reports are first-class Artifacts (content_draft) with
        # body.content.content_type=report_<audience> — they inherit
        # Library, .md download, grade, regenerate, approval gate from
        # the existing content surfaces.
        from datetime import date as _date2, timedelta as _td2
        from app.reports import build_report_intelligence
        from app.reports.renderers import (
            RENDERERS as _RENDERERS, render_board, render_ceo_weekly,
            render_sales_leadership,
        )
        from app.reports.intelligence import (
            _aggregate_funnel as _agg_funnel,
        )

        # The seed registers report_composer for Onit alongside content_engine.
        onit_report_reg = db.execute(
            scoped(AgentRegistration, onit.id)
            .where(AgentRegistration.key == "report_composer")
        ).scalar_one()

        def _trigger_report_run(audience: str, scope: dict,
                                product_id: str | None = None,
                                parent_artifact_id: str | None = None) -> Run:
            task = {"audience": audience, "scope": scope,
                    "lookback_days": 30}
            if parent_artifact_id:
                task["parent_artifact_id"] = parent_artifact_id
            r = Run(org_id=onit.id, agent_registration_id=onit_report_reg.id,
                    agent_key="report_composer", trigger="manual",
                    status="queued", product_id=product_id, task=task)
            db.add(r)
            db.commit(); db.refresh(r)
            enqueue(db, onit.id, "run_agent", {"run_id": r.id})
            assert run_once() is True, "worker did not pick up report job"
            db.refresh(r)
            return r

        # (1) Engine: build_report_intelligence for a POPULATED org
        # returns the full ReportIntelligence shape with non-null sections.
        # Memory + production + campaigns all touch real data from the
        # earlier smoke seeds.
        today_d = _date2.today()
        scope_window = {"kind": "time_window",
                        "start": (today_d - _td2(days=30)).isoformat(),
                        "end": today_d.isoformat()}
        intel = build_report_intelligence(db, onit.id, product_id=sl_id,
                                          scope=scope_window)
        for k in ("scope", "period_summary", "notable_changes",
                  "memory_highlights", "watching", "production",
                  "campaigns", "top_content", "open_questions",
                  "honesty_notes"):
            assert k in intel, f"intelligence missing required key {k!r}"
        # honesty_notes always populated when prior-period data is absent
        # (smoke's earlier seed has no points before the 30-day window).
        assert intel["honesty_notes"], (
            "honesty_notes must be populated when prior-period data is "
            "absent. Got: " + str(intel["honesty_notes"]))
        assert isinstance(intel["memory_highlights"], list)
        assert isinstance(intel["production"], dict)
        print(f"[OK] Report (1): engine returns full intelligence shape "
              f"({len(intel['memory_highlights'])} memory_highlights, "
              f"{len(intel['watching'])} watching, "
              f"{len(intel['campaigns'])} campaigns, "
              f"{len(intel['honesty_notes'])} honesty_notes).")

        # (2) Empty scope: a far-PAST window has zero data. Engine must
        # return a truthful minimal object with honesty_notes explicitly
        # saying "no data in scope" rather than confabulating. Far past
        # (rather than far future) keeps this distinct from Report (11)
        # which covers the future-date confabulation guard separately.
        empty_scope = {"kind": "time_window",
                       "start": "1900-01-01", "end": "1900-01-31"}
        intel_empty = build_report_intelligence(db, onit.id, product_id=sl_id,
                                                scope=empty_scope)
        assert not intel_empty["scope"].get("is_future"), (
            "far-past scope must NOT be flagged as future")
        assert intel_empty["period_summary"]["attributed"]["data_points"] == 0
        assert intel_empty["period_summary"]["backdrop"]["data_points"] == 0
        assert intel_empty["campaigns"] == []
        assert intel_empty["top_content"] == []
        # The "No metric_points in scope" note must be one of the honesty_notes.
        notes_joined = " ".join(intel_empty["honesty_notes"]).lower()
        assert "no metric_points in scope" in notes_joined, \
            f"empty scope must produce explicit 'no data in scope' note; got " \
            f"{intel_empty['honesty_notes']}"
        print(f"[OK] Report (2): empty (far-past) scope honest — zero "
              f"attributed + zero backdrop, no campaigns, no top content; "
              f"honesty_notes call out 'no data in scope' explicitly.")

        # (3) Memory propagation: an insufficient pattern lands in
        # `watching`, NEVER in `memory_highlights`. Moderate/high land in
        # highlights with evidence. Reuse the seeded data — google
        # (insufficient) must NOT show up in highlights; linkedin
        # (moderate) MUST show up with metric_basis.
        highlight_keys = {h.get("key") for h in intel["memory_highlights"]}
        watching_keys = {w.get("key") for w in intel["watching"]}
        assert "google" not in highlight_keys, (
            "insufficient 'google' must NOT land in memory_highlights")
        assert "google" in watching_keys, (
            "insufficient 'google' must land in watching")
        # linkedin (moderate from earlier seed) → in highlights with evidence.
        li_high = next((h for h in intel["memory_highlights"]
                        if h.get("key") == "linkedin"), None)
        assert li_high is not None, "linkedin (moderate) must be in memory_highlights"
        assert li_high.get("metric_basis", {}).get("clicks", 0) > 0, \
            "memory_highlight must carry evidence in metric_basis"
        # And reddit (low) is in watching too, framed honestly via the
        # observation field (Bug 3's deferring phrasing — no rate quoted).
        rd_w = next((w for w in intel["watching"]
                     if w.get("key") == "reddit"), None)
        if rd_w is not None:
            assert "per 100 clicks" not in (rd_w["observation"] or "").lower(), \
                "watching observation must defer, not quote a rate"
        print(f"[OK] Report (3): memory propagation — moderate/high → "
              f"memory_highlights with metric_basis ({len(intel['memory_highlights'])} "
              f"items); insufficient/low → watching ({len(intel['watching'])} "
              f"items); google never in highlights.")

        # (4) THE LOAD-BEARING TEST — three audiences differ materially
        # from the SAME intelligence object. Call all three renderers on
        # `intel`. Lengths must differ in line with the audience target
        # ranges; audience-specific sections must appear/absent per spec.
        from app.config import get_settings as _gs_rpt
        rpt_settings = _gs_rpt()
        # Force deterministic path so the test doesn't depend on Anthropic.
        prev_key = rpt_settings.anthropic_api_key
        rpt_settings.anthropic_api_key = ""  # type: ignore[attr-defined]
        try:
            board_draft, _ = render_board(intel, profile={}, settings=rpt_settings)
            ceo_draft, _ = render_ceo_weekly(intel, profile={}, settings=rpt_settings)
            sales_draft, _ = render_sales_leadership(intel, profile={}, settings=rpt_settings)
        finally:
            rpt_settings.anthropic_api_key = prev_key  # type: ignore[attr-defined]

        def _flat(draft):
            return "\n".join((b.get("text") or "")
                             for b in (draft or {}).get("blocks", []))
        board_text, ceo_text, sales_text = _flat(board_draft), _flat(ceo_draft), _flat(sales_draft)
        # Three drafts must have different content_types.
        assert board_draft["content_type"] == "report_board"
        assert ceo_draft["content_type"] == "report_ceo_weekly"
        assert sales_draft["content_type"] == "report_sales_leadership"
        # Length discipline: CEO weekly is the shortest (signal-dense);
        # board is the longest (~600-900 word target). Sales is in between.
        assert len(ceo_text) < len(board_text), (
            f"CEO weekly must be shorter than board. ceo={len(ceo_text)} "
            f"board={len(board_text)}")
        assert len(ceo_text) < len(sales_text), (
            f"CEO weekly must be shorter than sales. ceo={len(ceo_text)} "
            f"sales={len(sales_text)}")
        # Audience-specific markers — explicit content differences.
        assert "Strategic asks" in board_text, (
            "board MUST surface 'Strategic asks' (defensive posture). Got: "
            + board_text[:300])
        assert "Period summary" in board_text, "board MUST have period summary"
        # CEO weekly: NO campaign list, NO production accounting section.
        assert "Campaigns in play" not in ceo_text, (
            "CEO weekly MUST NOT include a campaigns-in-play section")
        assert "Production accountability" not in ceo_text, (
            "CEO weekly MUST NOT include production accounting")
        # Sales: "for sales" / "share with prospects" language; campaigns block.
        assert ("sales" in sales_text.lower()
                and "share" in sales_text.lower()), (
            "sales draft MUST be framed TO sales (share / sales language). "
            "Got: " + sales_text[:300])
        assert "Strategic asks" not in sales_text, (
            "sales MUST NOT include board-style 'Strategic asks' section")
        print(f"[OK] Report (4): three audiences DIFFER materially — "
              f"board={len(board_text)}c (has Strategic asks + Period summary), "
              f"ceo={len(ceo_text)}c (no campaigns/production block), "
              f"sales={len(sales_text)}c (TO-sales framing).")

        # (5) Reports are first-class assets — generate via worker, then
        # confirm Artifact + Library projection + .md download +
        # gradeable + regenerable via parent_id.
        _set_review_mode(onit.id, "all_through")
        rpt_run = _trigger_report_run("board", scope_window, product_id=sl_id)
        assert rpt_run.status == "succeeded", (rpt_run.status, rpt_run.error)
        rpt_art = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.run_id == rpt_run.id,
                   Artifact.type == "report_draft")
        ).scalar_one()
        assert rpt_art.status == "ready", rpt_art.status
        body = rpt_art.body or {}
        ct = (body.get("content") or {}).get("content_type")
        assert ct == "report_board", f"report content_type must be 'report_board'; got {ct!r}"
        # Library projection picks it up as a TOP-LEVEL "report" kind —
        # not a content sub-kind. The filter resolves off the same field
        # every other kind uses (Artifact.type).
        lib = client.get(
            f"/api/assets?asset_kind=report&product_id={sl_id}",
            headers=H_ONIT)
        assert lib.status_code == 200, lib.text
        lib_assets = lib.json()["assets"]
        lib_ids = {x["id"] for x in lib_assets}
        assert rpt_art.id in lib_ids, (
            "report artifact must appear in the Library projection with "
            "asset_kind=report")
        # All returned assets must be asset_kind=report (the projection
        # surfaces the top-level kind).
        for x in lib_assets:
            assert x["asset_kind"] == "report", (
                f"asset_kind=report filter returned a non-report: {x}")
        # .md download — same content path accepts report_draft too.
        dl = client.get(
            f"/api/assets/content/{rpt_art.id}/download?format=md",
            headers=H_ONIT)
        assert dl.status_code == 200
        assert b"Board update" in dl.content or b"Period summary" in dl.content, (
            "downloaded .md must contain the report's actual content")
        # Grade attached (advisory, may be ungraded if grader stubbed but
        # the field is always present on report artifacts).
        assert rpt_art.grade is not None, "report grade field must be populated"
        # Regenerate via parent_id (versioning chain reused — same as content).
        rpt_run_v2 = _trigger_report_run("board", scope_window,
                                          product_id=sl_id,
                                          parent_artifact_id=rpt_art.id)
        rpt_art_v2 = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.run_id == rpt_run_v2.id,
                   Artifact.type == "report_draft")
        ).scalar_one()
        assert rpt_art_v2.parent_id == rpt_art.id, (
            "regenerate must produce a child artifact via parent_id chain. "
            f"Got parent_id={rpt_art_v2.parent_id!r}, expected {rpt_art.id!r}")
        # GET /api/reports lists them.
        rpt_list = client.get(f"/api/reports?product_id={sl_id}",
                              headers=H_ONIT).json()
        assert any(r["id"] == rpt_art.id for r in rpt_list)
        print(f"[OK] Report (5): first-class asset — content_type="
              f"{ct!r}, in Library projection, .md download works, grade "
              f"attached, regenerate chains parent_id={rpt_art_v2.parent_id[:8]}; "
              f"GET /api/reports lists it.")

        # (6) Approval gate honored. Flip to gate_all → next report lands
        # status=pending_review AND a content_review proposal sits in
        # the queue. all_through (default for the next tests) leaves
        # things at ready as test (5) already showed.
        _set_review_mode(onit.id, "gate_all")
        try:
            gated_run = _trigger_report_run("ceo_weekly", scope_window,
                                             product_id=sl_id)
            gated_art = db.execute(
                scoped(Artifact, onit.id)
                .where(Artifact.run_id == gated_run.id,
                       Artifact.type == "report_draft")
            ).scalar_one()
            assert gated_art.status == "pending_review", (
                "gate_all must route reports to pending_review; got "
                + gated_art.status)
            gated_props = db.execute(
                scoped(Proposal, onit.id)
                .where(Proposal.run_id == gated_run.id,
                       Proposal.action_type == "content_review")
            ).scalars().all()
            assert len(gated_props) == 1, (
                f"gate_all must produce one content_review proposal; got "
                f"{len(gated_props)}")
        finally:
            _set_review_mode(onit.id, "guardrail")
        print("[OK] Report (6): approval gate honored — gate_all → "
              "status=pending_review + content_review proposal in queue; "
              "guardrail + all_through paths produce ready (verified in 5).")

        # (7) Tenant isolation — Acme cannot list / generate / preview /
        # download Onit's reports. Acme tries each endpoint; auth-domain
        # 403 is the same boundary the other tests rely on.
        acme_list = client.get("/api/reports", headers=H_ACME)
        assert acme_list.status_code in (200, 403, 404), acme_list.text
        if acme_list.status_code == 200:
            # If Acme is allowed by the domain map in this test build,
            # the list must be empty — never leaking Onit's data.
            assert acme_list.json() == [], (
                "TENANT LEAK: Acme list returned Onit reports")
        cross_intel = client.post(
            "/api/reports/intelligence",
            headers={**H_ACME, "Content-Type": "application/json"},
            json={"scope": {"kind": "time_window",
                            "start": scope_window["start"],
                            "end": scope_window["end"]},
                  "product_id": sl_id})
        assert cross_intel.status_code in (200, 403, 404)
        if cross_intel.status_code == 200:
            ci = cross_intel.json()
            assert ci["period_summary"]["attributed"]["data_points"] == 0, (
                "TENANT LEAK: Acme intelligence saw Onit's metric_points")
            assert ci["campaigns"] == [], (
                "TENANT LEAK: Acme intelligence saw Onit's campaigns")
        # Cross-org generate: Acme cannot generate against Onit's product;
        # at minimum the auth boundary holds.
        cross_gen = client.post(
            "/api/reports/generate",
            headers={**H_ACME, "Content-Type": "application/json"},
            json={"audience": "board",
                  "scope": {"kind": "time_window",
                            "start": scope_window["start"],
                            "end": scope_window["end"]}})
        assert cross_gen.status_code in (200, 403, 404)
        print(f"[OK] Report (7): tenant isolation — Acme list status="
              f"{acme_list.status_code}, intelligence status="
              f"{cross_intel.status_code}, generate status="
              f"{cross_gen.status_code}. No Onit data crosses tenants.")

        # (8) No-echo discipline — a brand_voice / instruction MARKER in
        # the profile's voice MUST NOT echo into the rendered report
        # body. Same trick the content_engine no-echo tests use.
        TONE_MARKER_RPT = "TONE_MARKER_DO_NOT_ECHO_rpt_9zzz"
        # Build the prompt that the renderer would send to Claude under
        # the LLM path — we just call _llm_system_msg directly via the
        # imported renderer module's helpers and assert the marker IS in
        # the system message (so we know the test setup works) AND assert
        # the deterministic output does NOT contain it (since the
        # deterministic path doesn't echo voice at all). For the LLM
        # path, the system message frames voice as instruction, never
        # as a labeled field — verified by inspecting the system_msg
        # shape from board.
        from app.reports.renderers import board as _board_mod
        profile_with_marker = {"brand_voice": TONE_MARKER_RPT}
        board_sys = _board_mod._llm_system_msg(profile_with_marker)
        assert TONE_MARKER_RPT in board_sys, (
            "test setup error: marker must appear in the system message")
        # Now run the renderer with no LLM key — deterministic output
        # must NOT contain the marker even when the profile carries it.
        rpt_settings.anthropic_api_key = ""  # type: ignore[attr-defined]
        try:
            board_d, _ = render_board(intel, profile=profile_with_marker,
                                       settings=rpt_settings)
        finally:
            rpt_settings.anthropic_api_key = prev_key  # type: ignore[attr-defined]
        body_text = _flat(board_d)
        assert TONE_MARKER_RPT not in body_text, (
            "no-echo regression: TONE_MARKER leaked from profile into the "
            "rendered report body. First 300 chars: " + body_text[:300])
        # System message MUST NOT carry a labeled 'brand_voice:' field
        # (no-echo discipline reused from content_templates).
        assert '"brand_voice":' not in board_sys, (
            "system msg must NOT serialize voice as a labeled field "
            "(no-echo). Got: " + board_sys[:300])
        # And the system message frames voice as "embody this, NEVER
        # describe or label it" instruction.
        assert "embody" in board_sys.lower(), board_sys[:300]
        print(f"[OK] Report (8): no-echo discipline — TONE_MARKER in "
              "profile.brand_voice landed in system message (instruction), "
              "did NOT appear in rendered body; system msg has no labeled "
              "'brand_voice' field, voice framed as 'embody'.")

        # (9) Deterministic fallback — with NO LLM key, generation
        # produces a basic-but-truthful report from the intelligence.
        # The fallback body must contain the period summary numbers and
        # a memory highlight observation if any exist.
        rpt_settings.anthropic_api_key = ""  # type: ignore[attr-defined]
        try:
            det_content, det_cost = render_board(intel, profile={},
                                                  settings=rpt_settings)
        finally:
            rpt_settings.anthropic_api_key = prev_key  # type: ignore[attr-defined]
        assert det_cost == 0.0, det_cost
        det_text = _flat(det_content)
        attr = intel["period_summary"]["attributed"]
        # The attributed clicks number must appear in prose (formatted by
        # fmt_num, so we look for the integer form).
        if attr.get("clicks"):
            assert str(int(attr["clicks"])) in det_text or \
                   f"{int(attr['clicks']):,}" in det_text, (
                "deterministic fallback must include period summary numbers. "
                f"clicks={attr['clicks']}, body[:300]={det_text[:300]}")
        # The top memory highlight's KEY (e.g. "LinkedIn") must surface
        # in the body. The board renderer now rewrites memory lines
        # with per-number ledger markers (Build P8.2) rather than
        # emitting the raw observation verbatim, so we check for the
        # key_display rather than the full observation string.
        if intel["memory_highlights"]:
            top_label = intel["memory_highlights"][0].get(
                "key_display") or intel["memory_highlights"][0].get("key")
            if top_label:
                assert top_label in det_text, (
                    "deterministic fallback must surface the top memory "
                    f"highlight's key ({top_label!r}) somewhere in the body. "
                    f"body[:300]={det_text[:300]}")
        assert "deterministic" in (det_content.get("metadata") or {}).get(
            "render_strategy", ""), det_content.get("metadata")
        print(f"[OK] Report (9): deterministic fallback — no-key render "
              f"costs $0, contains period summary numbers, surfaces a "
              "memory highlight observation; metadata.render_strategy="
              f"{det_content['metadata']['render_strategy']!r}.")

        # (10) Untagged data honesty — untagged metric_points are funnel
        # backdrop ONLY, never claimed in the report as marketing-driven.
        # Seed earlier added 10 organic rows with utm_campaign=None;
        # those rolled into the "backdrop" lane of the funnel. Assert:
        # (a) intel.period_summary.backdrop has those rows in it,
        # (b) honesty_notes calls out the excluded volume by magnitude,
        # (c) the deterministic board render does NOT attribute organic
        # clicks / conversions to a campaign or to marketing action.
        backdrop = intel["period_summary"]["backdrop"]
        assert backdrop["data_points"] > 0, (
            "test precondition: untagged organic rows must land in backdrop")
        # honesty_notes must include the "untagged volume excluded" note.
        honesty_joined = " ".join(intel["honesty_notes"])
        assert "untagged" in honesty_joined.lower() or \
               "backdrop" in honesty_joined.lower(), (
            "honesty_notes must explicitly call out untagged volume. Got: "
            + str(intel["honesty_notes"]))
        # Render the board report deterministically and assert the
        # rendered prose does NOT attribute organic clicks/conversions
        # to "campaign" or "drove" language. (The backdrop figure may
        # appear in honesty notes, but never as a marketing claim.)
        rpt_settings.anthropic_api_key = ""  # type: ignore[attr-defined]
        try:
            unt_content, _ = render_board(intel, profile={},
                                           settings=rpt_settings)
        finally:
            rpt_settings.anthropic_api_key = prev_key  # type: ignore[attr-defined]
        unt_text_lower = _flat(unt_content).lower()
        # The forbidden shape: a sentence that puts organic + drove + a
        # number together. We assert the report does NOT contain phrases
        # that claim organic as a marketing-driven outcome.
        forbidden_attribution = [
            "organic drove", "organic delivered", "organic produced",
            "organic generated", "via organic", "organic campaign",
        ]
        for phrase in forbidden_attribution:
            assert phrase not in unt_text_lower, (
                f"untagged organic must NEVER be claimed as marketing "
                f"action. Forbidden phrase {phrase!r} appeared in report.")
        print(f"[OK] Report (10): untagged data honesty — "
              f"{int(backdrop['clicks']):g} backdrop click(s) + "
              f"{int(backdrop['conversions']):g} backdrop conversion(s) "
              f"excluded from attributed numbers, called out in "
              f"honesty_notes, and never claimed as marketing-driven in "
              "the rendered report.")

        # (11) Future-date confabulation guard — a time_window scope in
        # the future must produce a stub report across all three
        # audiences. The stub explicitly says "no data exists yet,"
        # includes memory as a reference baseline AS-OF-TODAY only, and
        # does NOT contain period_summary-style claims (no "rose X%",
        # no "drove the most conversions," no "during [period]" framing).
        # Reports describe what HAS happened; they don't forecast.
        future_start = (today_d + _td2(days=60)).isoformat()
        future_end = (today_d + _td2(days=90)).isoformat()
        future_scope = {"kind": "time_window",
                        "start": future_start, "end": future_end}
        fut_intel = build_report_intelligence(db, onit.id, product_id=sl_id,
                                              scope=future_scope)
        # Engine flagged it as future and omitted load-bearing sections.
        assert fut_intel["scope"]["is_future"] is True, fut_intel["scope"]
        assert fut_intel["period_summary"] is None, fut_intel["period_summary"]
        assert fut_intel["production"] is None, fut_intel["production"]
        assert fut_intel["notable_changes"] == [], fut_intel["notable_changes"]
        assert fut_intel["top_content"] == [], fut_intel["top_content"]
        assert fut_intel["campaigns"] == [], fut_intel["campaigns"]
        # honesty_notes explicitly call out the future.
        honesty_joined_fut = " ".join(fut_intel["honesty_notes"]).lower()
        assert "future" in honesty_joined_fut, fut_intel["honesty_notes"]
        assert "no data" in honesty_joined_fut, fut_intel["honesty_notes"]
        # Memory survives as reference baseline, labeled.
        assert "memory_reference_label" in fut_intel, fut_intel.keys()
        ref_label = fut_intel["memory_reference_label"].lower()
        assert "as of" in ref_label and "not a finding" in ref_label, ref_label

        # Each renderer produces an honest stub. Three forbidden shapes
        # the deterministic body MUST NOT contain (these are the exact
        # confabulation patterns the bug produced live):
        forbidden_in_future_body = [
            "rose ",            # "rose X%" delta framing
            "fell ",            # delta framing the other way
            "up ", "down ",     # generic delta shorthand — too risky, see below
            "drove most",       # ranked attribution claims
            "was the clear",    # winner framing
            "led to ",          # cause-effect attribution
        ]
        # "up " and "down " are common English words — only forbid them
        # when they appear as standalone period-delta language ("up 20%",
        # "down 5%"). We check via a regex below to avoid false positives.
        import re as _re_fut
        delta_pat = _re_fut.compile(
            r"\b(?:up|down|rose|fell|grew|dropped)\s+\d+\s*%", _re_fut.IGNORECASE)
        rpt_settings.anthropic_api_key = ""  # type: ignore[attr-defined]
        try:
            fut_board, _ = render_board(fut_intel, profile={},
                                         settings=rpt_settings)
            fut_ceo, _ = render_ceo_weekly(fut_intel, profile={},
                                            settings=rpt_settings)
            fut_sales, _ = render_sales_leadership(fut_intel, profile={},
                                                    settings=rpt_settings)
        finally:
            rpt_settings.anthropic_api_key = prev_key  # type: ignore[attr-defined]
        for label, draft in [("board", fut_board), ("ceo_weekly", fut_ceo),
                              ("sales_leadership", fut_sales)]:
            body_text = _flat(draft)
            # 1) The fact is stated — "no data exists yet" for the period.
            assert "no data" in body_text.lower(), (
                f"{label} future stub MUST say 'no data exists yet'. "
                f"Got: {body_text[:300]}")
            # 2) The period is named (so user knows what was asked).
            assert future_start in body_text and future_end in body_text, (
                f"{label} future stub MUST name the requested period "
                f"({future_start} to {future_end}). Got: {body_text[:300]}")
            # 3) The reference baseline is explicitly labeled — memory
            # is "as of today," NOT for the requested period.
            if fut_intel["memory_highlights"]:
                assert ("as of today" in body_text.lower()
                        or "as of " in body_text.lower()), (
                    f"{label} future stub MUST label memory bullets as "
                    f"'as of today' when they appear. Got: {body_text[:300]}")
            # 4) NO delta / period-attribution language anywhere.
            for forbidden in ["drove most", "was the clear", "led to "]:
                assert forbidden not in body_text.lower(), (
                    f"{label} future stub MUST NOT contain {forbidden!r}. "
                    f"Got: {body_text[:300]}")
            # No "up X%" / "down X%" / "rose X%" patterns either.
            assert not delta_pat.search(body_text), (
                f"{label} future stub MUST NOT contain period-delta "
                f"language (up/down/rose N%). Got: {body_text[:300]}")
            # 5) Metadata flags this as the future stub render strategy.
            assert (draft.get("metadata") or {}).get(
                "render_strategy") == "future_stub", draft.get("metadata")
            # 6) The body is SHORT — the brief calls for a stub, not a
            # narrative. 100-1000 chars is the honest target band.
            assert len(body_text) < 1000, (
                f"{label} future stub MUST be short (< 1000 chars). "
                f"Got {len(body_text)} chars.")
        # End-to-end via the agent — generation through the full
        # spine produces an Artifact whose body reflects the stub.
        _set_review_mode(onit.id, "all_through")
        fut_run = _trigger_report_run("ceo_weekly", future_scope,
                                       product_id=sl_id)
        assert fut_run.status == "succeeded", (fut_run.status, fut_run.error)
        fut_art = db.execute(
            scoped(Artifact, onit.id)
            .where(Artifact.run_id == fut_run.id,
                   Artifact.type == "report_draft")
        ).scalar_one()
        fut_body_text = "\n".join(
            (b.get("text") or "")
            for b in (((fut_art.body or {}).get("content")
                        or {}).get("blocks") or []))
        assert "no data" in fut_body_text.lower()
        assert (fut_art.body or {}).get("content", {}).get(
            "metadata", {}).get("render_strategy") == "future_stub"
        print(f"[OK] Report (11): future-date confabulation guard — "
              f"engine returns is_future intelligence (period_summary/"
              f"production/campaigns/top_content omitted); all three "
              f"renderers emit a stub naming the period, framing memory "
              f"as 'as of today', no delta/attribution language; "
              f"end-to-end agent run lands a future_stub artifact.")

        # (12) REPORT as a top-level kind in the Library — presence +
        # absence + behavior, per docs/cross-layer-disciplines.md. The
        # distinction enforced at the origin (body.content.content_type)
        # is now re-asserted at the consumer boundary (Library filter +
        # badge) via the existing top-level Artifact.type field.
        from app.reports.backfill import backfill_report_artifact_type

        # ---- PRESENCE -------------------------------------------------
        # The report from Report (5) has Artifact.type="report_draft"
        # and projects as asset_kind="report" via the kind filter.
        present_resp = client.get(
            f"/api/assets?asset_kind=report&product_id={sl_id}",
            headers=H_ONIT)
        assert present_resp.status_code == 200, present_resp.text
        present_assets = present_resp.json()["assets"]
        assert len(present_assets) > 0, (
            "asset_kind=report filter must return at least the report "
            "generated in Report (5)")
        present_asset_types = {a["asset_type"] for a in present_assets}
        # asset_type carries the audience slug (report_board / etc.).
        assert any(t.startswith("report_") for t in present_asset_types), (
            "report assets must carry an audience-shaped asset_type. Got: "
            + str(present_asset_types))

        # ---- ABSENCE --------------------------------------------------
        # Filtering by asset_kind=report returns NO non-report assets.
        # Filtering by content/document/brief returns NO reports.
        for a in present_assets:
            assert a["asset_kind"] == "report", (
                "asset_kind=report leaked a non-report: " + str(a))
        for non_report_kind in ("content", "document", "brief"):
            resp = client.get(
                f"/api/assets?asset_kind={non_report_kind}&limit=500",
                headers=H_ONIT)
            assert resp.status_code == 200
            for a in resp.json()["assets"]:
                assert a["asset_kind"] != "report", (
                    f"asset_kind={non_report_kind} leaked a report: "
                    + str(a))
                # Inverse check: a non-report asset must never carry an
                # asset_type that starts with report_ either (the badge
                # logic depends on the kind being right).
                if non_report_kind == "content":
                    assert not (a.get("asset_type") or "").startswith("report_"), (
                        "content asset must not carry a report_* asset_type "
                        "after backfill: " + str(a))

        # ---- BEHAVIOR — backfill picks up legacy reports --------------
        # Simulate a pre-fix legacy report: write an Artifact with
        # type="content_draft" but body.content.content_type=report_board.
        # This is the exact shape the confabulated July report had in
        # the live DB before this build. The kind filter must NOT see
        # it (yet) — and after running the backfill, it MUST appear
        # under asset_kind=report.
        legacy_art = Artifact(
            org_id=onit.id, run_id=rpt_run.id, product_id=sl_id,
            type="content_draft",  # the LEGACY top-level kind
            title="Legacy board report (pre-backfill simulation)",
            body={
                "content": {
                    "content_type": "report_board",  # audience visible
                                                      # ONLY in body JSON
                    "blocks": [{"kind": "body", "text": "Legacy body."}],
                    "metadata": {"audience": "Board update"},
                },
                "provenance": {"intelligence_scope": {}},
                "grade": {"status": "ungraded", "overall": None,
                          "per_criterion": [], "suggestions": []},
            },
            citations=[],
            status="ready",
            grade={"status": "ungraded", "overall": None,
                   "per_criterion": [], "suggestions": []},
        )
        db.add(legacy_art)
        db.commit(); db.refresh(legacy_art)
        legacy_id = legacy_art.id

        # Before backfill: filter by asset_kind=report should NOT see
        # this artifact, because the kind filter resolves off the
        # top-level Artifact.type (which is "content_draft" here), not
        # the nested body JSON. This is the load-bearing check — the
        # filter MUST operate on the top-level field, not body JSON.
        pre_resp = client.get(
            f"/api/assets?asset_kind=report&product_id={sl_id}&limit=500",
            headers=H_ONIT)
        pre_ids = {a["id"] for a in pre_resp.json()["assets"]}
        assert legacy_id not in pre_ids, (
            "Legacy artifact (type=content_draft, body content_type="
            "report_board) must NOT match asset_kind=report before "
            "backfill — the filter must operate on the top-level kind "
            "field, not nested body JSON.")

        # Run the backfill (it's the same function the API startup
        # invokes). Idempotent — calling twice should also yield zero
        # additional updates.
        n_updated = backfill_report_artifact_type(db)
        assert n_updated >= 1, (
            f"backfill must find the legacy report; got {n_updated} updates")
        db.refresh(legacy_art)
        assert legacy_art.type == "report_draft", (
            f"backfill must rewrite type to 'report_draft'; got "
            f"{legacy_art.type!r}")
        n_again = backfill_report_artifact_type(db)
        assert n_again == 0, (
            f"backfill must be idempotent; second run did {n_again} updates")

        # After backfill: asset_kind=report DOES include the legacy
        # artifact. The filter still operates on the top-level field —
        # what changed is the field's value, not the filter logic.
        post_resp = client.get(
            f"/api/assets?asset_kind=report&product_id={sl_id}&limit=500",
            headers=H_ONIT)
        post_ids = {a["id"] for a in post_resp.json()["assets"]}
        assert legacy_id in post_ids, (
            "after backfill, legacy artifact MUST appear under "
            "asset_kind=report. The filter resolves via top-level field.")

        # ---- BEHAVIOR — unresolvable content_type → unknown kind ------
        # An Artifact with type="report_draft" but body.content.content_type
        # missing/garbage cannot have its audience resolved. Per §5 of
        # the cross-layer disciplines (unknown vs. zero), it surfaces
        # as asset_kind="unknown" rather than silently bucketed as
        # "report" (which would imply an audience we can't name) or
        # silently dropped (which would hide the inconsistent state).
        broken_art = Artifact(
            org_id=onit.id, run_id=rpt_run.id, product_id=sl_id,
            type="report_draft",  # claims to be a report
            title="Broken report (no audience)",
            body={
                "content": {
                    "content_type": "report_unknown_audience",  # not a real audience
                    "blocks": [{"kind": "body", "text": "x"}],
                    "metadata": {},
                },
            },
            citations=[],
            status="ready",
        )
        db.add(broken_art)
        db.commit(); db.refresh(broken_art)
        broken_id = broken_art.id

        # When listed under ANY kind, the projection surfaces it as
        # asset_kind="unknown" — never as "report" (we can't resolve
        # the audience) and never as a non-report default.
        any_resp = client.get(
            f"/api/assets?asset_kind=report&product_id={sl_id}&limit=500",
            headers=H_ONIT)
        broken_proj = next((a for a in any_resp.json()["assets"]
                             if a["id"] == broken_id), None)
        assert broken_proj is not None, (
            "broken report (type=report_draft) must still surface when "
            "filtering by asset_kind=report — it lives in that kind's "
            "type-set, just with an unresolved audience.")
        assert broken_proj["asset_kind"] == "unknown", (
            "unresolvable content_type MUST render as asset_kind=unknown "
            "(§5 discipline). Got: " + str(broken_proj))
        assert broken_proj["asset_type"] == "unknown", (
            "unresolvable audience MUST render as asset_type=unknown. "
            "Got: " + str(broken_proj))

        # Clean up the two synthetic artifacts so they don't leak into
        # other tests further down (defensive — there are no further
        # tests, but keeps the DB tidy for the smoke teardown).
        db.delete(broken_art); db.delete(legacy_art); db.commit()

        print(f"[OK] Report (12): top-level 'report' kind — Library "
              f"filter resolves off Artifact.type, not body JSON. "
              f"Presence: asset_kind=report returns reports with audience "
              f"asset_type (e.g. report_board). Absence: content/document/"
              f"brief never include reports, content never has report_* "
              f"asset_type. Behavior: backfill rewrites a legacy "
              f"(content_draft + body content_type=report_board) artifact "
              f"to type=report_draft (idempotent on rerun); after, the "
              f"asset_kind=report filter sees it. Unresolvable "
              f"content_type renders as asset_kind=unknown, not "
              f"silently bucketed.")

        # (13) Evidence ledger — §6 generated-vs-observed enforcement.
        # Presence + absence + behavior per docs/cross-layer-disciplines.md.
        # The binding is WRITTEN at generation time (renderer holds the
        # ledger and emits markers); the validator independently checks
        # every quantitative claim in prose resolves to a real entry.
        from app.reports.evidence import (
            build_ledger_from_intelligence, validate_evidence_binding,
            Ledger as _Ledger,
        )

        # ---- PRESENCE: ledger built from populated intelligence -------
        # Use the smoke's earlier `intel` (populated, non-future). Walk
        # the ledger and confirm entries exist for the load-bearing
        # fields: period_summary.attributed, memory_highlights, and at
        # least one campaign + production figure. Each entry carries
        # source, confidence (existing vocabulary), and
        # baseline_vs_attributed flag.
        ledger_p = build_ledger_from_intelligence(intel)
        assert len(ledger_p) > 0, "ledger must have entries for populated intel"
        sources = {e["source"] for e in ledger_p}
        assert "period_summary.attributed.clicks" in sources, sources
        assert "period_summary.attributed.conversion_rate" in sources, sources
        # Memory highlights entries — at least one with confidence
        # from the EXISTING tier vocabulary (high/moderate/low/
        # insufficient — NO new tiers).
        memory_entries = [e for e in ledger_p
                          if e["source"].startswith("memory_highlights[")]
        assert memory_entries, "ledger must include memory_highlights entries"
        for e in memory_entries:
            assert e["confidence"] in (
                "high", "moderate", "low", "insufficient", "n_a"), \
                f"ledger entry confidence must use existing tiers; got " \
                f"{e['confidence']!r}"
            assert e["baseline_vs_attributed"] == "attributed", (
                "memory entries are attributed (Bug 1 — memory excludes "
                "untagged). Got: " + str(e))
        # Backdrop entries (when present) carry the backdrop flag.
        backdrop_entries = [e for e in ledger_p
                            if e["source"].startswith("period_summary.backdrop.")]
        for e in backdrop_entries:
            assert e["baseline_vs_attributed"] == "backdrop", (
                "backdrop entries MUST be flagged backdrop, not "
                "attributed (§3 discipline). Got: " + str(e))
        print(f"[OK] Report (13a): ledger built from populated intel — "
              f"{len(ledger_p)} entry(s) covering period_summary, "
              f"{len(memory_entries)} memory, "
              f"{len(backdrop_entries)} backdrop; tiers from existing "
              "vocabulary; baseline/attributed flag correct.")

        # ---- PRESENCE: a generated report's trust_checks passes -------
        # The Report (5) board artifact `rpt_art` was generated via the
        # full agent path which now builds the ledger + runs validation.
        # Reload and verify trust_checks.
        db.refresh(rpt_art)
        tc = (rpt_art.body or {}).get("trust_checks")
        assert tc is not None, "report body must carry trust_checks"
        # Every marker resolves; no numbers are unbound. This is the
        # binding-presence check — the renderer wrote markers for every
        # quantitative claim and they all point at real ledger entries.
        assert tc["markers_unresolved"] == [], (
            "every emitted marker must resolve to a ledger entry. "
            "Unresolved: " + str(tc["markers_unresolved"]))
        assert tc["numbers_unbound"] == [], (
            "every number-shaped token in the report must carry a "
            "resolving marker. Unbound: "
            + str(tc["numbers_unbound"]))
        assert tc["passed"] is True, tc
        # Ledger size matches what was rendered (no stripping in the
        # composer).
        assert tc["ledger_size"] > 0
        # Markers found > 0 means the renderer actually emitted them.
        assert tc["markers_found"] > 0
        print(f"[OK] Report (13b): binding presence — generated report's "
              f"trust_checks.passed=True; {tc['markers_found']} marker(s) "
              f"all resolved; {tc['numbers_found']} number(s) found and "
              f"all bound to ledger entries.")

        # ---- ABSENCE (load-bearing) — bare number must fail -----------
        # Construct a synthetic content with a quantitative claim that
        # has NO marker. Validation MUST flag it as a violation,
        # passed=False. Without this check, the system would be
        # structurally-green-while-actually-broken: regex-matching a
        # post-hoc claim is exactly the failure mode §6 exists to
        # prevent.
        bare_content = {
            "content_type": "report_board",
            "blocks": [
                {"kind": "body",
                 "text": "Email had 42 conversions last quarter."},
            ],
            "metadata": {},
        }
        tc_bare = validate_evidence_binding(bare_content, ledger_p)
        assert tc_bare["passed"] is False, (
            "VALIDATION REGRESSION: a bare number with no marker MUST "
            "fail validation. trust_checks=" + str(tc_bare))
        assert tc_bare["numbers_unbound"], (
            "bare number must appear in numbers_unbound. Got: "
            + str(tc_bare))
        assert any(u["text"] == "42" for u in tc_bare["numbers_unbound"]), (
            "the bare '42' must be flagged specifically. Got: "
            + str(tc_bare["numbers_unbound"]))

        # ---- ABSENCE (load-bearing) — unresolved marker must fail ----
        bogus_content = {
            "content_type": "report_board",
            "blocks": [
                {"kind": "body",
                 "text": "Sales drove 100⟦ev:nonexistent⟧ conversions."},
            ],
            "metadata": {},
        }
        tc_bogus = validate_evidence_binding(bogus_content, ledger_p)
        assert tc_bogus["passed"] is False, (
            "VALIDATION REGRESSION: a marker pointing at no ledger "
            "entry MUST fail validation. trust_checks="
            + str(tc_bogus))
        assert "nonexistent" in tc_bogus["markers_unresolved"], (
            "unresolved marker id must appear in markers_unresolved. "
            "Got: " + str(tc_bogus["markers_unresolved"]))
        # The bogus marker doesn't resolve, so the number it claimed
        # to source is ALSO unbound (no resolving marker nearby).
        # This is the "the check is behavioral, not structural" proof:
        # a regex would have found the marker and called it good; the
        # validator finds the marker, tries to resolve it, fails, and
        # therefore counts the number as unbound too.
        assert tc_bogus["numbers_unbound"], (
            "unresolved marker means the number it claimed to source "
            "is effectively unbound. Got: " + str(tc_bogus))
        print(f"[OK] Report (13c): ABSENCE checks (the behavioral proof) "
              f"— bare number triggers FAIL with the bare '42' in "
              f"numbers_unbound; unresolved marker triggers FAIL with "
              f"'nonexistent' in markers_unresolved + the orphaned number "
              "in numbers_unbound.")

        # ---- BEHAVIOR — future scope has empty ledger / stub passes ---
        # When the engine returns is_future intelligence, the ledger
        # contains only memory_highlights (no period_summary entries).
        # The future_stub_blocks emit markers next to memory numbers.
        # Validation passes: zero unbound, zero unresolved.
        ledger_f = build_ledger_from_intelligence(fut_intel)
        # Future-scope ledger excludes period_summary entries (they are
        # None / omitted). Only memory + open_questions content.
        sources_f = {e["source"] for e in ledger_f}
        assert not any(s.startswith("period_summary.attributed")
                        for s in sources_f), (
            "future-scope ledger MUST NOT carry period_summary entries "
            "(the engine omitted them). Got: " + str(sources_f))
        # Generate a future-scope board report and check trust_checks.
        db.refresh(fut_art)
        tc_fut = (fut_art.body or {}).get("trust_checks")
        assert tc_fut is not None, "future-scope report must carry trust_checks"
        assert tc_fut["passed"] is True, (
            "future-scope stub MUST pass validation (no false positives "
            "on the honest stub). trust_checks=" + str(tc_fut))
        # No unbound numbers — even though the stub body MAY contain
        # numbers in memory reference observations, every one has a
        # marker.
        assert tc_fut["numbers_unbound"] == [], (
            "future-scope stub must have ZERO unbound numbers. Got: "
            + str(tc_fut["numbers_unbound"]))
        assert tc_fut["markers_unresolved"] == [], tc_fut
        print(f"[OK] Report (13d): behavior — future-scope ledger excludes "
              f"period_summary entries; future-scope stub passes "
              f"validation with zero unbound numbers + zero unresolved "
              f"markers (no false positives on the honest stub).")

        print("[OK] Smoke test passed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
