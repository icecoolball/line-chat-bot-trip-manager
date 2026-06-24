# 0001 — Family invite-link access

**Date:** 2026-06-24
**Status:** Accepted

## Context

The dashboard is shared by a few family members. Separate accounts and passwords add unnecessary friction, but leaving schedule APIs public would allow anyone with the Render URL to create or delete reminders.

## Decision

Use a high-entropy `FAMILY_ACCESS_TOKEN` in the URL fragment for first access. The browser exchanges it for a signed, HttpOnly, SameSite cookie valid for 30 days and removes the fragment from browser history. Rotating the token invalidates every existing cookie.

## Consequences

Family members open one invite link per device and do not type a password. Anyone who obtains that link can gain access until the token is rotated, so it must only be shared privately.

## Alternatives

- Public APIs with rate limits: rejected because rate limits do not prevent unwanted schedule changes.
- Supabase Auth accounts: rejected because the single-family audience does not need account lifecycle management.
- Cloudflare Access email verification: rejected because it adds an external login step.

## Payoff trigger

Move to individual accounts when access must be revoked per person or the audience expands beyond one trusted family group.
