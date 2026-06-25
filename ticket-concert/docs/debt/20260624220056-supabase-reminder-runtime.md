---
id: 20260624220056
title: supabase-reminder-runtime
principal: unknown
interest: unknown
hotspot: supabase/functions/ticket-reminders
business_capability: line-reminders
payoff_trigger: when-delivery-volume-or-guarantees-grow
quadrant: prudent-deliberate
category: infrastructure
ai_authored: true
created: 2026-06-24
---

Reminder delivery is still coupled to Supabase Cron and one Edge Function. The production smoke checklist now lives in `docs/operations.md`, including the 7-minute setup and single 5-minute delivery confirmation. The remaining debt is architectural: this runtime is still sized for family-scale traffic and should be revisited if volume or delivery guarantees grow.
