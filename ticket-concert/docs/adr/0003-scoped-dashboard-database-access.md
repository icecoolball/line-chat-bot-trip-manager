# 0003 — Scoped dashboard database access

**Date:** 2026-06-24
**Status:** Accepted

## Context

The Render dashboard must create, list, and delete ticket schedules, but giving it the Supabase service-role key would also grant access to unrelated tables in the shared Trip-Manager project.

## Decision

Render uses the public Supabase key plus a high-entropy `TICKET_BACKEND_TOKEN`. Three security-definer RPCs validate that token from Vault and expose only ticket schedule operations; direct table access remains revoked.

## Consequences

A leaked backend token can modify ticket schedules but cannot query unrelated tables through these RPCs. Rotating `ticket_backend_token` in Vault and Render revokes the old token.

## Alternatives

- Service-role key in Render: rejected because its authority is wider than this application needs.
- Supabase Auth accounts: rejected because family access intentionally has no user accounts.
- Direct anon table policies: rejected because they would make authorization depend on a client-visible role without the shared backend secret.

## Payoff trigger

Replace the shared backend token with per-user authorization if the dashboard audience expands beyond one trusted family group.
