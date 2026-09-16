-- Keep the minute-level timing guarantee while avoiding empty Edge Function
-- invocations. The PGMQ consumer is called only when a message is visible.

create or replace function public.ticket_dispatch_due_reminders()
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  project_url text;
  publishable_key text;
  request_id bigint;
begin
  if not exists (
    select 1
    from pgmq.q_ticket_reminders
    where vt <= now()
  ) then
    return null;
  end if;

  select decrypted_secret
  into project_url
  from vault.decrypted_secrets
  where name = 'ticket_project_url'
  order by created_at desc
  limit 1;

  select decrypted_secret
  into publishable_key
  from vault.decrypted_secrets
  where name = 'ticket_publishable_key'
  order by created_at desc
  limit 1;

  if project_url is null or publishable_key is null then
    raise exception 'ticket reminder dispatch secrets are missing from Vault';
  end if;

  select net.http_post(
    url := rtrim(project_url, '/') || '/functions/v1/ticket-reminders',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'apikey', publishable_key,
      'Authorization', 'Bearer ' || publishable_key
    ),
    body := jsonb_build_object('source', 'ticket-reminder-cron'),
    timeout_milliseconds := 10000
  )
  into request_id;

  return request_id;
end;
$$;

revoke all on function public.ticket_dispatch_due_reminders() from public, anon, authenticated;

do $$
declare
  reminder_job_id bigint;
  reminder_job_count integer;
begin
  select count(*), min(jobid)
  into reminder_job_count, reminder_job_id
  from cron.job
  where jobname = 'ticket-reminders-every-minute'
     or command like '%/functions/v1/ticket-reminders%'
     or command = 'select public.ticket_dispatch_due_reminders();';

  if reminder_job_count = 0 then
    perform cron.schedule(
      'ticket-reminders-every-minute',
      '* * * * *',
      'select public.ticket_dispatch_due_reminders();'
    );
  elsif reminder_job_count = 1 then
    perform cron.alter_job(
      reminder_job_id,
      schedule := '* * * * *',
      command := 'select public.ticket_dispatch_due_reminders();',
      active := true
    );
  else
    raise exception 'Expected at most one ticket reminder Cron job, found %', reminder_job_count;
  end if;
end;
$$;
