"""
Promotion — accepted/edited candidates land in ProductProfile via the
additive merge.

Precedence (same shape as the org-profile Setup merge):
    manual edits > most-recently-accepted extracted > earlier-accepted

Single-value fields (positioning): the new accepted value becomes the
ProductProfile column; the prior active entry in field_history flips to
`superseded`; the new entry is `active`. Nothing is deleted.

List-valued fields (value_props, proof_points, differentiators,
key_features, use_cases, product_competitors): case-insensitive dedupe +
append. Existing items retained. Removal is an explicit user action via
PATCH /api/products/{id}, not an implicit consequence of promotion.

Messaging notes (objection_handling, launch_messaging): append into the
corresponding list in ProductProfile.messaging_notes with the source
passage + doc reference retained.

Every promotion writes a field_history entry referencing the source
insight id, so audit + rollback work end-to-end.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.documents.extract import (CANONICAL_FIELDS, FORBIDDEN_FIELDS,
                                   MESSAGING_NOTE_FIELDS)
from app.models import ExtractedInsight, ProductProfile


# Fields whose values are single scalars (not lists). For these, "promote"
# means replace the current value (and supersede the prior history entry).
_SINGLE_VALUE_CANONICAL: frozenset[str] = frozenset((
    "positioning", "target_persona",
))
# target_persona is a dict but it's "the persona", not a list — single-
# value semantics.

# Fields whose values are lists. For these, "promote" means append-with-
# dedupe; the column is the union of accepted + manual.
_LIST_VALUE_CANONICAL: frozenset[str] = frozenset((
    "value_props", "proof_points", "differentiators",
    "key_features", "use_cases", "product_competitors",
))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_str(s: Any) -> str:
    """Normalize a string for case-insensitive dedupe. Strips, lowercases,
    collapses whitespace. Returns "" for non-strings."""
    if not isinstance(s, str):
        return ""
    return " ".join(s.strip().lower().split())


def _value_key(value: Any) -> str:
    """Dedupe key for list-field items. For strings: normalized text. For
    dicts (e.g. {name, url} competitors): the name field's normalized
    text. Falls back to repr() for anything else so the dedupe is safe
    without claiming false positives."""
    if isinstance(value, str):
        return _norm_str(value)
    if isinstance(value, dict):
        # Common shape: {"name": "...", "url": "..."}.
        for key in ("name", "value", "objection"):
            if key in value:
                return _norm_str(value[key])
        return repr(sorted(value.items()))
    return repr(value)


def _merge_list_append(existing: list, new_items: list) -> tuple[list, int]:
    """Append new_items into existing, deduping by _value_key. Returns
    (merged_list, num_added)."""
    seen = {_value_key(v) for v in (existing or [])}
    out = list(existing or [])
    added = 0
    for item in new_items or []:
        key = _value_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        added += 1
    return out, added


def _history_append(profile: ProductProfile, field: str, value: Any,
                    insight_id: str | None) -> None:
    """Add a new active entry for `field` and supersede any prior active
    entries for the same field. field_history is JSON, so we work on a
    plain list and reassign so SQLAlchemy notices the change."""
    history = list(profile.field_history or [])
    for entry in history:
        if entry.get("field") == field and entry.get("status") == "active":
            entry["status"] = "superseded"
            entry["superseded_at"] = _now_iso()
    history.append({
        "field": field,
        "value": value,
        "accepted_from_insight_id": insight_id,
        "accepted_at": _now_iso(),
        "status": "active",
    })
    profile.field_history = history


def promote_insight(profile: ProductProfile, insight: ExtractedInsight) -> dict:
    """Promote an accepted/edited insight into the ProductProfile. Returns
    a small diagnostic dict the API surfaces back to the UI ({field,
    promoted_value, mode}). Raises ValueError on a forbidden field — defense
    in depth (extract.py also filters), so even a hand-crafted PATCH can't
    sneak past."""
    field = insight.field_name
    if field in FORBIDDEN_FIELDS:
        raise ValueError(
            f"Refusing to promote insight into forbidden field {field!r}. "
            f"Override fields require explicit human action, not extraction.")

    # Accepted value defaults to the original LLM-extracted value; an edit
    # supplies its own accepted_value.
    value = insight.accepted_value if insight.accepted_value is not None \
        else insight.value

    if field in _SINGLE_VALUE_CANONICAL:
        setattr(profile, field, value)
        _history_append(profile, field, value, insight.id)
        return {"field": field, "mode": "single", "promoted_value": value}

    if field in _LIST_VALUE_CANONICAL:
        existing = getattr(profile, field, None) or []
        # An accepted single value is wrapped in a list; an accepted list
        # is taken as-is. Templates already handle both shapes.
        new_items = value if isinstance(value, list) else [value]
        merged, added = _merge_list_append(existing, new_items)
        setattr(profile, field, merged)
        _history_append(profile, field, new_items, insight.id)
        return {"field": field, "mode": "list",
                "promoted_value": new_items, "added": added,
                "list_size": len(merged)}

    if field in MESSAGING_NOTE_FIELDS:
        notes = dict(profile.messaging_notes or {})
        bucket = list(notes.get(field) or [])
        items = value if isinstance(value, list) else [value]
        for item in items:
            entry = item if isinstance(item, dict) else {"note": str(item)}
            # Retain source_passage + doc reference for audit. Promotion
            # path always carries these (the worker stamps them).
            entry.setdefault("source_passage", insight.source_passage or "")
            entry.setdefault("source_doc_id", insight.product_document_id)
            bucket.append(entry)
        notes[field] = bucket
        profile.messaging_notes = notes
        _history_append(profile, field, items, insight.id)
        return {"field": field, "mode": "messaging_notes",
                "promoted_value": items, "list_size": len(bucket)}

    # Unknown field — extraction shouldn't have produced one, but defense
    # in depth: refuse rather than silently writing into nothing.
    raise ValueError(
        f"Unknown promotion target field {field!r}. "
        f"Allowed canonical: {sorted(CANONICAL_FIELDS)}; "
        f"messaging notes: {sorted(MESSAGING_NOTE_FIELDS)}.")


def restore_history_entry(profile: ProductProfile, history_index: int) -> dict:
    """Re-activate a superseded entry. The current active entry for that
    field is itself superseded so there's still exactly one active value
    at any time. UI calls this from the "history" toggle's restore button."""
    history = list(profile.field_history or [])
    if not 0 <= history_index < len(history):
        raise ValueError("history_index out of range")
    target = history[history_index]
    field = target.get("field")
    if not field:
        raise ValueError("history entry has no field")
    # Flip whatever's currently active on that field to superseded.
    for entry in history:
        if entry.get("field") == field and entry.get("status") == "active":
            entry["status"] = "superseded"
            entry["superseded_at"] = _now_iso()
    # Flip the target to active + reapply its value.
    target["status"] = "active"
    target["restored_at"] = _now_iso()
    value = target.get("value")
    if field in _SINGLE_VALUE_CANONICAL:
        setattr(profile, field, value)
    elif field in _LIST_VALUE_CANONICAL:
        # Restoring a list means: ensure those items are present (dedup-
        # append). We do NOT remove items added later — that's destructive
        # without user consent.
        existing = getattr(profile, field, None) or []
        merged, _ = _merge_list_append(existing, value or [])
        setattr(profile, field, merged)
    profile.field_history = history
    return {"field": field, "restored_value": value}
