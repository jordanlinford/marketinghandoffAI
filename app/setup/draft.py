"""
Draft OrgProfile builder for the Setup stage. Three entry paths:

  * `draft_from_crawl(crawl_result)` — turn the cleaned website text into a
    proposed profile (product, value prop, ICP guess, competitors, keywords,
    conversion goal). When ANTHROPIC_API_KEY is set we ask Claude for a
    structured JSON proposal; otherwise we synthesize a minimal draft from
    title/meta/headings so the flow still works.
  * `draft_from_knowledge(domain, name=...)` — used when we COULDN'T read the
    site (Cloudflare 403, DNS miss, JS-only page). Asks Claude to draft the
    same structured profile FROM ITS OWN KNOWLEDGE of the company at that
    domain, with explicit instructions to leave fields blank rather than
    invent details for companies it doesn't recognize. Tags source="knowledge"
    so the UI labels it honestly.
  * `draft_from_csv_rows(rows)` — infer ICP fields from an uploaded customer
    sample (most-common industries, median size/revenue).

All three return a DRAFT, never a saved profile. `confirmed` is always False,
and every field the system proposed is tagged in `source` ("llm" | "crawl" |
"knowledge" | "csv") so the UI can render "we guessed this — confirm it"
instead of presenting a draft as fact. Same honesty rule as the CSV intent
column.

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

    if not settings.anthropic_api_key:
        base["llm_error"] = _no_key_note()
        return base
    if not crawl_result.get("text"):
        return base  # nothing to send the LLM; not an LLM failure

    try:
        proposal = _llm_propose(crawl_result["text"], settings)
    except Exception as exc:
        # Fall back to the deterministic draft, but DON'T swallow the reason —
        # surface it so the UI can say "AI enrichment unavailable: <why>"
        # instead of silently presenting a thinner draft. (See onit.com /
        # "credit balance too low" incident.)
        base["llm_error"] = _llm_error_payload(exc)
        return base

    # Merge LLM proposal over the minimal base. We trust the LLM only for the
    # fields it actually returned and that pass shape validation; everything
    # else keeps the deterministic fallback. Empty values (the model's signal
    # for "I don't know") get the value but NOT the source tag — the UI uses
    # source to mark "suggested", and a blank field shouldn't pose as a suggestion.
    merged = dict(base)
    source = dict(base.get("source") or {})
    for field in _LLM_FIELDS:
        if field not in proposal:
            continue
        value = _coerce_field(field, proposal[field])
        if value is None:
            continue
        merged[field] = value
        if not _is_empty(value):
            source[field] = "llm"
    merged["source"] = source
    return merged


def draft_from_knowledge(domain: str, name: str = "") -> dict:
    """Draft a profile from what the LLM already knows about the company at
    this domain — used when the crawl could NOT read the site (Cloudflare,
    DNS, JS-only). Never raises.

    Honesty discipline: the prompt explicitly tells the model to return empty
    fields rather than confabulate. A mostly-blank result is the correct
    outcome for an unknown company; the API surfaces that as
    draft_source="skeleton" and the UI nudges the user to manual entry.

    With no API key (or on any LLM failure), returns the same minimal skeleton
    the no-key crawl path returns — the form is still the always-works fallback."""
    settings = get_settings()
    base = _minimal_from_domain(domain, name)
    if not settings.anthropic_api_key:
        base["llm_error"] = _no_key_note()
        return base

    try:
        proposal = _llm_propose_from_knowledge(domain, name, settings)
    except Exception as exc:
        # Surface the real reason. Without this, an account-level failure
        # (credit balance too low, bad key, rate limit) is indistinguishable
        # from "the model didn't recognize the company" — both produce a blank
        # skeleton, and the user can't tell which.
        base["llm_error"] = _llm_error_payload(exc)
        return base

    merged = dict(base)
    source = dict(base.get("source") or {})
    for field in _LLM_FIELDS:
        if field not in proposal:
            continue
        value = _coerce_field(field, proposal[field])
        if value is None:
            continue
        merged[field] = value
        # Only tag non-empty values. An empty list / empty string from the
        # model is its honest "I don't know" answer for that field — we keep
        # the blank but do NOT mark it "suggested," so the UI shows it as an
        # empty field the user fills, not as a hollow suggestion.
        if not _is_empty(value):
            source[field] = "knowledge"
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


# Shared JSON schema spec for both LLM prompts (crawl-text and from-knowledge).
# Whatever the input source, the OUTPUT shape is identical so the merge logic
# in `draft_from_*` doesn't have to branch on input mode.
_PROFILE_JSON_SPEC = (
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
    '  "conversion_goal": string    (e.g. "book a demo", "start free trial")'
)


def _llm_propose(text: str, settings) -> dict:
    """Ask Claude for a strict-JSON proposal from crawled website text.
    Returns a dict; raises on any transport or parse failure so the caller
    can fall back."""
    prompt = (
        "You are helping a B2B marketing team set up their account profile. "
        "Based ONLY on the website content below, propose a draft profile. "
        "Do NOT invent facts you cannot ground in the text. If a field is "
        "unclear, return an empty string / empty list rather than guessing.\n\n"
        f"{_PROFILE_JSON_SPEC}\n\n"
        f"WEBSITE CONTENT:\n{text[:_LLM_INPUT_CAP]}"
    )
    return _llm_json_call(prompt, settings)


def _llm_propose_from_knowledge(domain: str, name: str, settings) -> dict:
    """Ask Claude for a strict-JSON proposal from its own knowledge of the
    company at this domain — used when we COULD NOT read the site. The
    instructions explicitly allow (and require) blank fields for companies
    the model doesn't recognize. Returns a dict; raises on any transport or
    parse failure so the caller can fall back to the minimal skeleton."""
    prompt = (
        "You are helping a B2B marketing team set up their account profile. "
        "We could NOT read the company's website (it is blocked by anti-bot "
        "protection, JS-only, or otherwise unreachable). Using ONLY your "
        "existing knowledge of the company at the domain below, propose a "
        "draft profile.\n\n"
        "CRITICAL HONESTY RULE: if you do NOT actually recognize this company, "
        "return an empty string / empty list / null for every field. Do NOT "
        "invent details, do NOT guess based on the domain name's resemblance "
        "to other companies, and do NOT confabulate. A blank draft the user "
        "fills in is the correct outcome; a confabulated draft presented as "
        "fact is the failure mode we are avoiding. Same rule applies field-"
        "by-field: leave specific fields blank if you don't know that part.\n\n"
        f"{_PROFILE_JSON_SPEC}\n\n"
        f"COMPANY:\n  domain: {domain}\n  name guess: {name or '(unknown)'}"
    )
    return _llm_json_call(prompt, settings)


def _no_key_note() -> dict:
    return {
        "type": "NoApiKey",
        "message": "ANTHROPIC_API_KEY is not set",
        "friendly": "AI drafting is off (no ANTHROPIC_API_KEY) — fill the form manually.",
    }


def _llm_error_payload(exc: Exception) -> dict:
    """Turn an LLM exception into a structured, surfaceable reason. `friendly`
    is a one-liner the UI can show directly; `type`/`message` are kept for the
    dev. We special-case the common operational failures so the user gets an
    actionable sentence instead of a raw SDK traceback string."""
    msg = str(exc) or exc.__class__.__name__
    name = exc.__class__.__name__
    low = msg.lower()
    if "credit balance is too low" in low:
        friendly = ("Anthropic credit balance is too low — add credits at "
                    "console.anthropic.com to enable AI drafting.")
    elif name == "RateLimitError" or "rate limit" in low:
        friendly = "Anthropic rate limit hit — try again in a moment."
    elif name == "AuthenticationError" or "authentication" in low or "invalid x-api-key" in low:
        friendly = "Anthropic rejected the API key (authentication failed)."
    elif name in ("APITimeoutError", "APIConnectionError") or "timeout" in low:
        friendly = "Couldn't reach the Anthropic API (network/timeout) — try again."
    else:
        friendly = f"AI drafting failed ({name}). Fill the form manually."
    return {"type": name, "message": msg, "friendly": friendly}


def _llm_json_call(prompt: str, settings) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    return _parse_json_blob(raw)


def _minimal_from_domain(domain: str, name: str) -> dict:
    """No-key / LLM-failed fallback for the knowledge path. The user gets a
    blank form with the domain prefilled — same skeleton the no-key crawl
    path returns, so the always-works-manually rule holds."""
    return {
        "product_summary": "",
        "value_prop": "",
        "icp": {"industries": [], "min_employees": None, "min_revenue_usd": None,
                "regions": [], "titles": [], "notes": ""},
        "competitors": [],
        "keywords": [],
        "brand_voice": "",
        "banned_claims": [],
        "conversion_goal": "",
        "conversion_event": "",
        "website_url": _domain_to_url(domain),
        "crawl_summary": "",
        "source": {},
        "confirmed": False,
    }


def _domain_to_url(domain: str) -> str:
    d = (domain or "").strip()
    if not d:
        return ""
    if d.startswith(("http://", "https://")):
        return d
    return "https://" + d.lstrip("/")


def name_from_domain(domain: str) -> str:
    """Cheap heuristic so the LLM has a name to anchor to: drop scheme, drop
    leading 'www.', take the host's first label, replace '-/_' with spaces,
    title-case. 'www.acme-corp.io' -> 'Acme Corp'. Best-effort only; the LLM
    is given the domain too and will correct obviously-wrong guesses."""
    from urllib.parse import urlparse
    d = (domain or "").strip()
    if d.startswith(("http://", "https://")):
        d = urlparse(d).netloc
    d = d.lower().lstrip(".")
    if d.startswith("www."):
        d = d[4:]
    first = d.split(".", 1)[0] if d else ""
    cleaned = re.sub(r"[-_]+", " ", first).strip()
    return cleaned.title()


def _is_empty(value) -> bool:
    """True when a value is the model's honest 'I don't know' answer. We use
    this to decide whether to mark a field as 'suggested' in source — a blank
    field should NOT pose as a suggestion in the UI."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, dict):
        # An icp dict counts as empty only if EVERY sub-value is itself empty.
        return all(_is_empty(v) for v in value.values())
    return False


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
