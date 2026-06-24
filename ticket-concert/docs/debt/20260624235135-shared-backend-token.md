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

Render authenticates ticket schedule RPCs with one shared high-entropy backend token stored in Supabase Vault. This limits exposure compared with a service-role key, but the token still grants all ticket schedule operations until it is rotated.
