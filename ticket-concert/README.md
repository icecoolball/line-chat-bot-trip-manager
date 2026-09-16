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

Apply the migrations in `supabase/migrations/` in filename order. Keep `ticket_line_token`, `ticket_line_target`, `ticket_backend_token`, `ticket_project_url`, and `ticket_publishable_key` in Supabase Vault; the Render `TICKET_BACKEND_TOKEN` must match the Vault signing secret.

Migration `20260916_ticket_conditional_reminder_dispatch.sql` creates or updates Cron job `ticket-reminders-every-minute`. It runs every minute and calls `public.ticket_dispatch_due_reminders()`. The dispatcher starts the `ticket-reminders` Edge Function only when `pgmq.q_ticket_reminders` has a visible message.

To roll back conditional dispatch, restore the previous Cron command and remove the dispatcher:

```sql
select cron.alter_job(
  (select jobid from cron.job where jobname = 'ticket-reminders-every-minute'),
  command := $command$
    select net.http_post(
      url := (select decrypted_secret from vault.decrypted_secrets where name = 'ticket_project_url') || '/functions/v1/ticket-reminders',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'apikey', (select decrypted_secret from vault.decrypted_secrets where name = 'ticket_publishable_key'),
        'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'ticket_publishable_key')
      ),
      body := jsonb_build_object('time', now()),
      timeout_milliseconds := 10000
    )
  $command$,
  active := true
);

drop function if exists public.ticket_dispatch_due_reminders();
```

## Verification

```powershell
npm test
node --check server.js
node --check public/app.js
```
