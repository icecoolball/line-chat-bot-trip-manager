---
id: 20260624220051
title: shared-family-invite
principal: unknown
interest: unknown
hotspot: lib/auth.js
business_capability: family-access
payoff_trigger: when-per-person-revocation-is-needed
quadrant: prudent-deliberate
category: planning
ai_authored: true
created: 2026-06-24
---

Family access still uses one shared invite token instead of individual identities. Rotation steps now live in `docs/operations.md`, and automated tests verify that changing the secret invalidates existing sessions. The remaining debt is purely architectural: one person's access still cannot be revoked without rotating the token for everyone.
