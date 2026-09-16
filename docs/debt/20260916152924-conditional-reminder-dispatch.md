---
id: 20260916152924
title: conditional-reminder-dispatch
principal: unknown
interest: +1 indexed queue check/minute plus Cron history write
hotspot: ticket-concert/supabase/migrations/20260916_ticket_conditional_reminder_dispatch.sql
business_capability: ticket reminders
payoff_trigger: PGMQ changes its queue-table interface, Cron history becomes material Disk IO, or reminder latency must be under one minute
quadrant: prudent-deliberate
category: infrastructure
ai_authored: true
created: 2026-09-16
---

The reminder dispatcher depends on the internal PGMQ q_ticket_reminders table and its vt index, while a minute Cron still writes run history. This preserves reminder precision and eliminates idle Edge calls, but it leaves a lightweight database poll and an interface dependency. Revisit under ADR 0008's payoff trigger.
