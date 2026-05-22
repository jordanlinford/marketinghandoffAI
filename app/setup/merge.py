"""
Profile merge engine for the Setup stage. Layers Setup's inputs (manual /
crawl+knowledge / customer-CSV) additively instead of having each one clobber
the form.

Precedence when sources disagree on a field:
    manual > csv (real data) > knowledge/crawl (guess) > blank

Rules (apply identically in both flows — first-time layering and enrichment):
  * A blank incoming value NEVER overwrites a non-blank existing value.
  * A `manual` field is SACRED — never auto-overwritten. A differing incoming
    value becomes a conflict the user must resolve, not a silent change.
  * Higher-precedence incoming auto-applies over lower (CSV's real median size
    beats a knowledge-guessed "min 500") — but the change is SURFACED, never
    hidden.
  * Equal-precedence disagreement is a conflict (no winner — the user chooses).
  * Every field carries a `source` tag so the UI shows provenance and the merge
    can reason about precedence. ICP is merged per sub-field (industries /
    min_employees / ... each get their own source), so a CSV can ground the
    quantitative ICP while a knowledge draft keeps the qualitative bits.

Pure functions — they compute and return; they never read the DB or write
anything. The API layer owns persistence (PUT remains the only writer).
"""
from __future__ import annotations

import copy

# Higher tier wins. Anything unknown / unset is tier 0 (blank-ish).
_TIER = {"manual": 3, "form": 3, "csv": 2, "knowledge": 1, "crawl": 1, "llm": 1}

# Atomic top-level fields.
_SCALAR_FIELDS = ("product_summary", "value_prop", "brand_voice",
                  "conversion_goal", "conversion_event", "website_url")
_LIST_FIELDS = ("keywords", "competitors", "banned_claims")
_ICP_SUBFIELDS = ("industries", "min_employees", "min_revenue_usd",
                  "regions", "titles", "notes")

# All mergeable fields as dotted paths, in the order the changelist presents.
MERGE_FIELDS = (
    _SCALAR_FIELDS + _LIST_FIELDS + tuple(f"icp.{s}" for s in _ICP_SUBFIELDS)
)


def _tier(source) -> int:
    return _TIER.get((source or "").lower(), 0)


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False  # a non-None number is a real value


def _norm_eq(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    return a == b


def _get(profile: dict, dotted: str):
    if "." in dotted:
        head, tail = dotted.split(".", 1)
        return (profile.get(head) or {}).get(tail)
    return profile.get(dotted)


def _set(profile: dict, dotted: str, value) -> None:
    if "." in dotted:
        head, tail = dotted.split(".", 1)
        profile.setdefault(head, {})
        profile[head][tail] = value
    else:
        profile[dotted] = value


def _expanded_source(profile: dict) -> dict:
    """Return the profile's source map with the umbrella 'icp' key expanded
    into per-subfield 'icp.<sub>' keys (for each non-blank icp sub-value), so
    the merge can track ICP provenance at sub-field granularity. The draft
    endpoints emit a single source['icp']; this normalizes that."""
    src = dict(profile.get("source") or {})
    umbrella = src.pop("icp", None)
    if umbrella:
        icp = profile.get("icp") or {}
        for sub in _ICP_SUBFIELDS:
            key = f"icp.{sub}"
            if key not in src and not _is_blank(icp.get(sub)):
                src[key] = umbrella
    return src


def _collapse_icp_umbrella(source: dict) -> dict:
    """If any per-subfield icp keys exist, drop a stale umbrella 'icp' key so
    provenance stays unambiguous."""
    if any(k.startswith("icp.") for k in source):
        source.pop("icp", None)
    return source


def merge_profiles(base: dict, incoming: dict) -> dict:
    """Layer `incoming` onto a copy of `base`. Returns:
        {"merged": <profile with source>, "changes": [<change>, ...]}
    where each change is:
        {field, old_value, old_source, new_value, new_source,
         conflict: bool, applied: bool}
    `applied` means the value was auto-merged into `merged` (clean precedence
    win or filling a blank). `conflict` means it needs an explicit human
    decision (would overwrite a manual field, or an equal-precedence tie) and
    was therefore NOT auto-applied. Only fields that actually differ appear in
    `changes`. Never raises; writes nothing."""
    merged = copy.deepcopy(base or {})
    merged.setdefault("icp", {})
    out_src = _expanded_source(merged)
    inc_src = _expanded_source(incoming or {})
    changes: list[dict] = []

    for field in MERGE_FIELDS:
        new_val = _get(incoming or {}, field)
        if _is_blank(new_val):
            continue  # blank never overwrites
        old_val = _get(merged, field)
        old_blank = _is_blank(old_val)
        if not old_blank and _norm_eq(old_val, new_val):
            continue  # identical — nothing to do

        new_source = inc_src.get(field)
        old_source = out_src.get(field)

        applied = False
        conflict = False
        if old_blank:
            applied = True  # fill an empty field — clean
        elif _tier(old_source) == 3:  # base is manual/form — sacred
            conflict = True
        else:
            ot, nt = _tier(old_source), _tier(new_source)
            if nt > ot:
                applied = True   # higher precedence wins (e.g. csv > knowledge)
            elif nt == ot:
                conflict = True  # tie — user chooses
            else:
                continue         # lower precedence loses; not a proposed change

        if applied:
            _set(merged, field, new_val)
            out_src[field] = new_source

        changes.append({
            "field": field,
            "old_value": None if old_blank else old_val,
            "old_source": old_source,
            "new_value": new_val,
            "new_source": new_source,
            "conflict": conflict,
            "applied": applied,
        })

    merged["source"] = _collapse_icp_umbrella(out_src)
    return {"merged": merged, "changes": changes}


def apply_accepted(base: dict, changes: list[dict], accepted: set[str]) -> dict:
    """Build the final profile by applying ONLY the accepted changes' new
    values onto a copy of `base`. Used by the enrichment flow: the user picks
    which proposed changes to keep, and the client PUTs the result. Rejected
    fields keep their base value. Pure function."""
    out = copy.deepcopy(base or {})
    out.setdefault("icp", {})
    out_src = _expanded_source(out)
    for ch in changes:
        if ch["field"] not in accepted:
            continue
        _set(out, ch["field"], ch["new_value"])
        out_src[ch["field"]] = ch["new_source"]
    out["source"] = _collapse_icp_umbrella(out_src)
    return out
