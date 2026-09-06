---
id: 20260906195020
title: expense-deposit-snapshots
principal: unknown
interest: unknown
hotspot: docs/adr/0002-expense-deposit-snapshots.md
business_capability: expense-deposits
payoff_trigger: when-transfer-history-refunds-or-multiple-collectors-are-needed
quadrant: prudent-deliberate
category: planning
ai_authored: true
created: 2026-09-06
---

Expense deposits retain one corrected cumulative amount and receiver per participant, rather than individual transfer history. This keeps batch chat commands idempotent and avoids duplicate expense totals, but cannot represent partial refunds or multiple simultaneous receivers for one contribution. ADR 0002 records the alternatives; replace snapshots with an append-only payment ledger when transaction history, refunds, or multiple collectors are required.
