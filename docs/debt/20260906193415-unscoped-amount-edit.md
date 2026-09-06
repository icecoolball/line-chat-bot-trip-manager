---
id: 20260906193415
title: unscoped-amount-edit
principal: unknown
interest: unknown
hotspot: src/worker.ts
business_capability: expense-editing
payoff_trigger: before-expanding-bot-access
quadrant: prudent-inadvertent
category: code_quality
ai_authored: false
created: 2026-09-06
---

The existing amount-edit command selects and updates an expense by global ID without checking its trip or caller context. This predates the expense-label and quiet-chat change; the new name-edit path must be scoped to the current conversation. Restrict the legacy amount path in a separate authorization change with boundary tests.
