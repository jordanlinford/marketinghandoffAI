"""
UTM tagging — the join key a future analytics dashboard will use to attribute
performance back to the content that produced it.

The four dimensions (campaign / source / medium / content) match the way
GA / LinkedIn / Search Ads already parse traffic, so a later dashboard can
JOIN performance rows to artifacts on these exact keys without translation.

v1 SCOPE: generate + surface the tagged link. We do NOT publish it. The join
only works if the human carries the tagged link to the channel they publish
on. The UI is explicit about that.
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Default channel / medium per content_type. These are SUGGESTIONS — the user
# can override at trigger time or via the artifact-tags PATCH. Picking sane
# defaults here saves a click per piece while leaving the choice editable.
_CHANNEL_DEFAULTS: dict[str, tuple[str, str]] = {
    "email":        ("email",    "email"),
    "ad":           ("linkedin", "paid-social"),
    "social_post":  ("linkedin", "organic-social"),
    "blog_outline": ("organic",  "blog"),
}


def _slug(text: str, max_len: int = 60) -> str:
    """Lowercase ascii kebab-case slug, suitable for a UTM value. Strips
    punctuation, collapses whitespace, trims to max_len. Empty input → ''."""
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len].rstrip("-")


def suggest_utms(content_type: str, topic: str, *, version: int = 1,
                 overrides: dict | None = None) -> dict:
    """Compute a sensible UTM set for a freshly-generated draft. Auto-slugs
    `topic` into `utm_campaign` and emits a versioned `utm_content` so v2 of
    the same draft is distinguishable. `overrides` (from task.utm or a future
    PATCH) win field-by-field, so the user always has the final say."""
    source, medium = _CHANNEL_DEFAULTS.get(
        content_type, ("organic", "organic-social"))
    campaign_slug = _slug(topic) or "untitled"
    content_slug = _slug(f"{content_type}-{topic}", max_len=100) or content_type
    if version > 1:
        content_slug = f"{content_slug[:140]}-v{version}"
    base = {
        "utm_campaign": campaign_slug,
        "utm_source": source,
        "utm_medium": medium,
        "utm_content": content_slug,
    }
    for k, v in (overrides or {}).items():
        if k in base and v:
            # The user's override is treated as a raw value — they typed it,
            # they own it. Only slug when we generated it.
            base[k] = str(v).strip()
    return base


def build_tagged_url(destination_url: str | None, utms: dict) -> str:
    """Append the four UTM params to `destination_url`, preserving any
    existing query string and overwriting any conflicting UTM keys (the
    artifact's tags win — that's the whole point of storing them).

    Returns "" when destination_url is empty/None, so the UI can show a
    "set a destination URL" prompt rather than a half-formed link."""
    if not destination_url:
        return ""
    keep: dict[str, str] = {}
    parsed = urlsplit(destination_url.strip())
    # Preserve non-UTM params; UTMs from the artifact tags override.
    for k, v in parse_qsl(parsed.query, keep_blank_values=False):
        if not k.lower().startswith("utm_"):
            keep[k] = v
    for key in ("utm_campaign", "utm_source", "utm_medium", "utm_content"):
        val = (utms.get(key) or "").strip()
        if val:
            keep[key] = val
    new_query = urlencode(keep)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                       new_query, parsed.fragment))


def utm_dict_from_artifact(art_like) -> dict:
    """Helper for endpoints/agent code: pull the four UTM fields off an
    Artifact ORM row OR a serialized dict, returning a normalized dict."""
    def _get(key):
        if isinstance(art_like, dict):
            return art_like.get(key)
        return getattr(art_like, key, None)
    return {
        "utm_campaign": _get("utm_campaign") or "",
        "utm_source": _get("utm_source") or "",
        "utm_medium": _get("utm_medium") or "",
        "utm_content": _get("utm_content") or "",
    }


def utm_field_keys() -> Iterable[str]:
    """The exact set of UTM column names; one source of truth for callers
    that iterate (PATCH endpoint, worker persistence)."""
    return ("utm_campaign", "utm_source", "utm_medium", "utm_content")
