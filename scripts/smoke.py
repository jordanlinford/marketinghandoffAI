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
atexit.register(lambda: shutil.rmtree(_SMOKE_DB_DIR, ignore_errors=True))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.agents import content_grader as content_grader_mod  # noqa: E402
from app.agents import content_templates as content_templates_mod  # noqa: E402
from app.agents import synthesis as synthesis_mod  # noqa: E402
from app.data_sources.csv import CsvMarketDataSource  # noqa: E402
from app.db import SessionLocal, create_all  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (AgentRegistration, Artifact, Guardrail, Org,  # noqa: E402
                        OrgProfile, Proposal, Run, Upload, User)
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
    def _stub_content_llm(content_type, profile, brief, topic, target, settings,
                          critique=""):
        topic_for_template = (f"{topic} — addressing: {critique}"
                              if critique else topic)
        return content_templates_mod._REGISTRY[content_type](
            profile, brief, topic_for_template, target), 0.0007
    content_templates_mod._llm_build = _stub_content_llm

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
        # and competitors=[{"name":"SimpleLegal"}] — both must surface.
        assert "Plain, confident, no jargon" in body_text, \
            f"brand_voice not reflected in draft: {body_text!r}"
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
              f"{len(blocks)} ordered blocks, brand_voice + competitors reflected, "
              f"cost=${gen_run.cost_usd:.4f}.")

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

        print("[OK] Smoke test passed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
