# Profile-from-knowledge fallback — build brief

Hand this to Claude Code. Read CLAUDE.md and docs/setup-stage-brief.md first.

## Goal
The website crawl 403s on bot-protected sites (Cloudflare, etc.) — common for real
companies. Fix it the way a human researcher does: when you can't read their HTML, draft
the profile from **what's already known about the company** (name + domain), via the LLM,
instead of dead-ending at "http_error". This makes "Crawl my website" produce something
useful on EVERY site, Cloudflare or not. It is a DRAFT the user confirms — same honesty
rule as everything else. No new external dependencies (uses the existing ANTHROPIC_API_KEY).

This is a small build on top of the shipped Setup stage. Do NOT add web search yet — that's
a deliberate later upgrade. This brief is LLM-from-knowledge only.

## The behavior
In `app/setup/draft.py` (or a sibling function), add a knowledge-based drafting path:
- Input: a domain (and any company name we can derive from it).
- If ANTHROPIC_API_KEY is set: ask Claude to produce the SAME structured profile JSON it
  already produces from crawl text — product_summary, value_prop, icp (industries, size),
  competitors, keywords, conversion_goal — but from the model's own knowledge of the company
  at that domain, NOT from fetched HTML. Reuse the existing prompt/JSON-parse/coercion logic;
  just swap the input ("here is the company's domain/name" instead of "here is the crawl
  text"). Defensive JSON parse, fall back to a minimal draft on parse failure.
- If no key: return the existing minimal skeleton (domain filled, fields blank) — unchanged.
- The model MUST be allowed to say it doesn't recognize the company. Instruct it: if you
  don't actually know this company, return empty/blank fields rather than inventing details.
  A blank draft the user fills is correct; a confabulated draft presented as fact is the
  failure mode we're avoiding.

## Wire it into the crawl flow (graceful fallback, not a separate button)
In `app/api/profile.py`, the `/api/profile/crawl` endpoint:
- Try the crawl as today.
- If the crawl SUCCEEDS (status "ok") → draft from crawl text, as today. Tag source fields
  "crawl"/"llm" as they already are.
- If the crawl FAILS (http_error, timeout, not_html, etc.) → fall back to the
  knowledge-based draft from the domain. Tag those fields **source="knowledge"** so the UI
  and user can see these came from "what we know about the company," not their site.
- Either way, return a DRAFT (confirmed=false). Include in the response which path produced
  it (e.g. crawl_status="ok" vs crawl_status="http_error", and a draft_source of
  "crawl" | "knowledge" | "skeleton") so the UI can label it honestly.

## UI (app/static/ui.html)
- When the draft came from knowledge fallback, the draft banner should say so clearly, e.g.:
  "Couldn't read example.com directly (site blocked the crawler) — drafted this from what we
  know about the company. Review carefully and Save to confirm." Distinct from the
  crawl-succeeded message.
- If the knowledge draft came back mostly blank (model didn't recognize the company), the
  banner should nudge toward manual: "We don't have enough on this company — fill the form
  manually." Don't show empty 'suggested' fields as if they were real suggestions.
- Keep the existing "suggested — confirm" marking. A knowledge-sourced field is still a
  suggestion, just from a different source; mark it accordingly (source="knowledge").

## Honesty discipline (do not drift — same rule as csv vs csv+intent)
- A knowledge draft is explicitly labeled as such; never presented as if read from their site.
- The model is instructed to leave fields blank when it doesn't know, not to invent.
- Nothing is saved without the user's explicit Save (PUT /api/profile), unchanged.

## Constraints (from CLAUDE.md)
- `scoped(Model, org_id)` everywhere; no new tenant-table reads outside it.
- Reuse the synthesis.py / existing draft.py LLM-or-template fallback pattern — no new pattern.
- Don't touch the TS app or .replit's [deployment] block.
- No new external dependency. No web search in this brief.
- Keep `python -m scripts.smoke` green. ADD tests:
  (1) Knowledge draft path with a monkeypatched LLM call (don't hit the real API in tests)
      returns a structured draft tagged source="knowledge", confirmed=false.
  (2) /api/profile/crawl with a monkeypatched _fetch that FAILS (e.g. returns http_403) falls
      back to the knowledge path and returns a draft (confirmed=false) with
      draft_source="knowledge" — and does NOT write to org_profiles.
  (3) The existing crawl-succeeds test still passes (crawl path unchanged when fetch works).

## Done looks like
- Crawling a Cloudflare-blocked domain (e.g. onit.com) now returns a useful, LLM-drafted
  profile labeled "drafted from what we know," instead of a dead "http_error" skeleton.
- Crawling a fetchable site (e.g. hobbyalpha.com, example.com) still uses the real HTML.
- An unknown company yields a mostly-blank draft that nudges to manual, not a confabulation.
- Drafts still require explicit Save; source labels are honest; smoke green; committed + pushed.

## Out of scope (don't build)
- Web search grounding — deliberate next upgrade, not now.
- Headless-browser / Cloudflare-bypass rendering — out of scope, ethically grey.
- Wiring OrgProfile into the agents — separate next build.
