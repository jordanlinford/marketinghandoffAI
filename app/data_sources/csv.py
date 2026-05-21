"""
CsvMarketDataSource — backs `market_intel` with a user-uploaded account list.

This is the first real (non-stub) data source. It proves the pattern: the agent
never imports this module — it talks to `MarketDataSource`. ZoomInfo / Apollo
later are the same shape behind the same seam, just an API call instead of a row.

Honesty principles (NOT bugs):
  * intent_score = 0.0 and source = "csv". A static upload has no live intent
    signal. We do not fake one. Live intent needs a feed (ZoomInfo/Apollo).
  * keyword_universe() returns []. CSV has no keyword data. The agent already
    handles empty clusters and the brief renders cleanly without them.
"""
from __future__ import annotations

from app.data_sources.base import CompanyRecord, KeywordRecord, MarketDataSource


def _coerce_int(v) -> int:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _coerce_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _norm_industry(s: str | None) -> str:
    return (s or "").strip().lower()


def fit_score(row: dict, icp: dict) -> float:
    """Native fit-scoring against the ICP — deterministic, explainable, NOT random.

    Score is the average of however many ICP criteria apply:
      * industry match    (1.0 if row industry ∈ icp.industries, else 0.0)
      * employees ≥ min   (1.0 if employees ≥ icp.min_employees, else 0.0)
      * revenue ≥ min     (1.0 if revenue ≥ icp.min_revenue_usd, else 0.0)
    Any ICP criterion that isn't configured is skipped — we don't penalize
    rows for criteria the ICP didn't ask about. If the ICP is empty we return
    0.5 (neutral) so the agent's downstream sizing still has signal."""
    parts: list[float] = []
    industries = {(_norm_industry(s)) for s in (icp.get("industries") or []) if s}
    if industries:
        parts.append(1.0 if _norm_industry(row.get("industry")) in industries else 0.0)
    min_emp = icp.get("min_employees")
    if min_emp is not None:
        parts.append(1.0 if _coerce_int(row.get("employees")) >= int(min_emp) else 0.0)
    min_rev = icp.get("min_revenue_usd")
    if min_rev is not None:
        parts.append(1.0 if _coerce_float(row.get("revenue_usd")) >= float(min_rev) else 0.0)
    if not parts:
        return 0.5
    return round(sum(parts) / len(parts), 3)


class CsvMarketDataSource(MarketDataSource):
    """Backed by an `Upload.rows` payload — list of {name, employees,
    revenue_usd, industry} dicts already normalized at upload time."""

    def __init__(self, rows: list[dict]):
        self._rows = rows or []

    def find_companies(self, icp: dict, limit: int = 100) -> list[CompanyRecord]:
        icp = icp or {}
        out: list[CompanyRecord] = []
        for row in self._rows[:limit]:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            out.append(CompanyRecord(
                name=name,
                employees=_coerce_int(row.get("employees")),
                revenue_usd=_coerce_float(row.get("revenue_usd")),
                industry=(row.get("industry") or "").strip(),
                fit_score=fit_score(row, icp),
                intent_score=0.0,  # honest: a static upload carries no intent signal
                source="csv",
            ))
        return out

    def keyword_universe(self, seeds: list[str], limit: int = 50) -> list[KeywordRecord]:
        # A CSV of accounts has no keyword data. Returning [] is the honest answer;
        # the agent groups by intent and renders cleanly when clusters are empty.
        return []
