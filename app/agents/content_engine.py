"""
Content engine — the first ACTION-TAKING agent.

This is the agent that finally exercises the proposal → guardrail → review →
approve/reject spine. market_intel never produced a proposal; this one does.

Two modes (chosen via ctx.task.action):

  * "suggest"  — read the confirmed OrgProfile + the latest market_brief and
                 propose a short list of {content_type, topic, rationale,
                 target} ideas. Read-only: emits an artifact (status=ready),
                 no proposals.

  * "generate" — produce ONE piece of structured, block-based content for the
                 chosen idea (or a user-supplied content_type + topic). The
                 draft is routed per the org's content_review_mode:

                    all_through → artifact status=ready, NO proposal.
                    guardrail   → clean drafts ready; banned-claim trips
                                  land in the queue as pending_review.
                    gate_all    → every draft lands in the queue.

                 Routing precedence: mode=gate_all wins; otherwise the
                 guardrail decides. The agent CALLS the shared guardrail
                 evaluator (it never reinvents the framework).

CRITICAL: "approved" here means "draft marked ready" — NOT published. The
content_review_mode governs DRAFT review only. Publishing to any live channel
is a separate, always-gated action and is out of scope for v1.

The agent reads the profile + brief + guardrail rules ONLY through ctx, per
the chassis contract. It does not touch the DB.
"""
from __future__ import annotations

from app import guardrails as guardrails_mod
from app.agents.base import Agent
from app.agents.content_templates import (available_types, build as build_content)
from app.agents.registry import register
from app.schemas import (AgentContext, AgentResult, ArtifactDraft, Citation,
                         ProposedAction)


# Default suggestion seeds when no market brief exists yet — keeps suggest mode
# useful on day one. Each suggestion still grounds in the profile.
_SEED_TOPICS = [
    ("email", "Cut weekly review overhead"),
    ("ad", "Stop the spreadsheet sprawl"),
    ("social_post", "What modern operations teams actually need"),
    ("blog_outline", "The hidden cost of manual workflows"),
]

# Content_review_mode values that route drafts to the queue regardless of the
# guardrail verdict. Kept here as the single source for the routing decision.
_GATE_ALL = "gate_all"
_GUARDRAIL = "guardrail"
_ALL_THROUGH = "all_through"


@register
class ContentEngineAgent(Agent):
    key = "content_engine"
    display_name = "Content engine"

    def run(self, ctx: AgentContext) -> AgentResult:
        log = ctx.log
        task = ctx.task or {}
        action = (task.get("action") or "suggest").lower()
        log(f"content_engine action={action} for {ctx.org_name}")

        profile = ctx.org_profile
        if profile is None:
            # We could still suggest from seed defaults, but the brief is
            # clear: content engine is the payoff of Setup. If there's no
            # confirmed profile, fail loudly rather than ship ungrounded copy.
            raise RuntimeError(
                "content_engine requires a confirmed org profile. "
                "Save the profile in Setup before running this agent.")

        brief = self._latest_brief(ctx)

        if action == "suggest":
            return self._suggest(ctx, profile, brief)
        if action == "generate":
            return self._generate(ctx, profile, brief, task)
        raise RuntimeError(
            f"content_engine: unknown task.action {action!r} "
            f"(expected 'suggest' or 'generate')")

    # ---- Mode: suggest ----------------------------------------------------
    def _suggest(self, ctx: AgentContext, profile: dict,
                 brief: dict | None) -> AgentResult:
        ideas = self._build_ideas(profile, brief)
        body = {
            "ideas": ideas,
            "grounded_in": {
                "org_profile": True,
                "market_brief_id": (brief or {}).get("id"),
            },
        }
        cites = [
            Citation(source="Org profile (confirmed)",
                     snippet="ICP, product, voice, competitors and conversion goal"),
        ]
        if brief:
            cites.append(Citation(
                source="Latest market brief",
                snippet=f"{brief.get('title') or 'market_brief'} — "
                        f"keyword gaps and target accounts"))
        else:
            cites.append(Citation(
                source="No market brief yet",
                snippet="Suggestions still ground in your org profile; run "
                        "market_intel for richer ideas."))
        art = ArtifactDraft(type="content_ideas",
                            title=f"Content ideas for {ctx.org_name}",
                            body=body, citations=cites, status="ready")
        return AgentResult(artifacts=[art], proposed_actions=[],
                           cost_usd=0.0, logs=[])

    @staticmethod
    def _build_ideas(profile: dict, brief: dict | None) -> list[dict]:
        struct = ((brief or {}).get("body") or {}).get("structured") or {}
        # Seed topics from the brief's recommendations + keyword clusters when
        # available, else from a small built-in list. Each idea pairs a
        # content_type with a topic + rationale + target. Target is drawn from
        # the profile's ICP industries / titles so it's specific.
        targets = (profile.get("icp", {}).get("industries")
                   or profile.get("icp", {}).get("titles") or ["your ICP"])
        target = targets[0] if targets else "your ICP"
        ideas: list[dict] = []

        recs = struct.get("recommendations") or []
        for rec in recs[:3]:
            ideas.append({"content_type": "email", "topic": rec[:120],
                          "rationale": "Surfaced in the latest market brief.",
                          "target": target})

        commercial = ((struct.get("keyword_clusters") or {}).get("commercial") or [])
        for term in commercial[:2]:
            ideas.append({"content_type": "ad", "topic": term["term"],
                          "rationale": f"High-intent commercial term "
                                       f"(vol {term.get('volume', '?')}).",
                          "target": target})

        # Always include at least one of each remaining type so the user has
        # variety on day one — even when the brief is thin or missing.
        seen_types = {i["content_type"] for i in ideas}
        for ct, topic in _SEED_TOPICS:
            if ct in seen_types:
                continue
            ideas.append({"content_type": ct, "topic": topic,
                          "rationale": "From your org profile (no matching "
                                       "brief signal yet).",
                          "target": target})
            seen_types.add(ct)

        return ideas[:6]

    # ---- Mode: generate ---------------------------------------------------
    def _generate(self, ctx: AgentContext, profile: dict, brief: dict | None,
                  task: dict) -> AgentResult:
        content_type = (task.get("content_type") or "").strip()
        topic = (task.get("topic") or "").strip()
        target = (task.get("target") or "").strip()
        if not content_type:
            raise RuntimeError("content_engine generate: 'content_type' is required")
        if content_type not in available_types():
            raise RuntimeError(
                f"content_engine generate: unknown content_type {content_type!r} "
                f"(available: {available_types()})")
        if not topic:
            raise RuntimeError("content_engine generate: 'topic' is required")

        content, cost = build_content(content_type, profile, brief, topic, target)
        # Assemble flat text for the guardrail check — the rule trips on any
        # banned phrase appearing in ANY block's text.
        flat_text = "\n".join((b.get("text") or "") for b in content.get("blocks", []))

        mode = (profile.get("content_review_mode") or _GUARDRAIL).lower()
        # The agent calls the SHARED guardrail evaluator with the org's rules
        # (loaded by the worker into ctx.guardrail_rules). banned_claims is
        # merged in from the profile by the worker — single source of truth.
        probe = ProposedAction(
            action_type="content_review",
            payload={"content_type": content_type, "topic": topic, "text": flat_text},
            guardrail_scope="content",
            reasoning=f"Drafted {content_type} for topic {topic!r}.")
        gstatus, gdetail = guardrails_mod.evaluate(probe, ctx.guardrail_rules or {})

        # Routing precedence (kept here, single decision site):
        #   * mode=gate_all → always queue.
        #   * mode=guardrail → queue iff the rule blocked the draft.
        #   * mode=all_through → never queue (still recorded as an artifact).
        # NOTE: "ready" never means "published" — publishing is a separate,
        # always-gated future action.
        if mode == _GATE_ALL:
            needs_review, why = True, "Org policy: every draft reviewed (gate_all)."
        elif mode == _ALL_THROUGH:
            needs_review, why = False, "Org policy: drafts auto-ready (all_through)."
        else:  # guardrail
            needs_review = (gstatus == "blocked")
            why = gdetail

        artifact_status = "pending_review" if needs_review else "ready"
        provenance = Citation(
            source="Org profile (confirmed) + latest market brief"
            if brief else "Org profile (confirmed)",
            snippet="Brand voice, banned_claims, value prop, competitors, "
                    "conversion goal" + (
                        " · brief targets and keyword gaps" if brief else ""))
        review_note = Citation(
            source="Review routing",
            snippet=f"mode={mode}, guardrail={gstatus} — {why}")
        body = {
            "content": content,
            "provenance": {
                "org_profile": True,
                "market_brief_id": (brief or {}).get("id"),
                "brand_voice_used": bool(profile.get("brand_voice")),
                "competitors_reflected": [
                    c.get("name") for c in (profile.get("competitors") or [])
                    if isinstance(c, dict) and c.get("name")],
            },
            "routing": {"mode": mode, "guardrail_status": gstatus,
                        "guardrail_detail": gdetail, "needs_review": needs_review,
                        "artifact_status": artifact_status},
        }
        art = ArtifactDraft(
            type="content_draft",
            title=f"{content_type.replace('_', ' ').title()} — {topic[:60]}",
            body=body, citations=[provenance, review_note],
            status=artifact_status,
        )

        proposals: list[ProposedAction] = []
        if needs_review:
            # The proposal payload carries enough for the queue UI to render
            # the draft (content_type, topic, blocks). artifact_id is filled
            # in by the worker after the artifact row is flushed.
            # The Proposal's run_id (set by the worker) already links back to
            # the content_draft artifact on the same run. Payload carries the
            # blocks so the queue UI can render the draft without an extra
            # fetch. Approve/Reject in review.py flips that artifact's status.
            proposals.append(ProposedAction(
                action_type="content_review",
                payload={
                    "content_type": content_type, "topic": topic,
                    "blocks": content.get("blocks", []),
                    "text": flat_text,
                    "review_mode": mode,
                },
                guardrail_scope="content",
                reasoning=f"{content_type} draft routed for human review — {why}",
            ))

        return AgentResult(artifacts=[art], proposed_actions=proposals,
                           cost_usd=cost, logs=[])

    # ---- Helpers ----------------------------------------------------------
    @staticmethod
    def _latest_brief(ctx: AgentContext) -> dict | None:
        for prior in ctx.prior_artifacts or []:
            if prior.get("type") == "market_brief":
                return prior
        return None
