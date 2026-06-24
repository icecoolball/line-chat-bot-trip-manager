# 0001 — FX rate cache access

**Date:** 2026-06-24
**Status:** Accepted

## Context

`fx_rates` is an internal cache written by the Cloudflare Worker. RLS was disabled, allowing anyone with the public anon key to read or change every fallback cache row.

## Decision

Enable RLS, revoke table privileges from `anon` and `authenticated`, and keep access server-side through the existing `SUPABASE_SERVICE_ROLE_KEY`. Keep static fallback values in one versioned JSON file consumed by both TypeScript and Python.

## Consequences

Public clients cannot tamper with cached conversion rates. The Worker remains trusted with service-role access, so secret handling and Worker authorization remain part of the security boundary.

## Alternatives

- Add public read/write RLS policies: rejected because browsers do not own the cache.
- Add read-only anon access: rejected because no public feature currently queries this table directly.
- Remove the database cache: rejected because the cache limits external FX API calls and preserves recent rates during outages.

## Payoff trigger

Introduce a narrower database role or authenticated policy when another runtime legitimately needs direct cache access.
