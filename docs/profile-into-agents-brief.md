# Wire OrgProfile into the agents — build brief

Hand this to Claude Code. Read CLAUDE.md first.

## Goal
The Setup stage now produces a saved, confirmed OrgProfile (ICP, product, competitors,
keywords, conversion goal). But the agents still reason from SEED defaults in the agent
registration config, not the real profile. Wire the saved OrgProfile INTO the agent run so
the market brief reflects the org's actual confirmed profile. This is the build that connects
Setup to the rest of the system.

## The change
- When a run executes, the worker should load the org's confirmed OrgProfile (via scoped())
  and make it available to the agent through AgentContext — the same way market data flows
  through ctx.get_market_data(). Add the profile to AgentContext (e.g. ctx.org_profile or a
  ctx.get_org_profile() accessor), populated from the saved OrgProfile when one exists.
- The market_intel agent should prefer the confirmed OrgProfile's ICP/product/competitors/
  keywords over the seed config when a profile exists. If NO confirmed profile exists, fall
  back to today's seed-config behavior unchanged (no regression).
- Precedence is explicit: confirmed OrgProfile > seed agent config. Document it in a comment.

## Honesty / provenance
- The brief should make clear what it reasoned from — e.g. a line/citation noting "based on
  your confirmed org profile" vs "based on seed defaults (no profile set up yet)". Same
  honesty discipline as the rest of the system: the user can see which inputs drove the output.

## Constraints (from CLAUDE.md)
- scoped(Model, org_id) is the ONLY way to read the OrgProfile. No bare selects.
- AgentContext / AgentResult contract unchanged in shape — extend it cleanly, don't break
  the BYO-agent seam.
- The market_intel agent reads the profile through ctx; it does not query the DB directly.
- No schema change (org_profiles already exists).
- Don't touch the TS app or .replit's [deployment] block.
- Keep python -m scripts.smoke green. ADD tests:
  (1) A run for an org WITH a confirmed profile produces a brief whose ICP/targets reflect the
      profile's ICP (not the seed config).
  (2) A run for an org with NO profile still works using seed defaults (no regression).
  (3) Tenant isolation: an agent run only ever loads its own org's profile via scoped().

## Done looks like
- With a confirmed Onit profile saved, running market_intel produces a brief grounded in the
  real Onit ICP/competitors/product instead of seed defaults.
- No profile -> unchanged seed behavior.
- The brief shows which inputs it used; smoke green with the three new tests; committed + pushed.

## Out of scope
- The content engine; live API data sources; CampaignBrief. Just wire the profile into the
  existing market_intel agent.
