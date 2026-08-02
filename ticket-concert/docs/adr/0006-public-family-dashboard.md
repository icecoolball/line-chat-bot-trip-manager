# 0006 — Public family dashboard

**Date:** 2026-08-02
**Status:** Accepted

## Context

The family dashboard's deployed member migration was missing, causing invite exchange to fail with an internal server error. The owner prefers direct access and accepts that anyone who discovers the Render URL can change ticket reminders.

## Decision

Remove invite and session checks from the web application. Route all schedule operations through one active Supabase member, configured as `PUBLIC_MEMBER_ID`, while retaining short-lived scoped backend credentials for database RPC calls.

## Consequences

The dashboard opens directly on every device. Anyone with the URL can inspect ticket sources and create or delete reminders, while unrelated Supabase data remains outside the ticket RPC boundary.

## Alternatives

- Repair per-person invite access: rejected because the owner does not want an access step.
- Keep a shared invite link: rejected because opening it on new browsers remains inconvenient.
- Use an external access gateway: rejected because it introduces another login.

## Payoff trigger

Restore access control when the URL is shared beyond the trusted family, unexplained schedule changes occur, or abuse reaches the service.
