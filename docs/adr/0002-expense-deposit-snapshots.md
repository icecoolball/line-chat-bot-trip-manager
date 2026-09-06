# 0002 — Per-person deposit snapshots on expenses

**Date:** 2026-09-06
**Status:** Proposed

## Context

Group members transfer deposits to a collector for an already recorded expense. Counting these transfers as expenses duplicates the bill, while ignoring them in net settlement asks people to pay twice. Users need a short command for several people paying the same amount, and must be able to repeat or correct it safely.

## Decision

Add a service-role-only `expense_deposits` table, keyed by expense ID and participant name, containing cumulative amount in minor currency units, collector, currency, and the last LINE event ID/time. A service-role-only SECURITY INVOKER function validates the signed conversation context against the trip, locks the expense and trip rows, and merges a batch in one transaction. The existing expense currency defines the deposit currency. Snapshots update only deposit metadata, never the bill or split participants. Incoming older events are ignored per person. Use event ID as the deterministic tie-breaker for equal event timestamps.

The status command allocates any rounding remainder across participants in their recorded order. For settlement, transfers credit the contributor and debit the collector equally; a collector's own earmarked contribution has no net transfer effect. Worker and Excel consume the same persisted snapshot semantics. Changing the amount or receiver corrects the recorded cumulative state, rather than appending another payment.

Reads use a service-role-only SQL function that returns one JSON aggregate per trip/expense. This produces a single-statement snapshot and avoids both PostgREST row-page truncation and rows shifting between separate page requests.

Participant replacement uses a separate service-role RPC with the same expense/trip locks. Two event-version columns on expenses prevent old LINE commands from restoring an outdated split. A replacement preserves the bill and original payer and cannot orphan a positive deposit contributor or collector. Keeping this validation in the database is preferred to a Worker read-then-patch check, which would race with deposits. Trip confirmation closes the trip before reading its final settlement; its UPDATE waits for in-flight deposit/participant transactions, and subsequent writers reject the closed trip. Failed summary reads retain the confirmation state for retry.

The migration also revokes public/anon/authenticated access to trips, expenses, bot_states, and export_jobs: these are the ownership and dispatch inputs for deposit reporting, and their existing unconditional policies permit forgery. Backend service-role access is retained. LINE history/end-trip/export routes are conversation-scoped, including cached history selections. The system HTTP export endpoint requires a configured CRON_SECRET bearer token and fails closed when missing. Leaving public parent access or trusting cached client context would expose the newly protected data indirectly, so those alternatives are rejected.

## Consequences

Repeating a command is safe, batch updates cannot partially apply, unrelated participants survive concurrent writes, and new deposit data does not inherit the existing parent tables' unconditional public RLS policies. Reports query the protected table separately. The additive migration must precede publishing the deposit/reporting code; old expenses need no backfill. Existing clients that directly use anon/authenticated access to these four tables must migrate to trusted backend access before rollout; the migration intentionally removes that old public access. System HTTP export callers must provide the existing CRON_SECRET bearer token. Only the latest cumulative state per person/expense is retained, so this is not a transaction-history or refund ledger. SQL serializes changes per expense/trip; this is appropriate for small family groups.

## Alternatives

- A normalized append-only payment table: supports multiple receivers and audit history, but requires payment identifiers, reversal commands, additional joins, and deduplication for every event. Prefer it if transaction-level tracking becomes required.
- A JSONB snapshot column on expenses: minimizes reads, but inherits the existing table's public policies and would directly expose new deposit records; rejected after inspecting production schema metadata.
- Additional expense rows for deposits: rejected because they inflate expense totals and distort who owes what.

## Payoff trigger

Replace snapshots with a payment ledger when users need transaction history, partial refunds, or multiple simultaneous collectors for one person's share.

## References

- [PostgreSQL row locking](https://www.postgresql.org/docs/17/explicit-locking.html)
- [PostgreSQL JSON operations](https://www.postgresql.org/docs/current/functions-json.html)
- [LINE duplicate and out-of-order webhook delivery](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
