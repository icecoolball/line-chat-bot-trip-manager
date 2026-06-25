---
id: 20260624235135
title: shared-backend-token
principal: unknown
interest: unknown
hotspot: lib/schedule-store.js
business_capability: ticket-scheduling
payoff_trigger: when-per-person-database-authorization-is-needed
quadrant: prudent-deliberate
category: infrastructure
ai_authored: true
created: 2026-06-24
---

Render still authenticates ticket schedule RPCs with one shared high-entropy backend token stored in Supabase Vault. Rotation steps now live in `docs/operations.md`, and tests verify that runtime startup fails when the token is missing while schedule RPCs always use the scoped backend token. The remaining debt is architectural: that single token still grants all ticket schedule operations until it is rotated.
