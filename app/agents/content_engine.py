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

        # The agent reads profile-level config through ctx.profile (the
        # ResolvedProfile from app/products.resolve_product_profile). With
        # no product selected, the resolved profile is the org-level view;
        # with a product, it carries the inherited fields, product-only
        # fields under profile["product"], and per-field provenance.
        # The resolver always returns a dict, so the underlying _org block
        # is what tells us whether a confirmed OrgProfile actually exists.
        resolved = ctx.profile or {}
        if not resolved.get("_org"):
            # We could still suggest from seed defaults, but the brief is
            # clear: content engine is the payoff of Setup. If there's no
            # confirmed profile, fail loudly rather than ship ungrounded copy.
            raise RuntimeError(
                "content_engine requires a confirmed org profile. "
                "Save the profile in Setup before running this agent.")
        profile = resolved

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
        # Memory re-rank: when the org has performance history above the
        # 'insufficient' threshold, surface ideas matching higher-performing
        # channels/content_types first. Evidence is attached per idea so
        # the UI shows the 'because' next to the rerank. No data → input
        # order is preserved (backwards compat).
        ideas = self._memory_rerank_ideas(ideas, ctx.memory_patterns or [])
        body = {
            "ideas": ideas,
            "grounded_in": {
                "org_profile": True,
                "market_brief_id": (brief or {}).get("id"),
                "memory_patterns": len(ctx.memory_patterns or []),
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

    @staticmethod
    def _memory_rerank_ideas(ideas: list[dict],
                             patterns: list[dict]) -> list[dict]:
        # Re-rank ideas by the org's own performance memory. Ideas whose
        # content_type matches a high/moderate/low confidence pattern
        # bubble up, with the pattern's observation attached so the UI
        # can show the 'because'. insufficient and empty patterns leave
        # input order untouched (backwards compat).
        if not ideas or not patterns:
            return ideas
        boost: dict[str, dict] = {}
        for p in patterns:
            if p.get("confidence") in ("insufficient",):
                continue
            if p.get("dimension") not in ("content_type", "channel"):
                continue
            key = p.get("key") or ""
            if not key:
                continue
            # First pattern per key wins (query_memory already sorts by
            # confidence + sample_size desc, so this favors the strongest).
            boost.setdefault(key, p)
        if not boost:
            return ideas
        _CONF_RANK = {"high": 0, "moderate": 1, "low": 2}
        def score(idea: dict) -> tuple:
            ct = (idea.get("content_type") or "").lower()
            p = boost.get(ct)
            if not p:
                return (99, 0.0)
            mb = p.get("metric_basis") or {}
            rate = mb.get("conversion_rate") or 0.0
            return (_CONF_RANK.get(p.get("confidence"), 99), -rate)
        annotated = []
        for idea in ideas:
            ct = (idea.get("content_type") or "").lower()
            p = boost.get(ct)
            out = dict(idea)
            if p is not None:
                out["memory_evidence"] = {
                    "observation": p.get("observation", ""),
                    "metric_basis": p.get("metric_basis") or {},
                    "confidence": p.get("confidence"),
                    "dimension": p.get("dimension"),
                }
            annotated.append(out)
        annotated.sort(key=score)
        return annotated

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

        # Product-aware template view: when a product is selected, fold its
        # positioning + first value_prop into the org-level fields the
        # template builders read, and prefer product_competitors over the
        # org's competitors list. The original resolved profile keeps the
        # full inheritance + provenance for downstream consumers.
        template_profile = self._product_aware_view(profile)
        # Persistent memory: weave actionable patterns into the style brief
        # as natural-language guidance (no-echo discipline — content_templates
        # _compose_style_brief reads profile["memory_summary"] and folds it
        # into the system message as instruction, never as a labeled field).
        # Only patterns above 'insufficient' show up here; thin-data noise is
        # filtered out at summarize_for_prompt.
        from app.memory import summarize_for_prompt  # local import: keeps cold paths cheap
        mem_summary = summarize_for_prompt(ctx.memory_patterns or [])
        if mem_summary:
            template_profile = dict(template_profile)
            template_profile["memory_summary"] = mem_summary
        content, gen_cost = build_content(content_type, template_profile, brief,
                                          topic, target, critique=critique)

        # ---- UTM tagging (the future analytics join key) ------------------
        # Version increments down the parent chain so utm_content is unique
        # per piece. The campaign/source/medium dimensions are CARRIED FORWARD
        # from the parent (a "Give me something better" stays inside the
        # same campaign), but utm_content is always re-derived per version so
        # v2 is distinguishable from v1. Task overrides win in all cases.
        # Product defaults (utm_source_default / utm_medium_default /
        # utm_campaign_prefix) are layered in BELOW the parent + task
        # overrides so an explicit value always wins.
        version = int(parent_body.get("version", 1)) + 1 if parent else 1
        utm_overrides: dict = {}
        for k in ("utm_source_default", "utm_medium_default"):
            v = profile.get(k)
            if v:
                # Strip the "_default" suffix to match the UTM key name.
                utm_overrides[k.replace("_default", "")] = v
        if parent:
            parent_utms = utm_dict_from_artifact(parent)
            # Preserve the campaign + channel dimensions; let utm_content be
            # rebuilt with the new version suffix.
            for k in ("utm_campaign", "utm_source", "utm_medium"):
                if parent_utms.get(k):
                    utm_overrides[k] = parent_utms[k]
        utm_overrides.update(task.get("utm") or {})
        utms = suggest_utms(content_type, topic, version=version,
                            overrides=utm_overrides,
                            campaign_prefix=profile.get("utm_campaign_prefix"))
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
        product = (profile.get("product") or None)
        prov_source_parts = ["Org profile (confirmed)"]
        if product:
            prov_source_parts.append(f"{product.get('name')} product profile")
        if brief:
            prov_source_parts.append("latest market brief")
        provenance = Citation(
            source=" + ".join(prov_source_parts),
            snippet=(
                "Brand voice, banned_claims, value prop, conversion goal"
                + (" · product positioning + value_props + product_competitors"
                   if product else " · competitors")
                + (" · brief targets and keyword gaps" if brief else "")))
        review_note = Citation(
            source="Review routing",
            snippet=f"mode={mode}, guardrail={gstatus} — {why}")
        cites = [provenance, review_note]
        if parent:
            cites.append(Citation(
                source="Revision",
                snippet=f"Revised from artifact {parent.get('id')} — critique: {critique!r}"))

        # Competitors that the template actually used (product-first when
        # product set, else org-level), for honest "reflected" provenance.
        competitors_used = (product or {}).get("product_competitors") \
            if product and (product or {}).get("product_competitors") \
            else profile.get("competitors") or []
        # Messaging notes (from document extraction's promotion path) —
        # consult objection_handling when the topic touches a named
        # competitor or pricing. Lightweight: we surface which notes
        # the prompt considered in the artifact's provenance so the
        # human can audit. The LLM path also receives the full notes
        # block via _grounding_payload in app/agents/content_templates.py.
        considered_objections = self._relevant_objections(
            profile.get("messaging_notes") or {}, topic, competitors_used)

        body = {
            "content": content,
            "provenance": {
                "org_profile": True,
                "product_id": (product or {}).get("id"),
                "product_name": (product or {}).get("name"),
                "market_brief_id": (brief or {}).get("id"),
                "brand_voice_used": bool(profile.get("brand_voice")),
                "brand_voice_source": (profile.get("provenance", {})
                                       .get("brand_voice", "org")),
                "competitors_reflected": [
                    c.get("name") for c in competitors_used
                    if isinstance(c, dict) and c.get("name")],
                "positioning_used": bool(product and product.get("positioning")),
                "objection_handling_considered": considered_objections,
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
    def _relevant_objections(messaging_notes: dict, topic: str,
                             competitors_used: list) -> list[dict]:
        """Pick the subset of objection_handling notes worth surfacing for
        THIS draft. Matched when the topic mentions a competitor name OR
        pricing words OR the objection's own text overlaps with the topic.
        Lightweight; we don't restructure the agent, we just include the
        relevant subset in the prompt + provenance."""
        all_notes = (messaging_notes or {}).get("objection_handling") or []
        if not all_notes:
            return []
        topic_l = (topic or "").lower()
        competitor_names = [
            (c.get("name") if isinstance(c, dict) else str(c)).lower()
            for c in (competitors_used or [])
            if (c.get("name") if isinstance(c, dict) else c)]
        pricing_signals = ("price", "pricing", "cost", "budget", "expensive",
                           "cheaper", "discount")
        touches_pricing = any(w in topic_l for w in pricing_signals)
        touches_competitor = any(name and name in topic_l for name in competitor_names)
        out: list[dict] = []
        for note in all_notes:
            if not isinstance(note, dict):
                continue
            obj_text = (note.get("objection") or note.get("note") or "").lower()
            if (touches_competitor or touches_pricing
                    or (obj_text and any(tok and tok in topic_l
                                         for tok in obj_text.split()
                                         if len(tok) > 4))):
                out.append({
                    "objection": note.get("objection") or note.get("note") or "",
                    "response": note.get("response") or "",
                    "source_passage": note.get("source_passage") or "",
                })
        return out[:5]   # cap so we don't blow up the prompt

    @staticmethod
    def _product_aware_view(profile: dict) -> dict:
        """Build the dict the template builders read. When a product layer
        is present in the resolved profile:
          - prepend the product's positioning to the org's product_summary
            so the LLM + template see "what we sell + how we sell it";
          - fold the product's first value_prop into the value_prop field
            (templates lead with it);
          - replace `competitors` with `product_competitors` when the
            product has any (the brief: prefer them for positioning copy).
        With no product layer, returns the resolved profile unchanged.
        Templates ignore unknown keys, so this stays purely additive."""
        product = profile.get("product") or None
        if not product:
            return profile
        view = dict(profile)
        positioning = (product.get("positioning") or "").strip()
        org_summary = (profile.get("product_summary") or "").strip()
        if positioning:
            view["product_summary"] = (
                f"{org_summary}. {positioning}" if org_summary else positioning)
        value_props = product.get("value_props") or []
        if value_props:
            lead = (value_props[0] or "").strip()
            org_value = (profile.get("value_prop") or "").strip()
            if lead:
                view["value_prop"] = f"{lead} ({org_value})" if org_value else lead
        prod_comps = product.get("product_competitors") or []
        if prod_comps:
            view["competitors"] = prod_comps
        return view

    @staticmethod
    def _latest_brief(ctx: AgentContext) -> dict | None:
        for prior in ctx.prior_artifacts or []:
            if prior.get("type") == "market_brief":
                return prior
        return None
