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
from app.reports.renderers import RENDERERS
from app.schemas import (AgentContext, AgentResult, ArtifactDraft, Citation,
                         ProposedAction)


_GATE_ALL = "gate_all"
_GUARDRAIL = "guardrail"
_ALL_THROUGH = "all_through"


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

        renderer = RENDERERS[audience]
        content, gen_cost = renderer(intelligence,
                                     profile=profile, settings=settings)

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
        # comes from content.content_type (report_<audience>).
        audience_titles = {
            "board": "Board update",
            "ceo_weekly": "CEO weekly digest",
            "sales_leadership": "Sales leadership update",
        }
        title = (f"{audience_titles.get(audience, audience.title())} — "
                 f"{scope_label}")

        # Reports are first-class assets: top-level Artifact.type is
        # "report_draft" so the Library kind filter resolves off the
        # same field every other kind uses (content/document/brief).
        # The block-based body shape is reused — reports inherit the
        # .md download, grade pill, and approval gate that content has,
        # for free. Audience (board / ceo_weekly / sales_leadership)
        # still lives in body.content.content_type for badge text.
        art = ArtifactDraft(
            type="report_draft",
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
