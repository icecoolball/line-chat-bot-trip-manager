# Family Ticket Reminder

Public family dashboard for checking ticket sale times, running a countdown, and scheduling LINE reminders with Render and Supabase.

## Access model

- Open `https://YOUR_DOMAIN/` directly. There is no invite or login step.
- Every request uses the shared active member configured by `PUBLIC_MEMBER_ID`.
- The server issues short-lived scoped RPC credentials before accessing Supabase.
- Anyone with the public URL can create or delete ticket reminders.

## Local setup

1. Copy `.env.example` to `.env`.
2. Set `PUBLIC_MEMBER_ID`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `TICKET_BACKEND_TOKEN`.
3. Run `npm ci`.
4. Run `npm start` and open `http://localhost:5177/`.

## Render

- Root Directory: `ticket-concert`
- Build Command: `cd ticket-concert && npm ci`
- Start Command: `cd ticket-concert && npm start`

## Supabase

Apply the migrations in `supabase/migrations/` in filename order. Keep `ticket_line_token`, `ticket_line_target`, and `ticket_backend_token` in Supabase Vault; the Render `TICKET_BACKEND_TOKEN` must match the Vault signing secret.

## Verification

```powershell
npm test
node --check server.js
node --check public/app.js
```
