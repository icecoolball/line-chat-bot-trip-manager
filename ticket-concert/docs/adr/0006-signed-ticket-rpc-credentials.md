# 0006 - Signed ticket RPC credentials

**Date:** 2026-06-25
**Status:** Accepted

## Context

The dashboard previously sent one long-lived backend token to every ticket RPC call. That was narrower than a service-role key, but it still behaved like a bearer token with full schedule authority until rotated.

## Decision

Use the existing `TICKET_BACKEND_TOKEN` as an HMAC signing secret instead of a bearer token. The Node server now mints short-lived signed credentials per request and per scope, and Supabase RPC functions verify signature, expiry, scope, and active member status before allowing access.

## Consequences

The server no longer sends a reusable long-lived credential over RPC calls, and revoking a member cuts off fresh RPC credentials immediately. The Supabase signing secret must stay synchronized with the Render env value.

## Alternatives

- Keep the static backend token: rejected because any leak grants all schedule actions until manual rotation.
- Use the Supabase service-role key in Render: rejected because it is wider than the dashboard needs.

## Payoff trigger

Revisit if the project grows into separate write/read roles or needs external service-to-service delegation.
