"""
report_composer — the THIRD action-taking agent (after content_engine
and market_intel). Its job is small and well-defined:

  1. Read the pre-computed ReportIntelligence object the worker handed
     it via ctx.report_intelligence. (The agent itself never queries the
     DB — same chassis discipline as every other agent.)
  2. Dispatch to the audience-specific renderer.
  3. Route the resulting draft through the SAME spine content_engine
     uses: guardrail evaluation, content_review_mode decision (gate_all
     / guardrail / all_through), advisory grade, and proposal creation
     when the gate fires.

Reports are first-class Artifacts with type="content_draft" so they
inherit Library projection, .md download, grading, and the parent_id
versioning chain. The content_type lives in body.content.content_type
(report_board / report_ceo_weekly / report_sales_leadership) — the
Library uses that to badge reports as a sub-kind of content.

Routing precedence — IDENTICAL to content_engine:
  - all_through → status=ready, NO proposal.
  - guardrail  → ready unless banned-claim trips.
  - gate_all   → every draft queued.

Approval here means "draft marked ready," NOT shared — a report
becoming a board slide is a separate, always-human action.
"""
from __future__ import annotations

from app import guardrails as guardrails_mod
from app.agents.base import Agent
from app.agents.content_grader import grade_content
from app.agents.registry import register
from app.reports.evidence import (build_ledger_from_intelligence,
                                   trust_checks_with_findings,
                                   validate_evidence_binding)
from app.reports.renderers import RENDERERS
from app.schemas import (AgentContext, AgentResult, ArtifactDraft, Citation,
                         ProposedAction)


_GATE_ALL = "gate_all"
_GUARDRAIL = "guardrail"
_ALL_THROUGH = "all_through"


# Brand-token shape on body.brand_tokens. Keep this in lock-step with
# app/api/brand.py's _DEFAULT_BRAND — the keys are what the UI chrome
# layer reads. The agent picks tokens off ctx.brand by NAME (not by
# blind copy) so a future field on OrgBrand can't accidentally leak
# into the body without an explicit decision here.
_BRAND_BODY_KEYS = (
    "color_primary", "color_secondary", "color_accent",
    "color_background", "color_text",
    "font_heading", "font_body",
    "logo_path", "logo_mime",
)


def _brand_tokens_for_body(brand: dict) -> dict:
    """Verbatim pass-through of presentation tokens from ctx.brand.
    Returns a fresh dict with only the explicit chrome keys (no
    `is_default`/`org_id`/timestamps). The renderer never sees this —
    it's attached AFTER rendering, alongside content/trust_checks/etc.
    PRESENTATION ONLY: callers must not feed these into prompts,
    selection logic, ledger entries, or any §6/§7 validator input."""
    return {k: brand.get(k) for k in _BRAND_BODY_KEYS}


@register
class ReportComposerAgent(Agent):
    key = "report_composer"
    display_name = "Report composer"

    def run(self, ctx: AgentContext) -> AgentResult:
        log = ctx.log
        task = ctx.task or {}
        audience = (task.get("audience") or "").strip().lower()
        if audience not in RENDERERS:
            raise RuntimeError(
                f"report_composer: unknown audience {audience!r} "
                f"(available: {sorted(RENDERERS.keys())}).")

        # The worker pre-loaded the intelligence object onto ctx. The
        # agent never queries the DB — that's the chassis contract.
        intelligence = ctx.report_intelligence or {}
        if not intelligence:
            raise RuntimeError(
                "report_composer: ctx.report_intelligence is empty. The "
                "worker is responsible for pre-loading it via "
                "build_report_intelligence() based on the task's scope.")

        log(f"report_composer audience={audience} for {ctx.org_name}")
        profile = ctx.profile or ctx.org_profile or {}

        # Settings — used by the renderer to decide LLM vs deterministic
        # fallback. We import locally so the chassis import surface stays
        # the same as other agents (no settings on the AgentContext
        # contract).
        from app.config import get_settings
        settings = get_settings()

        # Build the evidence ledger from the SAME intelligence the
        # renderer will read (§6 generated-vs-observed: the binding is
        # written, not reverse-engineered). The renderer receives the
        # ledger as input and emits markers per number; the validation
        # pass below then reads the SAME ledger and the SAME rendered
        # content. Same source of truth, end to end.
        ledger = build_ledger_from_intelligence(intelligence)

        renderer = RENDERERS[audience]
        content, gen_cost = renderer(intelligence,
                                     profile=profile, settings=settings,
                                     ledger=ledger)

        # Deterministic post-render validation + severity classification.
        # The validator scans prose per-block (so findings carry
        # locations); the classifier maps detections to severity tiers
        # and emits the gate signal (`approval_blocked` is True iff any
        # critical finding fired). The tier mapping is FIXED:
        #   CRITICAL §6 = unsourced quantitative claim, broken marker.
        #   CRITICAL §4 = delta/attribution language on a future scope.
        #   WARNING  §1 = block whose every cited entry is at low or
        #                 insufficient confidence (thin evidence; never
        #                 blocks the gate).
        # See docs/cross-layer-disciplines.md for the underlying rules.
        trust_checks = validate_evidence_binding(content, ledger)
        trust_checks = trust_checks_with_findings(
            trust_checks, ledger.to_list(), content,
            scope=intelligence.get("scope") or {})
        approval_blocked = bool(trust_checks.get("approval_blocked"))

        # ---- Guardrail + routing — same shape as content_engine ----------
        flat_text = "\n".join((b.get("text") or "")
                              for b in content.get("blocks", []))
        mode = (profile.get("content_review_mode") or _GUARDRAIL).lower()
        probe = ProposedAction(
            action_type="content_review",
            payload={"content_type": content.get("content_type"),
                     "topic": f"{audience} report",
                     "text": flat_text},
            guardrail_scope="content",
            reasoning=f"report ({audience}) routing — content_review pre-check.")
        gstatus, gdetail = guardrails_mod.evaluate(
            probe, ctx.guardrail_rules or {})

        if mode == _GATE_ALL:
            needs_review, why = True, "Org policy: every draft reviewed (gate_all)."
        elif mode == _ALL_THROUGH:
            needs_review, why = False, "Org policy: drafts auto-ready (all_through)."
        else:
            needs_review = (gstatus == "blocked")
            why = gdetail
        # Evidence severity gate: any CRITICAL finding (unsourced
        # quantitative claim, broken marker, or future-date confabulation)
        # blocks auto-ready regardless of content_review_mode. The
        # existing approval queue handles it from there — no new gate,
        # no new route, just an extra reason the existing one fires.
        # all_through still applies as a normal posture, but a fabricated
        # claim is not a posture decision.
        if approval_blocked and not needs_review:
            needs_review = True
            n_critical = trust_checks["findings_by_severity"]["critical"]
            why = (f"{n_critical} critical evidence finding(s): approval "
                   f"blocked until reviewed. See body.trust_checks.findings "
                   f"for the per-claim breakdown.")
        artifact_status = "pending_review" if needs_review else "ready"

        # ---- Advisory grade ---------------------------------------------
        # Reports go through the same grader as content. The advisory
        # rubric in DEFAULT_RUBRIC reads sensibly enough for reports
        # (on-brand, on-strategy, clarity, specificity, no banned claims).
        grade, grade_cost = grade_content(content, profile)
        total_cost = round(gen_cost + grade_cost, 6)

        # ---- Body assembly — same shape content_engine produces ---------
        scope_info = intelligence.get("scope") or {}
        scope_label = (
            f"campaign={scope_info.get('campaign_name')}"
            if scope_info.get("kind") == "campaign"
            else f"{scope_info.get('start')} → {scope_info.get('end')}"
        )
        body = {
            "content": content,
            "provenance": {
                "org_profile": bool(ctx.org_profile),
                "memory_patterns_seen": len(intelligence.get("memory_highlights") or [])
                                          + len(intelligence.get("watching") or []),
                "intelligence_scope": scope_info,
                "render_strategy": (content.get("metadata", {}) or {}).get(
                    "render_strategy"),
            },
            "routing": {
                "mode": mode, "guardrail_status": gstatus,
                "guardrail_detail": gdetail, "needs_review": needs_review,
                "artifact_status": artifact_status, "why": why,
            },
            "version": 1,
            "parent_artifact_id": task.get("parent_artifact_id"),
            "critique": (task.get("critique") or "").strip(),
            "grade": grade,
            "cost_breakdown": {"generation": gen_cost, "grading": grade_cost},
            # The intelligence object the renderer used — kept here so
            # the UI can show "what the report was based on" without a
            # second engine call. JSON-serializable shape.
            "intelligence_used": intelligence,
            # Evidence ledger + post-render validation result. The
            # ledger is what every claim is supposed to cite from; the
            # trust_checks result records pass/fail. RECORDING ONLY in
            # this build — the gate enforcement lands in the next UI
            # build. Trust pill in the UI reads off body.trust_checks.
            "evidence_ledger": ledger.to_list(),
            "trust_checks": trust_checks,
            # Tenant brand tokens — PRESENTATION ONLY. The renderer was
            # never handed brand (it's not in scope), so this is a pure
            # post-render attachment that wraps the rendered content for
            # the UI chrome layer. Smoke test #18c pins the invariant:
            # body.content.blocks and body.trust_checks are byte-
            # identical regardless of brand state. Don't move this key
            # under body["content"] — that's the trust-tested object,
            # and brand is presentation, not content.
            "brand_tokens": _brand_tokens_for_body(ctx.brand or {}),
        }

        cites: list[Citation] = []
        # period_summary is None for future-date intelligence — guard
        # against None.get() since the citation is just a hint, not
        # load-bearing for the body.
        ps = intelligence.get("period_summary") or {}
        attr = (ps.get("attributed") if isinstance(ps, dict) else {}) or {}
        cites.append(Citation(
            source="Substrate (metric_points)",
            snippet=(f"{attr.get('data_points', 0)} "
                     "attributed data point(s) aggregated for this scope.")))
        if intelligence.get("memory_highlights"):
            cites.append(Citation(
                source="Memory highlights",
                snippet=f"{len(intelligence['memory_highlights'])} pattern(s) "
                        "at moderate-or-higher confidence."))
        if intelligence.get("campaigns"):
            cites.append(Citation(
                source="Campaigns",
                snippet=f"{len(intelligence['campaigns'])} campaign(s) in scope."))
        if intelligence.get("honesty_notes"):
            cites.append(Citation(
                source="Honesty notes",
                snippet=f"{len(intelligence['honesty_notes'])} note(s) about "
                        "data gaps and untagged volume."))

        # Title is human-readable for the Library list. The Library badge
        # comes from content.content_type (report_<audience>) for
        # audience reports, or content.content_type='whitepaper' for
        # anchor-class outputs.
        audience_titles = {
            "board": "Board update",
            "ceo_weekly": "CEO weekly digest",
            "sales_leadership": "Sales leadership update",
            "whitepaper": "White paper",
            "buyer_guide": "Buyer's guide",
            "solution_guide": "Solution guide",
        }
        title = (f"{audience_titles.get(audience, audience.title())} — "
                 f"{scope_label}")

        # Anchor-vs-report kind promotion — same mechanism we used to
        # promote reports out of content_draft. Artifact.type drives
        # the Library kind filter (see app/api/assets.py). Each new
        # anchor class registers ONE entry here: audience name (the
        # RENDERERS key) → Artifact.type. The agent never branches on
        # content_type string inspection — that lives in the
        # downstream projection layer and reads off Artifact.type.
        _ANCHOR_TYPES = {
            "whitepaper":     "whitepaper_draft",
            "buyer_guide":    "buyer_guide_draft",
            "solution_guide": "solution_guide_draft",
        }
        artifact_type = _ANCHOR_TYPES.get(audience, "report_draft")
        art = ArtifactDraft(
            type=artifact_type,
            title=title, body=body, citations=cites,
            status=artifact_status,
            parent_id=task.get("parent_artifact_id"),
            grade=grade,
        )

        proposals: list[ProposedAction] = []
        if needs_review:
            proposals.append(ProposedAction(
                action_type="content_review",
                payload={
                    "content_type": content.get("content_type"),
                    "topic": f"{audience} report",
                    "blocks": content.get("blocks", []),
                    "text": flat_text,
                    "review_mode": mode,
                    "version": 1,
                    "parent_artifact_id": task.get("parent_artifact_id"),
                },
                guardrail_scope="content",
                reasoning=f"{audience} report routed for human review — {why}",
            ))

        return AgentResult(
            artifacts=[art], proposed_actions=proposals,
            cost_usd=total_cost, logs=[
                f"render strategy: {(content.get('metadata', {}) or {}).get('render_strategy', 'deterministic')}",
                f"routing: mode={mode} status={artifact_status} why={why}",
            ])
