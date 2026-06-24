---
id: 20260625001800
title: shared-repository-deploy
principal: unknown
interest: unknown
hotspot: docs/adr/0004-shared-repository-deployment.md
business_capability: ticket-deployment
payoff_trigger: when-release-permissions-or-cadence-diverge
quadrant: prudent-deliberate
category: release
ai_authored: true
created: 2026-06-25
---

The ticket dashboard now lives as a subdirectory of the LINE bot repository because the original standalone GitHub repository is unavailable. This avoids altering the bot root, but deployment depends on Render keeping its Root Directory set to ticket-concert.
