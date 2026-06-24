# 0002 — Supabase reminder runtime

**Date:** 2026-06-24
**Status:** Accepted

## Context

The Render service may sleep on the free plan, so an in-process Node scheduler cannot reliably deliver reminders. Reminder state also needs idempotent claiming and retry behavior.

## Decision

Store one row per reminder in `schedule_reminders`. Supabase Cron invokes a JWT-protected Edge Function every minute; the function atomically claims due rows, reads the fixed LINE destination from Supabase Vault through a service-role-only RPC, pushes the message, and records success or retry state.

## Consequences

Reminder delivery no longer depends on Render uptime. Supabase becomes the runtime owner for timing, retry state, Cron history, and Edge Function logs.

## Alternatives

- Node `setInterval` on Render: rejected because sleeping instances miss reminder windows.
- GitHub Actions cron: rejected because scheduling precision and startup latency are unsuitable for five-minute reminders.
- A separate Cloudflare Worker: rejected to avoid another deployment surface for this small application.

## Payoff trigger

Replace this design with a queue service when reminder volume or delivery guarantees exceed a single-family workload.
