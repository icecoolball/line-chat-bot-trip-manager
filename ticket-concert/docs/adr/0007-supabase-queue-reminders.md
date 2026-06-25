# 0007 - Supabase queue reminders

**Date:** 2026-06-25
**Status:** Accepted

## Context

The original reminder runtime scanned `schedule_reminders` rows on a timer and claimed work directly from the table. That works for a small family workload, but queue-backed delivery gives stronger durability and cleaner retry semantics without adding a paid service.

## Decision

Move reminder dispatch to `pgmq` in the existing Supabase project. Schedule creation now enqueues future reminders into a durable `ticket_reminders` queue, and the Edge Function reads queue messages, sends LINE notifications, updates `schedule_reminders`, and deletes queue messages only after terminal success or failure.

## Consequences

Reminder timing and retries now flow through a durable queue without adding another vendor. The Cron trigger still exists, but it now wakes a queue consumer instead of scanning the reminder table directly.

## Alternatives

- Keep direct table claiming: rejected because it leaves delivery state coupled to one custom claim loop.
- Add a new external queue service: rejected because the user wants the free path first.

## Payoff trigger

Revisit if reminder volume grows beyond what one Supabase queue consumer can process comfortably.
