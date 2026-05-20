"""
Deterministic stub data source. Produces plausible companies and keywords
shaped by the org's ICP, so the whole chassis runs end to end with zero API
credits. Replace with ZoomInfoDataSource / ApolloDataSource later — same
interface, the agent doesn't change.
"""
from __future__ import annotations

import hashlib
import random

from app.data_sources.base import CompanyRecord, KeywordRecord, MarketDataSource

_FIRST = ["Vertex", "Northwind", "Atlas", "Meridian", "Cobalt", "Summit", "Harbor",
          "Ironclad", "Beacon", "Lattice", "Granite", "Vantage", "Keystone", "Pinnacle",
          "Sterling", "Aegis", "Cardinal", "Bedrock", "Equinox", "Halcyon"]
_LAST = ["Legal", "Industries", "Holdings", "Group", "Partners", "Systems", "Capital",
         "Global", "Corp", "Solutions"]


def _seed(*parts: str) -> random.Random:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


class StubMarketDataSource(MarketDataSource):
    def find_companies(self, icp: dict, limit: int = 100) -> list[CompanyRecord]:
        industries = icp.get("industries") or ["Legal Services", "Financial Services",
                                                "Manufacturing", "Technology", "Healthcare"]
        min_emp = int(icp.get("min_employees", 200))
        rng = _seed("companies", str(icp))
        out: list[CompanyRecord] = []
        for i in range(limit):
            name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
            emp = int(min_emp * rng.uniform(1.0, 25.0))
            rev = emp * rng.uniform(180_000, 420_000)
            out.append(CompanyRecord(
                name=name,
                employees=emp,
                revenue_usd=round(rev, -3),
                industry=rng.choice(industries),
                fit_score=round(rng.uniform(0.45, 0.99), 3),
                intent_score=round(rng.betavariate(2, 5), 3),
            ))
        return out

    def keyword_universe(self, seeds: list[str], limit: int = 50) -> list[KeywordRecord]:
        modifiers = ["software", "platform", "tools", "vendors", "pricing", "vs",
                     "best", "for enterprise", "automation", "ROI", "implementation",
                     "alternatives", "reviews", "comparison"]
        intents = {"pricing": "transactional", "vs": "commercial", "best": "commercial",
                   "alternatives": "commercial", "reviews": "commercial",
                   "comparison": "commercial", "for enterprise": "commercial"}
        rng = _seed("keywords", *seeds)
        out: list[KeywordRecord] = []
        for seed in seeds:
            for mod in modifiers:
                term = f"{seed} {mod}".strip()
                intent = intents.get(mod, "informational")
                out.append(KeywordRecord(
                    term=term,
                    monthly_volume=int(rng.uniform(40, 8000)),
                    difficulty=int(rng.uniform(8, 78)),
                    intent=intent,
                ))
        rng.shuffle(out)
        return out[:limit]
