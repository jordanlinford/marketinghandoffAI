"""
Draft OrgProfile builder for the Setup stage. Two entry paths:

  * `draft_from_crawl(crawl_result)` — turn the cleaned website text into a
    proposed profile (product, value prop, ICP guess, competitors, keywords,
    conversion goal). When ANTHROPIC_API_KEY is set we ask Claude for a
    structured JSON proposal; otherwise we synthesize a minimal draft from
    title/meta/headings so the flow still works.
  * `draft_from_csv_rows(rows)` — infer ICP fields from an uploaded customer
    sample (most-common industries, median size/revenue).

Both return a DRAFT, never a saved profile. `confirmed` is always False, and
every field the system proposed is tagged in `source` ("llm" | "crawl" | "csv")
so the UI can render "we guessed this — confirm it" instead of presenting a
draft as fact. Same honesty rule as the CSV intent column.

LLM-or-template fallback pattern is the same one used by
`app.agents.synthesis.synthesize_brief` — see that module for the reference
shape. We don't invent a new pattern here.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter

from app.config import get_settings

_LLM_FIELDS = (
    "product_summary", "value_prop", "icp", "competitors",
    "keywords", "conversion_goal",
)
# Max characters of crawl text we send to the LLM. The crawler also caps, so
# this is a belt-and-suspenders bound on per-call cost.
_LLM_INPUT_CAP = 24_000


def draft_from_crawl(crawl_result: dict) -> dict:
    """Build a draft profile from a crawl result. Always returns a dict; never
    raises. If the LLM is unavailable or the call fails, falls back to a
    minimal draft derived from title/meta/headings."""
    settings = get_settings()
    base = _minimal_from_crawl(crawl_result)

    if not crawl_result.get("text") or not settings.anthropic_api_key:
        return base

    try:
        proposal = _llm_propose(crawl_result["text"], settings)
    except Exception:
        return base  # silent fallback — same discipline as synthesis.py

    # Merge LLM proposal over the minimal base. We trust the LLM only for the
    # fields it actually returned and that pass shape validation; everything
    # else keeps the deterministic fallback.
    merged = dict(base)
    source = dict(base.get("source") or {})
    for field in _LLM_FIELDS:
        if field not in proposal:
            continue
        value = _coerce_field(field, proposal[field])
        if value is None:
            continue
        merged[field] = value
        source[field] = "llm"
    merged["source"] = source
    return merged


def draft_from_csv_rows(rows: list[dict]) -> dict:
    """Infer ICP fields from a customer sample. Rows are the same shape
    produced by `app.api.uploads._parse_csv` / `CsvMarketDataSource`:
    {name, employees, revenue_usd, industry, intent_score?}. Returns a
    draft profile with `source` marked "csv" for inferred fields."""
    industries = [
        (r.get("industry") or "").strip()
        for r in rows if (r.get("industry") or "").strip()
    ]
    top_industries = [name for name, _ in Counter(industries).most_common(5)]

    employees = [int(r["employees"]) for r in rows
                 if isinstance(r.get("employees"), (int, float)) and r.get("employees")]
    revenues = [float(r["revenue_usd"]) for r in rows
                if isinstance(r.get("revenue_usd"), (int, float)) and r.get("revenue_usd")]

    icp = {
        "industries": top_industries,
        "min_employees": int(statistics.median(employees)) if employees else None,
        "min_revenue_usd": float(statistics.median(revenues)) if revenues else None,
        "regions": [],
        "titles": [],
        "notes": f"Inferred from {len(rows)} customer accounts.",
    }
    return {
        "product_summary": "",
        "value_prop": "",
        "icp": icp,
        "competitors": [],
        "keywords": [],
        "brand_voice": "",
        "banned_claims": [],
        "conversion_goal": "",
        "conversion_event": "",
        "website_url": "",
        "crawl_summary": "",
        "source": {"icp": "csv"},
        "confirmed": False,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _minimal_from_crawl(crawl_result: dict) -> dict:
    """Deterministic fallback. Even when the crawl returned status != 'ok' we
    still emit a draft skeleton with whatever scraps were extractable, so the
    user gets a half-filled form instead of an error wall."""
    title = (crawl_result.get("title") or "").strip()
    meta = (crawl_result.get("meta_description") or "").strip()
    headings = crawl_result.get("headings") or []
    summary = " — ".join(p for p in (title, meta) if p) or (headings[0] if headings else "")

    crawl_summary = ""
    status = crawl_result.get("status")
    if status and status != "ok":
        crawl_summary = (
            f"[crawl status: {status}] {crawl_result.get('message') or ''}".strip()
        )

    draft = {
        "product_summary": summary,
        "value_prop": meta or "",
        "icp": {"industries": [], "min_employees": None, "min_revenue_usd": None,
                "regions": [], "titles": [], "notes": ""},
        "competitors": [],
        "keywords": _keywords_from_text(headings, meta, title),
        "brand_voice": "",
        "banned_claims": [],
        "conversion_goal": "",
        "conversion_event": "",
        "website_url": crawl_result.get("url") or "",
        "crawl_summary": crawl_summary,
        "source": {},
        "confirmed": False,
    }
    # Mark any field the deterministic pass actually populated. Empty strings /
    # empty lists stay unsourced so the UI doesn't flag a blank as "suggested".
    sourced = {}
    if draft["product_summary"]:
        sourced["product_summary"] = "crawl"
    if draft["value_prop"]:
        sourced["value_prop"] = "crawl"
    if draft["keywords"]:
        sourced["keywords"] = "crawl"
    draft["source"] = sourced
    return draft


def _keywords_from_text(headings: list[str], meta: str, title: str) -> list[str]:
    """Pull a handful of candidate keywords from the structural hints. This is
    a deterministic fallback — the LLM, when available, replaces it."""
    bag: list[str] = []
    for h in headings[:6]:
        bag.extend(_phrases(h))
    bag.extend(_phrases(meta))
    bag.extend(_phrases(title))
    seen: set[str] = set()
    out: list[str] = []
    for term in bag:
        key = term.lower()
        if len(term) < 4 or key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= 10:
            break
    return out


def _phrases(text: str) -> list[str]:
    """Cheap 2-3 word phrase extractor — splits on punctuation, returns
    word-pairs/triples. Good enough for a fallback seed; the LLM does better
    when available."""
    if not text:
        return []
    chunks = re.split(r"[|,•·:;()\[\]–—\-\n\r\t]+", text)
    out: list[str] = []
    for chunk in chunks:
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9'+&]{2,}", chunk)]
        for n in (3, 2):
            for i in range(len(words) - n + 1):
                out.append(" ".join(words[i:i + n]))
    return out


def _llm_propose(text: str, settings) -> dict:
    """Ask Claude for a strict-JSON proposal. Returns a dict; raises on any
    transport or parse failure so the caller can fall back."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = (
        "You are helping a B2B marketing team set up their account profile. "
        "Based ONLY on the website content below, propose a draft profile. "
        "Do NOT invent facts you cannot ground in the text. If a field is "
        "unclear, return an empty string / empty list rather than guessing.\n\n"
        "Return ONLY a single JSON object, no prose, with these exact keys:\n"
        '  "product_summary": string  (1-2 sentences, what the product does)\n'
        '  "value_prop":      string  (1 sentence, why a buyer would pick it)\n'
        '  "icp": {\n'
        '     "industries":      [string],\n'
        '     "min_employees":   integer or null,\n'
        '     "min_revenue_usd": number or null,\n'
        '     "regions":         [string],\n'
        '     "titles":          [string],\n'
        '     "notes":           string\n'
        '  }\n'
        '  "competitors":     [{"name": string, "url": string (optional)}]\n'
        '  "keywords":        [string]  (5-15 search terms a buyer might use)\n'
        '  "conversion_goal": string    (e.g. "book a demo", "start free trial")\n\n'
        f"WEBSITE CONTENT:\n{text[:_LLM_INPUT_CAP]}"
    )
    msg = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    return _parse_json_blob(raw)


def _parse_json_blob(raw: str) -> dict:
    """Defensive parse. Models sometimes wrap JSON in ```json fences or add a
    sentence before/after. Pull out the first {...} block and parse that."""
    raw = raw.strip()
    if raw.startswith("```"):
        # strip ```json ... ``` fences
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("LLM response contained no JSON object")
    return json.loads(match.group(0))


def _coerce_field(field: str, value):
    """Light shape validation. Returns None when the value doesn't look like
    the right type — the caller then keeps the deterministic fallback for
    that field instead of trusting garbage."""
    if field in ("product_summary", "value_prop", "conversion_goal"):
        if isinstance(value, str):
            return value.strip()
        return None
    if field == "keywords":
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return None
    if field == "competitors":
        if not isinstance(value, list):
            return None
        out = []
        for item in value:
            if isinstance(item, dict) and item.get("name"):
                out.append({"name": str(item["name"]).strip(),
                            "url": str(item.get("url") or "").strip()})
            elif isinstance(item, str) and item.strip():
                out.append({"name": item.strip(), "url": ""})
        return out
    if field == "icp":
        if not isinstance(value, dict):
            return None
        return {
            "industries": [str(x).strip() for x in (value.get("industries") or [])
                           if str(x).strip()],
            "min_employees": _as_int(value.get("min_employees")),
            "min_revenue_usd": _as_float(value.get("min_revenue_usd")),
            "regions": [str(x).strip() for x in (value.get("regions") or [])
                        if str(x).strip()],
            "titles": [str(x).strip() for x in (value.get("titles") or [])
                       if str(x).strip()],
            "notes": str(value.get("notes") or "").strip(),
        }
    return None


def _as_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
