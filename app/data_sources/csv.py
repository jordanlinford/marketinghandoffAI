"""
CsvMarketDataSource — backs `market_intel` with a user-uploaded account list.

This is the first real (non-stub) data source. It proves the pattern: the agent
never imports this module — it talks to `MarketDataSource`. ZoomInfo / Apollo
later are the same shape behind the same seam, just an API call instead of a row.

Honesty principles (NOT bugs):
  * If the CSV did not carry an intent column, intent_score = 0.0 and
    source = "csv". A static upload has no live intent signal and we will
    not fake one. The UI shows an "intent unavailable" banner in this case.
  * If the CSV DID carry an intent column, we use it: source = "csv+intent"
    and intent_score comes from the file. Honesty rule preserved — we only
    surface intent when the file actually provides it.
  * keyword_universe() returns []. CSV has no keyword data. The agent already
    handles empty clusters and the brief renders cleanly without them.
"""
from __future__ import annotations

import re

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


_SUFFIX_MULT = {"k": 1e3, "m": 1e6, "b": 1e9}
# Range separators: hyphen, en-dash, em-dash, double-hyphen, or the literal
# word "to" (case-insensitive). Whitespace around the separator is optional.
_RANGE_SPLIT = re.compile(r"\s*(?:--|–|—|-|\bto\b)\s*", re.IGNORECASE)


def _parse_money_token(s: str) -> float | None:
    """Parse a single money-like token: '$1M', '1,000,000', '500K', '5.2B'.
    Returns None when nothing usable is in the string."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("$", "").replace(" ", "")
    if not t:
        return None
    mult = 1.0
    if t[-1] in "kKmMbB":
        mult = _SUFFIX_MULT[t[-1].lower()]
        t = t[:-1]
    if not t:
        return None
    try:
        return float(t) * mult
    except ValueError:
        return None


def _parse_revenue(v) -> float | None:
    """Parse a revenue cell. Accepts a number, a single money token
    ('$5M', '1,000,000'), or a range ('$1M-$5M', '500K-1M',
    '1,000,000-5,000,000', '1M to 5M'). Returns the midpoint of a range,
    the value when only one is present, or None when nothing parseable."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    parts = [p for p in _RANGE_SPLIT.split(s) if p.strip()]
    nums = [n for n in (_parse_money_token(p) for p in parts) if n is not None]
    if not nums:
        return None
    if len(nums) >= 2:
        return (nums[0] + nums[-1]) / 2.0
    return nums[0]


def _parse_intent(v) -> float | None:
    """Parse an intent cell to a 0..1 float. Values that look 0..100 are
    divided by 100 (so 75 -> 0.75). Strips a trailing '%'. Returns None when
    nothing parseable; clamps to [0.0, 1.0]."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        s = str(v).strip().replace("%", "").replace(",", "")
        if not s:
            return None
        try:
            n = float(s)
        except ValueError:
            return None
    if n > 1.0:
        n = n / 100.0
    if n < 0.0:
        n = 0.0
    if n > 1.0:
        n = 1.0
    return n


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
    revenue_usd, industry, intent_score?} dicts already normalized at upload
    time. If any row carries an `intent_score` key the source advertises
    `csv+intent`; otherwise it stays the honest `csv` (intent=0.0)."""

    def __init__(self, rows: list[dict]):
        self._rows = rows or []
        # Presence of the column drives the source label, not per-row values.
        # A blank intent cell in a column that exists still means "the dataset
        # has intent" — that row just didn't have a score.
        self._has_intent = any("intent_score" in r for r in self._rows)

    def find_companies(self, icp: dict, limit: int = 100) -> list[CompanyRecord]:
        icp = icp or {}
        out: list[CompanyRecord] = []
        source = "csv+intent" if self._has_intent else "csv"
        for row in self._rows[:limit]:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            intent = _coerce_float(row.get("intent_score")) if self._has_intent else 0.0
            out.append(CompanyRecord(
                name=name,
                employees=_coerce_int(row.get("employees")),
                revenue_usd=_coerce_float(row.get("revenue_usd")),
                industry=(row.get("industry") or "").strip(),
                fit_score=fit_score(row, icp),
                intent_score=intent,
                source=source,
            ))
        return out

    def keyword_universe(self, seeds: list[str], limit: int = 50) -> list[KeywordRecord]:
        # A CSV of accounts has no keyword data. Returning [] is the honest answer;
        # the agent groups by intent and renders cleanly when clusters are empty.
        return []
