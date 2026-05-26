"""
Content engine — the first ACTION-TAKING agent.

This is the agent that finally exercises the proposal → guardrail → review →
approve/reject spine. market_intel never produced a proposal; this one does.

Three modes (chosen via ctx.task.action):

  * "suggest"    — propose a short list of {content_type, topic, rationale,
                   target} ideas, grounded in the confirmed OrgProfile + the
                   latest market_brief. Read-only.

  * "generate"   — produce ONE piece of structured, block-based content for
                   the chosen idea (or a user-supplied content_type + topic).
                   Grades the draft against the rubric (advisory), tags it
                   with UTMs (the future analytics join key), and routes it
                   per the org's content_review_mode.

  * "regenerate" — "Give me something better". Takes a parent_artifact_id +
                   the user's free-text critique, regenerates with that
                   feedback, preserves the prior version (parent_id chain),
                   re-grades. Same routing rules apply.

Routing precedence (single decision site):
  - all_through → artifact status=ready, NO proposal.
  - guardrail   → clean drafts ready; banned-claim trips queued.
  - gate_all    → every draft queued.

CRITICAL: "approved" here means "draft marked ready" — NOT published. The
content_review_mode governs DRAFT review only. Publishing to any live channel
is a separate, always-gated action and is out of scope for v1. Grades are
ADVISORY — they never gate approval.

The agent reads the profile + brief + parent draft + guardrail rules ONLY
through ctx, per the chassis contract. It does not touch the DB.
"""
from __future__ import annotations

from app import guardrails as guardrails_mod
from app.agents.base import Agent
from app.agents.content_grader import grade_content
from app.agents.content_templates import (available_types, build as build_content)
from app.agents.registry import register
from app.agents.utm import build_tagged_url, suggest_utms, utm_dict_from_artifact
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
            return self._generate(ctx, profile, brief, task, parent=None)
        if action == "regenerate":
            parent = self._lookup_parent(ctx, task)
            return self._generate(ctx, profile, brief, task, parent=parent)
        raise RuntimeError(
            f"content_engine: unknown task.action {action!r} "
            f"(expected 'suggest', 'generate', or 'regenerate')")

    # ---- Mode: regenerate helpers -----------------------------------------
    @staticmethod
    def _lookup_parent(ctx: AgentContext, task: dict) -> dict:
        """Find the parent draft in ctx.prior_artifacts (the worker prepends
        it when task.parent_artifact_id is set). Fails closed if missing —
        we'd rather raise than silently fall back to a fresh draft, because
        the user explicitly asked for a revision of THIS piece.

        Cross-tenant safety: the worker loaded the parent via scoped() before
        passing it in; if it didn't belong to this org, prior_artifacts won't
        contain it and we fail here."""
        parent_id = (task.get("parent_artifact_id") or "").strip()
        if not parent_id:
            raise RuntimeError(
                "content_engine regenerate: 'parent_artifact_id' is required")
        for prior in ctx.prior_artifacts or []:
            if (prior.get("type") == "content_draft"
                    and prior.get("id") == parent_id):
                return prior
        raise RuntimeError(
            f"content_engine regenerate: parent artifact {parent_id!r} not "
            f"found for this org. (Cross-tenant lookups are blocked by scoped().)")

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

    # ---- Mode: generate (covers both fresh + regenerate) ------------------
    def _generate(self, ctx: AgentContext, profile: dict, brief: dict | None,
                  task: dict, parent: dict | None) -> AgentResult:
        # For "generate", task carries content_type + topic + target. For
        # "regenerate", these default to the parent's values so the user only
        # has to supply the critique. Either path lets the user override.
        parent_body = (parent or {}).get("body") or {}
        parent_content = parent_body.get("content") or {}
        parent_meta = parent_content.get("metadata") or {}
        content_type = (task.get("content_type")
                        or (parent_content.get("content_type") if parent else "")
                        or "").strip()
        topic = (task.get("topic")
                 or (parent_meta.get("topic") if parent else "")
                 or "").strip()
        target = (task.get("target")
                  or (parent_meta.get("target") if parent else "")
                  or "").strip()
        critique = (task.get("critique") or "").strip() if parent else ""
        action_label = "regenerate" if parent else "generate"

        if not content_type:
            raise RuntimeError(f"content_engine {action_label}: 'content_type' is required")
        if content_type not in available_types():
            raise RuntimeError(
                f"content_engine {action_label}: unknown content_type {content_type!r} "
                f"(available: {available_types()})")
        if not topic:
            raise RuntimeError(f"content_engine {action_label}: 'topic' is required")
        if parent and not critique:
            raise RuntimeError(
                "content_engine regenerate: 'critique' is required — describe "
                "what you'd like changed.")

        content, gen_cost = build_content(content_type, profile, brief,
                                          topic, target, critique=critique)

        # ---- UTM tagging (the future analytics join key) ------------------
        # Version increments down the parent chain so utm_content is unique
        # per piece. The campaign/source/medium dimensions are CARRIED FORWARD
        # from the parent (a "Give me something better" stays inside the
        # same campaign), but utm_content is always re-derived per version so
        # v2 is distinguishable from v1. Task overrides win in all cases.
        version = int(parent_body.get("version", 1)) + 1 if parent else 1
        utm_overrides: dict = {}
        if parent:
            parent_utms = utm_dict_from_artifact(parent)
            # Preserve the campaign + channel dimensions; let utm_content be
            # rebuilt with the new version suffix.
            for k in ("utm_campaign", "utm_source", "utm_medium"):
                if parent_utms.get(k):
                    utm_overrides[k] = parent_utms[k]
        utm_overrides.update(task.get("utm") or {})
        utms = suggest_utms(content_type, topic, version=version,
                            overrides=utm_overrides)
        destination_url = (task.get("destination_url")
                           or (parent or {}).get("destination_url")
                           or profile.get("website_url") or "")
        tagged_url = build_tagged_url(destination_url, utms)

        # ---- Guardrail + routing (unchanged spine) ------------------------
        flat_text = "\n".join((b.get("text") or "") for b in content.get("blocks", []))
        mode = (profile.get("content_review_mode") or _GUARDRAIL).lower()
        probe = ProposedAction(
            action_type="content_review",
            payload={"content_type": content_type, "topic": topic, "text": flat_text},
            guardrail_scope="content",
            reasoning=f"{action_label} {content_type} for topic {topic!r}.")
        gstatus, gdetail = guardrails_mod.evaluate(probe, ctx.guardrail_rules or {})

        if mode == _GATE_ALL:
            needs_review, why = True, "Org policy: every draft reviewed (gate_all)."
        elif mode == _ALL_THROUGH:
            needs_review, why = False, "Org policy: drafts auto-ready (all_through)."
        else:  # guardrail
            needs_review = (gstatus == "blocked")
            why = gdetail
        artifact_status = "pending_review" if needs_review else "ready"

        # ---- Self-grade (advisory, never gating) --------------------------
        grade, grade_cost = grade_content(content, profile)
        total_cost = round(gen_cost + grade_cost, 6)

        # ---- Build the artifact -------------------------------------------
        provenance = Citation(
            source="Org profile (confirmed) + latest market brief"
            if brief else "Org profile (confirmed)",
            snippet="Brand voice, banned_claims, value prop, competitors, "
                    "conversion goal" + (
                        " · brief targets and keyword gaps" if brief else ""))
        review_note = Citation(
            source="Review routing",
            snippet=f"mode={mode}, guardrail={gstatus} — {why}")
        cites = [provenance, review_note]
        if parent:
            cites.append(Citation(
                source="Revision",
                snippet=f"Revised from artifact {parent.get('id')} — critique: {critique!r}"))

        body = {
            "content": content,
            "provenance": {
                "org_profile": True,
                "market_brief_id": (brief or {}).get("id"),
                "brand_voice_used": bool(profile.get("brand_voice")),
                "competitors_reflected": [
                    c.get("name") for c in (profile.get("competitors") or [])
                    if isinstance(c, dict) and c.get("name")],
                # Honest about the publish gap (brief: graceful-honesty rule):
                "publish_note": ("This is a draft. Publish using the tagged "
                                 "link so performance can be traced back here."),
            },
            "routing": {"mode": mode, "guardrail_status": gstatus,
                        "guardrail_detail": gdetail, "needs_review": needs_review,
                        "artifact_status": artifact_status},
            "version": version,
            "parent_artifact_id": (parent or {}).get("id"),
            "critique": critique,
            "grade": grade,
            "tagged_url": tagged_url,
            "cost_breakdown": {"generation": gen_cost, "grading": grade_cost},
        }
        art = ArtifactDraft(
            type="content_draft",
            title=f"{content_type.replace('_', ' ').title()} — {topic[:60]}"
                  + (f" (v{version})" if version > 1 else ""),
            body=body, citations=cites,
            status=artifact_status,
            parent_id=(parent or {}).get("id"),
            grade=grade,
            utm_campaign=utms["utm_campaign"], utm_source=utms["utm_source"],
            utm_medium=utms["utm_medium"], utm_content=utms["utm_content"],
            destination_url=destination_url or None,
        )

        proposals: list[ProposedAction] = []
        if needs_review:
            # The proposal payload carries enough for the queue UI to render
            # the draft (content_type, topic, blocks). The Proposal's run_id
            # links back to the content_draft artifact on the same run, so
            # review.py can flip its status on approve/reject.
            proposals.append(ProposedAction(
                action_type="content_review",
                payload={
                    "content_type": content_type, "topic": topic,
                    "blocks": content.get("blocks", []),
                    "text": flat_text,
                    "review_mode": mode,
                    "version": version,
                    "parent_artifact_id": (parent or {}).get("id"),
                },
                guardrail_scope="content",
                reasoning=f"{content_type} draft routed for human review — {why}",
            ))

        return AgentResult(artifacts=[art], proposed_actions=proposals,
                           cost_usd=total_cost, logs=[])

    # ---- Helpers ----------------------------------------------------------
    @staticmethod
    def _latest_brief(ctx: AgentContext) -> dict | None:
        for prior in ctx.prior_artifacts or []:
            if prior.get("type") == "market_brief":
                return prior
        return None
