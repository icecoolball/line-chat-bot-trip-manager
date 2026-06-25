# 0005 - Member invite identities

**Date:** 2026-06-25
**Status:** Accepted

## Context

The original family access model used one shared invite token, which meant any revocation action affected every device. The user wants to keep the no-password family workflow while gaining per-person identity and revocation.

## Decision

Store one record per family member in Supabase and issue one invite token per member. The Node server still signs the HttpOnly session cookie locally, but the session now carries a member ID and every authenticated request re-checks that the member is still active.

## Consequences

Each family member can now be revoked independently without cutting off everyone else. The bootstrap path still accepts the legacy shared invite only long enough to create the first member after migration.

## Alternatives

- Supabase Auth accounts: rejected because it adds account lifecycle and password/reset flows the family does not want.
- Keep one shared invite token: rejected because per-person revocation is impossible.

## Payoff trigger

Revisit if the family needs role-based permissions or a self-service invite management UI.
