---
id: 20260624221506
title: service-role-fx-cache
principal: unknown
interest: unknown
hotspot: src/worker.ts
business_capability: currency-conversion
payoff_trigger: when-another-runtime-needs-cache-access
quadrant: prudent-deliberate
category: infrastructure
ai_authored: true
created: 2026-06-24
---

The Worker uses the Supabase service-role key for the internal FX cache and other bot tables. This closes public access through RLS but keeps a broad server-side credential boundary that should be narrowed if another runtime needs direct cache access.
