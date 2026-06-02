"""
derivative_composer — the FOURTH action-taking agent. Produces a
Level-4 derivative (today: executive summary) from a Level-3 anchor.

Architectural contract (mirrors report_composer's chassis discipline):
  1. The agent reads ctx.source_anchor — a dict pre-loaded by the
     worker via scoped(). It NEVER queries the DB and NEVER reaches
     past the anchor to the intelligence engine or the ledger builder.
  2. It dispatches to a renderer in DERIVATIVE_RENDERERS by
     task.derivative_type.
  3. It runs containment validation against the anchor's ledger,
     classifies into §7 findings, and routes through the SAME gate
     mechanism the anchor pipeline uses (content_review_mode +
     guardrail evaluation + approval_blocked).
  4. The artifact persists as `<derivative>_draft` (exec_summary_draft
     today) — same kind-promotion mechanism the anchors use. Lineage
     is recorded as `body.source_anchor_id` so the UI can render
     "derived from <anchor>" without a graph.

§7 transitivity (the whole point): the source anchor already passed
§6. A derivative that passes containment therefore inherits §6 — every
claim in the derivative maps to an anchor claim that was already
bound. No re-binding against raw sources is needed.
"""
from __future__ import annotations

from app import guardrails as guardrails_mod
from app.agents.base import Agent
from app.agents.content_grader import grade_content
from app.agents.registry import register
from app.reports.derivatives import (DERIVATIVE_RENDERERS,
                                      trust_checks_with_containment_findings,
                                      validate_containment)
from app.reports.evidence import Ledger
from app.schemas import (AgentContext, AgentResult, ArtifactDraft, Citation,
                         ProposedAction)


_GATE_ALL = "gate_all"
_GUARDRAIL = "guardrail"
_ALL_THROUGH = "all_through"


# Derivative kind-promotion table. Mirrors report_composer's
# _ANCHOR_TYPES — one entry per derivative kind: dispatch key →
# Artifact.type. Stays in lock-step with the registry in
# app/api/assets.py (_DERIVATIVE_KINDS).
_DERIVATIVE_TYPES = {
    "exec_summary": "exec_summary_draft",
    "carousel":     "carousel_draft",
}


# Brand-token shape on body.brand_tokens. Lifted from
# report_composer; PRESENTATION ONLY. See report_composer.py for the
# load-bearing invariant.
_BRAND_BODY_KEYS = (
    "color_primary", "color_secondary", "color_accent",
    "color_background", "color_text",
    "font_heading", "font_body",
    "logo_path", "logo_mime",
)


def _brand_tokens_for_body(brand: dict) -> dict:
    return {k: brand.get(k) for k in _BRAND_BODY_KEYS}


@register
class DerivativeComposerAgent(Agent):
    key = "derivative_composer"
    display_name = "Derivative composer"

    def run(self, ctx: AgentContext) -> AgentResult:
        log = ctx.log
        task = ctx.task or {}
        deriv_type = (task.get("derivative_type") or "").strip().lower()
        if deriv_type not in DERIVATIVE_RENDERERS:
            raise RuntimeError(
                f"derivative_composer: unknown derivative_type "
                f"{deriv_type!r} (available: "
                f"{sorted(DERIVATIVE_RENDERERS.keys())}).")

        anchor = ctx.source_anchor
        if not anchor:
            raise RuntimeError(
                "derivative_composer: ctx.source_anchor is empty. The "
                "worker is responsible for pre-loading the source "
                "anchor via scoped() based on task.source_anchor_id.")

        log(f"derivative_composer type={deriv_type} from anchor="
            f"{anchor.get('id','?')[:8]} for {ctx.org_name}")

        # ---- Render -----------------------------------------------------
        from app.config import get_settings
        settings = get_settings()
        renderer = DERIVATIVE_RENDERERS[deriv_type]
        # Lead foregrounding — when the fan-out coordinator sets
        # task.lead_ev_id, pass it to the renderer so the lead claim
        # is the first lifted (selection-only; the renderer never
        # composes a new headline around it). Single-derivative runs
        # without a lead simply pass None and behave exactly as
        # before. The coordinator already validated the lead exists
        # in the anchor's ledger + is cited in prose; the renderer
        # treats None / missing-from-pairs gracefully.
        lead_ev_id = task.get("lead_ev_id") if isinstance(task, dict) else None
        content, gen_cost = renderer(anchor, settings=settings,
                                      lead_ev_id=lead_ev_id)

        # ---- Containment validation (the §7 enforcement site) ---------
        anchor_ledger_entries = (anchor.get("body") or {}).get(
            "evidence_ledger") or []
        # Reconstruct the Ledger object preserving ids verbatim — the
        # derivative's markers reference these ids directly, so the
        # round-trip must be exact (same pattern the demo seed uses).
        anchor_ledger = Ledger.from_entries(anchor_ledger_entries)
        trust_checks = validate_containment(content, anchor_ledger)
        trust_checks = trust_checks_with_containment_findings(
            trust_checks, anchor_ledger_entries, content)
        approval_blocked = bool(trust_checks.get("approval_blocked"))

        # ---- Visual render (carousel only, AFTER containment passes) ----
        # The visual pipeline reads ONLY the validated content. It
        # cannot reach into the source anchor's ledger or the
        # intelligence engine. By construction (see carousel_visual.py)
        # it adds no text the validated slides didn't already contain
        # — the slot template chooses font/color/layout, never copy.
        # We skip rendering on a containment failure: a §7-blocked
        # carousel must not produce shippable pixels.
        rendered_assets: list[dict] = []
        if deriv_type == "carousel" and not approval_blocked:
            try:
                from app.reports.derivatives.carousel_visual import (
                    render_carousel_images)
                file_records, _ = render_carousel_images(
                    content, ctx.brand or {},
                    org_id=ctx.org_id, run_id=ctx.run_id)
                # body.rendered_assets is the canonical UI-side
                # surface — paths are RELATIVE to storage_root so the
                # agent doesn't bake absolute filesystem layout into
                # the artifact body.
                rendered_assets = file_records
            except Exception as exc:
                # Visual render failure does NOT block the artifact —
                # the content is already validated and shippable.
                # Surface the error in logs so an op can investigate.
                log(f"carousel visual render failed: {exc!r}")

        # ---- Routing — same shape as report_composer ------------------
        flat_text = "\n".join((b.get("text") or "")
                              for b in content.get("blocks", []))
        profile = ctx.profile or ctx.org_profile or {}
        mode = (profile.get("content_review_mode") or _GUARDRAIL).lower()
        probe = ProposedAction(
            action_type="content_review",
            payload={"content_type": content.get("content_type"),
                     "topic": "executive summary",
                     "text": flat_text},
            guardrail_scope="content",
            reasoning="derivative routing — content_review pre-check.")
        gstatus, gdetail = guardrails_mod.evaluate(
            probe, ctx.guardrail_rules or {})

        if mode == _GATE_ALL:
            needs_review, why = True, ("Org policy: every draft "
                                        "reviewed (gate_all).")
        elif mode == _ALL_THROUGH:
            needs_review, why = False, ("Org policy: drafts auto-ready "
                                        "(all_through).")
        else:
            needs_review = (gstatus == "blocked")
            why = gdetail
        # A §7 containment breach blocks regardless of the org's
        # content_review_mode — same posture as the anchor §6 gate.
        if approval_blocked and not needs_review:
            needs_review = True
            n_critical = trust_checks["findings_by_severity"]["critical"]
            why = (f"{n_critical} §7 containment breach(es): derivative "
                   "introduced or mis-cited claims absent from the "
                   "source anchor. Approval blocked until reviewed.")
        artifact_status = "pending_review" if needs_review else "ready"

        # ---- Advisory grade -------------------------------------------
        grade, grade_cost = grade_content(content, profile)
        total_cost = round(gen_cost + grade_cost, 6)

        # ---- Body assembly -------------------------------------------
        body = {
            "content": content,
            # Canonical lineage — the body field the UI + smoke read.
            "source_anchor_id": anchor.get("id"),
            "source_anchor_type": anchor.get("type"),
            "source_anchor_title": anchor.get("title"),
            # Fan-out attribution (None for single-derivative runs).
            # lead_ev_id records WHICH anchor claim this derivative
            # was instructed to foreground; fanout_set_id records the
            # parent set. Both are pointers — no prose authored here.
            "lead_ev_id": lead_ev_id,
            "fanout_set_id": (task.get("fanout_set_id")
                               if isinstance(task, dict) else None),
            "provenance": {
                "org_profile": bool(ctx.org_profile),
                "derivative_type": deriv_type,
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
            "cost_breakdown": {"generation": gen_cost,
                                "grading": grade_cost},
            # The SOURCE ANCHOR's ledger — every marker in this body
            # cites an id here. We carry it verbatim so the trust_view
            # can render the ledger panel without a second fetch and
            # without ambiguity about which ledger this derivative
            # binds to.
            "evidence_ledger": anchor_ledger_entries,
            "trust_checks": trust_checks,
            # PRESENTATION ONLY (see report_composer for the load-
            # bearing invariant). Sibling of body.content, never inside.
            "brand_tokens": _brand_tokens_for_body(ctx.brand or {}),
            # Visual carousel slides (PNG paths relative to
            # storage_root). Empty list for non-carousel derivatives
            # or for carousels whose containment failed. The UI reads
            # this to show slide previews; the serve endpoint reads
            # individual rows to stream the bytes.
            "rendered_assets": rendered_assets,
        }

        # ---- Citations — point at the source anchor itself -----------
        cites: list[Citation] = []
        cites.append(Citation(
            source=f"Source anchor ({anchor.get('type','anchor')})",
            snippet=(anchor.get("title") or "source anchor")
                    + f" — id {anchor.get('id','?')[:8]}"))
        cites.append(Citation(
            source="Anchor evidence ledger",
            snippet=f"{len(anchor_ledger_entries)} entry(s) inherited "
                    "from source. Every claim in this derivative cites "
                    "one of them."))

        # ---- Artifact assembly ---------------------------------------
        artifact_type = _DERIVATIVE_TYPES[deriv_type]
        # One human noun per derivative kind for the title prefix.
        _TITLE_PREFIX = {
            "exec_summary": "Exec summary",
            "carousel":     "Carousel",
        }
        title = (f"{_TITLE_PREFIX.get(deriv_type, deriv_type.title())} "
                 f"— {anchor.get('title','(untitled anchor)')}")
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
                    "topic": "executive summary",
                    "blocks": content.get("blocks", []),
                    "text": flat_text,
                    "review_mode": mode,
                    "version": 1,
                    "parent_artifact_id": task.get("parent_artifact_id"),
                    "source_anchor_id": anchor.get("id"),
                },
                guardrail_scope="content",
                reasoning=(f"executive summary routed for human review "
                            f"— {why}"),
            ))

        return AgentResult(
            artifacts=[art], proposed_actions=proposals,
            cost_usd=total_cost, logs=[
                f"render strategy: {(content.get('metadata', {}) or {}).get('render_strategy', 'deterministic')}",
                f"containment: passed={trust_checks.get('passed')} "
                f"critical={trust_checks['findings_by_severity']['critical']}",
                f"routing: mode={mode} status={artifact_status} why={why}",
            ])
