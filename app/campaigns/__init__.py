"""
Campaign Builder — the orchestration layer (Phase 3 / step 4).

THE LOAD-BEARING PRINCIPLE (do not violate): campaigns REFERENCE assets;
they do NOT own content-generation logic. Campaign Builder CALLS the
existing content engine for each approved plan item — it never
reimplements, forks, or embeds generation logic. Generated assets are
normal content artifacts that carry a back-reference (`artifacts.
campaign_id`) but remain first-class library citizens. Archiving a
campaign leaves the assets intact.

Modules:
  * planner.py — channel recommendations + plan proposal (LLM + fallback).

The API + worker plumbing live in app/api/campaigns.py and app/worker.py.
"""
