# 0004 — Shared repository deployment

**Date:** 2026-06-25
**Status:** Accepted

## Context

The original `icecoolball/ticket-concert` repository is no longer available, while Render still needs a GitHub source for automatic deployments. The active `icecoolball/line-chat-bot-trip-manager` repository already owns the related family LINE bot but its root is also used by the Cloudflare Worker and legacy Python service.

## Decision

Store this Node dashboard under `ticket-concert/` in the shared repository. Configure only the Render `ticket-concert` service to use `ticket-concert` as its Root Directory, leaving the bot's root files and deployment commands unchanged.

## Consequences

Both systems share one repository without sharing runtime dependencies. Ticket changes must be committed from the shared repository, and Render must keep the Root Directory setting or it will run the bot root instead.

## Alternatives

- Create a replacement standalone repository: rejected because the user wants the related systems kept together.
- Merge the Node dashboard into the bot root: rejected because conflicting package and deployment files could break the Worker and Python service.

## Payoff trigger

Split the dashboard back into its own repository if release permissions or deployment cadence must differ from the bot.
