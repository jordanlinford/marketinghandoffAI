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
        icp = ctx.icp or {}
        log(f"Starting market-intel run for {ctx.org_name}")

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

        structured = {
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

        citations = [
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
