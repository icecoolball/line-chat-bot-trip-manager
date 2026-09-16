# 0008 — Conditional reminder dispatch

**Date:** 2026-09-16
**Status:** Accepted

## Context

The `ticket-reminders-every-minute` Cron job invokes the reminder Edge Function every minute, even when the durable `ticket_reminders` queue has no visible messages. Each empty invocation still passes through the API gateway, starts an Edge runtime, reads LINE configuration from Vault, and reads the queue. This steady idle traffic consumes a disproportionate share of the Nano project's daily requests and contributes avoidable database and network work.

## Decision

Keep the one-minute Cron schedule for reminder precision, but change its command to call a database dispatcher. The dispatcher performs an indexed existence check for queue messages whose visibility time has arrived and invokes the Edge Function only when work is available. The Edge Function keeps loading LINE configuration before claiming queue messages so that a transient Vault failure cannot consume a delivery attempt.

## Consequences

Reminder timing remains within the existing one-minute Cron interval. Empty minutes no longer start the Edge Function or call the Data API and Vault, while actual reminder delivery, visibility timeouts, retries, and batching remain unchanged. The minute-level database check and Cron run-history write still occur, and the dispatcher intentionally depends on PGMQ's `q_ticket_reminders.vt` queue table and index.

## Alternatives

- Keep invoking the Edge Function every minute: rejected because it preserves the idle API, runtime, Vault, and queue traffic that triggered the optimization.
- Arm and disarm a minute worker from a separate five-minute Cron job: rejected because it adds state transitions and failure modes while reducing only the small indexed checks.
- Create one Cron job per reminder: rejected because `pg_cron` has no native one-time timestamp job and cancellation or time changes would require complex job lifecycle management.

## Payoff trigger

Revisit when PGMQ changes its queue-table interface, Cron history becomes a material source of Disk IO, or reminder latency requirements become tighter than one minute.
