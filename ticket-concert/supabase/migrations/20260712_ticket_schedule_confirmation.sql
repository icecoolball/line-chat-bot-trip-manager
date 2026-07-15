create or replace function public.ticket_get_schedule_summary(p_schedule_id text)
returns table (
  schedule_id text,
  event_name text,
  event_site text,
  event_url text,
  sale_at timestamptz
)
language sql
security definer
set search_path = public
as $$
  select s.id, s.name, s.site, s.url, s.sale_at
  from public.schedules s
  where s.id = p_schedule_id;
$$;

create or replace function public.create_ticket_schedule(
  p_backend_credential text,
  p_id text,
  p_name text,
  p_site text,
  p_url text,
  p_sale_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = public, pgmq
as $$
declare
  result jsonb;
  caller_id uuid;
  reminder_row public.schedule_reminders;
  sleep_seconds integer;
begin
  caller_id := public.assert_ticket_backend_credential(p_backend_credential, 'ticket:schedules:create');

  if p_sale_at <= now() or p_sale_at > now() + interval '2 years' then
    raise exception 'sale_at must be in the next two years';
  end if;

  insert into public.schedules (id, name, site, url, sale_at, status, created_by)
  values (p_id, trim(p_name), trim(p_site), trim(p_url), p_sale_at, 'active', caller_id);

  for reminder_row in
    insert into public.schedule_reminders (schedule_id, offset_minutes, due_at, next_attempt_at, status, last_error)
    select
      p_id,
      minutes,
      p_sale_at - make_interval(mins => minutes),
      greatest(now(), p_sale_at - make_interval(mins => minutes)),
      case when p_sale_at - make_interval(mins => minutes) > now() then 'pending' else 'skipped' end,
      case when p_sale_at - make_interval(mins => minutes) > now() then null else 'Reminder window already passed at schedule creation' end
    from unnest(array[1440, 60, 30, 15, 5]) as minutes
    returning *
  loop
    if reminder_row.status = 'pending' then
      sleep_seconds := greatest(0, ceil(extract(epoch from reminder_row.due_at - now()))::integer);
      perform pgmq.send(
        'ticket_reminders',
        jsonb_build_object('reminder_id', reminder_row.id),
        sleep_seconds
      );
    end if;
  end loop;

  perform pgmq.send(
    'ticket_reminders',
    jsonb_build_object('kind', 'schedule_created', 'schedule_id', p_id),
    0
  );

  select to_jsonb(s) || jsonb_build_object(
    'schedule_reminders', coalesce((
      select jsonb_agg(to_jsonb(r) order by r.offset_minutes desc)
      from public.schedule_reminders r
      where r.schedule_id = s.id
    ), '[]'::jsonb)
  )
  into result
  from public.schedules s
  where s.id = p_id;

  return result;
end;
$$;

revoke all on function public.ticket_get_schedule_summary(text) from public, anon, authenticated;
grant execute on function public.ticket_get_schedule_summary(text) to service_role;
