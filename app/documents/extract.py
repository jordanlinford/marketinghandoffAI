"""
LLM extraction — turns a normalized doc text into candidate insights.

Discipline (do not drift):
  * Reuse the synthesis.py LLM-or-fallback pattern. WITHOUT an API key OR
    on any LLM error → return None. Callers (the worker) mark the doc
    `failed` with a clear extraction_error. We never confabulate candidates
    without an LLM.
  * Each candidate carries its verbatim source_passage (≤ ~30 words). The
    user audits what the model read.
  * Blank fields in the LLM output produce NO candidate row — the model
    is explicitly allowed to say "the doc is silent on this." Same
    discipline as the org-profile knowledge draft.
  * NEVER produce a candidate for an *_override field. Overriding
    inheritance is a deliberate human decision (see brief). The
    permitted-fields whitelist is enforced here AND asserted in smoke.

Cost is captured by the worker on the run record.
"""
from __future__ import annotations

import json

from app.config import get_settings


# Canonical ProductProfile target fields extraction is allowed to write
# candidates against. The override fields (brand_voice_override, etc.) are
# DELIBERATELY ABSENT — extraction is not allowed to set product-level
# overrides without explicit human action.
CANONICAL_FIELDS: tuple[str, ...] = (
    "positioning",
    "target_persona",
    "value_props",
    "proof_points",
    "differentiators",
    "key_features",
    "use_cases",
    "product_competitors",
)

# Messaging-notes target names — accepted as candidate field_names too, but
# they promote into ProductProfile.messaging_notes (JSON), not into a
# normalized column.
MESSAGING_NOTE_FIELDS: tuple[str, ...] = (
    "objection_handling",
    "launch_messaging",
)

# The union of permitted field_name values. Anything else the model returns
# is dropped silently — the schema is the contract.
PERMITTED_FIELDS: frozenset[str] = frozenset(CANONICAL_FIELDS + MESSAGING_NOTE_FIELDS)

# Reject-list: extraction MUST NEVER set these. Asserted in smoke.
FORBIDDEN_FIELDS: frozenset[str] = frozenset((
    "brand_voice_override",
    "banned_claims_override",
    "conversion_goal_override",
    "rubric_override",
    "utm_source_default",
    "utm_medium_default",
))


def extract_candidates(doc_text: str, org_profile: dict, product_profile: dict,
                       doc_kind: str = "messaging_framework") -> tuple[list[dict] | None, float]:
    """Returns (candidates, cost_usd). `candidates` is None when no LLM is
    available OR the call failed in any way — the worker translates that
    into a failed doc with a clear extraction_error. On success returns a
    list of {field_name, value, confidence, source_passage} ready for the
    worker to persist as ExtractedInsight rows."""
    settings = get_settings()
    if not (doc_text or "").strip():
        return [], 0.0
    if not settings.anthropic_api_key:
        return None, 0.0
    try:
        return _llm_extract(doc_text, org_profile, product_profile, doc_kind, settings)
    except Exception:
        # Defensive: never let extraction crash a worker job. Caller marks
        # the doc failed and surfaces the error to the user.
        return None, 0.0


def _llm_extract(doc_text: str, org_profile: dict, product_profile: dict,
                 doc_kind: str, settings) -> tuple[list[dict], float]:
    """Ask Claude for a structured per-field JSON, then defensively
    flatten + filter to PERMITTED_FIELDS only. Defensive parse: any
    malformed shape → empty candidate list (treated as a failed extraction
    by the worker, not silently empty)."""
    import anthropic

    grounding = {
        "org": {
            "product_summary": (org_profile or {}).get("product_summary", ""),
            "value_prop": (org_profile or {}).get("value_prop", ""),
            "brand_voice": (org_profile or {}).get("brand_voice", ""),
            "banned_claims": (org_profile or {}).get("banned_claims") or [],
            "icp": (org_profile or {}).get("icp") or {},
        },
        "product": {
            "name": (product_profile or {}).get("name", ""),
            "positioning": (product_profile or {}).get("positioning", ""),
            "value_props": (product_profile or {}).get("value_props") or [],
            "key_features": (product_profile or {}).get("key_features") or [],
            "product_competitors":
                (product_profile or {}).get("product_competitors") or [],
        },
        "doc_kind": doc_kind,
    }
    field_lines = "\n".join(f"  - {f}" for f in CANONICAL_FIELDS)
    notes_lines = "\n".join(f"  - {f}" for f in MESSAGING_NOTE_FIELDS)
    forbidden_lines = ", ".join(sorted(FORBIDDEN_FIELDS))
    prompt = (
        "Extract durable product intelligence from the document below. "
        "Return ONLY a JSON object with this shape (no prose, no markdown):\n\n"
        "{\n"
        '  "<field_name>": {\n'
        '    "value": <string | list | dict, depending on field>,\n'
        '    "confidence": <float 0..1>,\n'
        '    "source_passage": "<verbatim quote from the doc, ≤ 30 words>"\n'
        '  },\n'
        '  ...\n'
        "}\n\n"
        f"CANONICAL FIELDS (write into the corresponding ProductProfile field):\n{field_lines}\n\n"
        f"MESSAGING-NOTE FIELDS (write into messaging_notes; value should be a list):\n"
        f"{notes_lines}\n\n"
        f"FORBIDDEN: NEVER produce a field named: {forbidden_lines}.\n"
        "These are inheritable-override fields — overriding org inheritance "
        "is a deliberate human decision, not an automatic one. If the doc "
        "calls them out, capture the relevant quote under launch_messaging "
        "or objection_handling so the user can manually consider it.\n\n"
        "HONESTY RULES:\n"
        "  * If the document is SILENT on a field, OMIT IT from the JSON. "
        "Do not guess. An omitted field is the right answer.\n"
        "  * source_passage must be a verbatim quote from the document; "
        "do not paraphrase.\n"
        "  * confidence is a hint, not an oath — be conservative for "
        "inferred values.\n\n"
        f"GROUNDING (existing org + product context):\n{json.dumps(grounding, indent=2)}\n\n"
        f"DOCUMENT:\n{doc_text[:30000]}"   # cap to avoid an oversized prompt
    )
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model, max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    parsed = _parse_extraction_json(raw)
    if parsed is None:
        # Defensive parse failure — treat as no result so the caller marks
        # the doc failed rather than persisting nothing silently.
        raise ValueError(f"Extractor returned non-JSON: {raw[:200]!r}")
    candidates = _normalize_candidates(parsed)
    usage = getattr(msg, "usage", None)
    cost = 0.0
    if usage:
        cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
    return candidates, round(cost, 6)


def _parse_extraction_json(raw: str) -> dict | None:
    """Try strict JSON first; on failure try to peel off a markdown fence.
    Returns None if both fail."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except Exception:
        pass
    if raw.startswith("```"):
        # Tolerate ```json ... ``` if the model insists on a fence.
        body = raw.strip("`").strip()
        body = body.split("\n", 1)[1] if "\n" in body and body.startswith("json") else body
        try:
            v = json.loads(body)
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


def _normalize_candidates(parsed: dict) -> list[dict]:
    """Flatten the LLM JSON into a list[dict] of candidate insight payloads.
    Drops:
      * fields that aren't in PERMITTED_FIELDS,
      * blank/empty values,
      * forbidden field names (defense in depth — the prompt also says no)."""
    out: list[dict] = []
    for field, payload in (parsed or {}).items():
        if field in FORBIDDEN_FIELDS:
            continue
        if field not in PERMITTED_FIELDS:
            continue
        if not isinstance(payload, dict):
            continue
        value = payload.get("value")
        if _is_blank(value):
            continue
        try:
            conf = float(payload.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        source_passage = str(payload.get("source_passage") or "").strip()
        # Cap the verbatim quote so we never blow up the row with the
        # whole doc by accident.
        source_passage = source_passage[:600]
        out.append({
            "field_name": field,
            "value": value,
            "confidence": conf,
            "source_passage": source_passage,
        })
    return out


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    return False
