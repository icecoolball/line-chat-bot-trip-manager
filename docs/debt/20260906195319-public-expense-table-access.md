---
id: 20260906195319
title: public-expense-table-access
principal: unknown
interest: unknown
hotspot: supabase/worker-free-schema.sql
business_capability: expense-data-isolation
payoff_trigger: before-enabling-new-deposit-data
quadrant: prudent-inadvertent
category: infrastructure
ai_authored: false
created: 2026-09-06
---

Read-only production metadata inspection found that trips and expenses grant anon/authenticated full CRUD and have unconditional RLS policies. This pre-existing access boundary lets public-key callers alter the source records used by the bot; the deposit feature will store its new data in a separate service-role-only table to avoid directly exposing it. Tightening legacy table access needs a coordinated review of existing clients before release; do not assume that enabling RLS alone restricts those policies.

Local candidate closure: db/2026-09-06-expense-deposits.sql now revokes public/anon/authenticated access to trips, expenses, bot_states and export_jobs while retaining service-role CRUD. Production remains unchanged until this reviewed migration is authorized and applied; verify backend client keys before rollout.
