"""
Market-intelligence agent (Stages 1+2 of the pipeline).

Read-only by design: it produces a cited weekly brief and returns ZERO proposed
actions, so it exercises the entire runs/artifacts/observability path without
ever touching the spend or publish gates.
"""
from __future__ import annotations

from collections import defaultdict

from app.agents.base import Agent
from app.agents.registry import register
from app.agents.synthesis import synthesize_brief
from app.data_sources.stub import StubMarketDataSource
from app.schemas import AgentContext, AgentResult, ArtifactDraft, Citation


@register
class MarketIntelAgent(Agent):
    key = "market_intel"
    display_name = "Market intelligence"

    def run(self, ctx: AgentContext) -> AgentResult:
        log = ctx.log
        log(f"Starting market-intel run for {ctx.org_name}")

        # Input precedence (do not drift): a CONFIRMED OrgProfile (resolved
        # via ctx.profile when a product is selected, otherwise the org
        # layer itself) wins over the seed agent config field by field.
        # With no confirmed profile, fall back to the seed config unchanged
        # (no regression). The agent reads the profile ONLY through ctx —
        # never the DB.
        #
        # The resolver always returns a dict (with provenance + _org keys),
        # so "no confirmed profile" is detected via _org being empty rather
        # than the outer dict being falsy.
        resolved = ctx.profile or {}
        profile_in = resolved if resolved.get("_org") else ctx.org_profile
        icp, product_summary, competitors, inputs_source = self._effective_inputs(
            ctx.icp or {}, profile_in)
        log("Using confirmed org profile over seed config"
            if inputs_source == "org_profile"
            else "No confirmed org profile — using seed agent config")

        ds = ctx.get_market_data() if ctx.get_market_data else StubMarketDataSource()

        # --- Stage 1: sizing + targets ------------------------------------
        companies = ds.find_companies(icp, limit=int(ctx.config.get("company_limit", 120)))
        log(f"Pulled {len(companies)} companies from {companies[0].source if companies else 'n/a'}")

        tam_revenue = sum(c.revenue_usd for c in companies)
        sam_min_fit = float(ctx.config.get("sam_min_fit", 0.6))
        sam = [c for c in companies if c.fit_score >= sam_min_fit]
        som_rate = float(ctx.config.get("som_capture_rate", 0.08))

        ranked = sorted(companies, key=lambda c: c.fit_score * 0.5 + c.intent_score * 0.5,
                        reverse=True)
        top_targets = [{
            "name": c.name, "industry": c.industry, "employees": c.employees,
            "fit_score": c.fit_score, "intent_score": c.intent_score,
        } for c in ranked[:15]]

        # --- Stage 2: keywords, intent clusters, channel recs -------------
        seeds = icp.get("keyword_seeds") or [icp.get("category", "legal operations")]
        keywords = ds.keyword_universe(seeds, limit=int(ctx.config.get("keyword_limit", 60)))
        clusters: dict[str, list[dict]] = defaultdict(list)
        for k in keywords:
            clusters[k.intent].append(
                {"term": k.term, "volume": k.monthly_volume, "difficulty": k.difficulty})
        for v in clusters.values():
            v.sort(key=lambda x: x["volume"], reverse=True)

        recommendations = self._recommend(sam, clusters, icp)
        competitor_names = [c.get("name") for c in competitors
                            if isinstance(c, dict) and c.get("name")]
        if competitor_names:
            recommendations.insert(0, "Position against named competitors "
                                   f"({', '.join(competitor_names)}) in comparison "
                                   "content and high-intent ads.")

        structured = {
            # Honesty/provenance: surface which inputs drove this brief so the
            # user can see whether it reasoned from their confirmed profile or
            # from seed defaults.
            "inputs": {
                "source": inputs_source,            # "org_profile" | "seed"
                "product_summary": product_summary,
                "competitors": competitor_names,
                "industries": icp.get("industries", []),
                "keyword_seeds": seeds,
            },
            "sizing": {
                "tam_companies": len(companies),
                "tam_revenue_usd": tam_revenue,
                "sam_companies": len(sam),
                "som_companies": int(len(sam) * som_rate),
            },
            "top_targets": top_targets,
            "keyword_clusters": dict(clusters),
            "recommendations": recommendations,
        }

        narrative, cost = synthesize_brief(structured, ctx.org_name)
        log(f"Synthesis complete (cost ${cost:.4f})")

        provenance = (
            Citation(source="Org profile (confirmed)",
                     snippet="ICP, product, competitors and keywords from your "
                             "saved org profile")
            if inputs_source == "org_profile" else
            Citation(source="Seed defaults",
                     snippet="No confirmed org profile yet — using the seed agent config")
        )
        citations = [
            provenance,
            Citation(source="ZoomInfo (stub)", snippet="Firmographic + intent data"),
            Citation(source="Keyword data (stub)", snippet="Search volume + difficulty"),
        ]
        artifact = ArtifactDraft(
            type="market_brief",
            title=f"Weekly market brief — {ctx.org_name}",
            body={"narrative": narrative, "structured": structured},
            citations=citations,
        )
        return AgentResult(artifacts=[artifact], proposed_actions=[],
                           cost_usd=cost, logs=[])

    @staticmethod
    def _effective_inputs(seed_icp: dict, profile: dict | None):
        """Resolve the agent's inputs with explicit precedence:
        confirmed OrgProfile > seed agent config, field by field.

        Returns (icp, product_summary, competitors, source) where `source` is
        "org_profile" when a confirmed profile contributed, else "seed". When
        no profile exists the seed config is returned untouched (no regression).
        """
        if not profile:
            return seed_icp, "", [], "seed"
        # Start from the seed icp so fields the profile doesn't carry (channels,
        # category) still flow through; then overlay the profile's values.
        icp = dict(seed_icp)
        p_icp = profile.get("icp") or {}
        if p_icp.get("industries"):
            icp["industries"] = p_icp["industries"]
        if p_icp.get("min_employees") is not None:
            icp["min_employees"] = p_icp["min_employees"]
        if profile.get("keywords"):
            icp["keyword_seeds"] = profile["keywords"]
        return (icp, profile.get("product_summary") or "",
                profile.get("competitors") or [], "org_profile")

    @staticmethod
    def _recommend(sam, clusters, icp) -> list[str]:
        recs = []
        commercial = clusters.get("commercial", []) + clusters.get("transactional", [])
        if commercial:
            top = commercial[0]["term"]
            recs.append(f"Run search ads against high-intent terms (e.g. '{top}') — "
                        f"buyers here are comparison-shopping.")
        if clusters.get("informational"):
            recs.append("Build top-of-funnel content (guides, frameworks) for "
                        "informational queries to capture early research.")
        hot = [c for c in sam if c.intent_score >= 0.5]
        if hot:
            recs.append(f"Prioritize ABM outreach to {len(hot)} accounts showing active "
                        f"in-market intent.")
        channels = icp.get("channels") or ["LinkedIn", "Google Search", "Email"]
        recs.append(f"Lead channels for this ICP: {', '.join(channels)}.")
        return recs
