-- Give the Render dashboard only ticket-specific RPC access. Direct table
-- access remains blocked for anon/authenticated roles.

create or replace function public.assert_ticket_backend_token(p_token text)
returns void
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  expected_token text;
begin
  select decrypted_secret
  into expected_token
  from vault.decrypted_secrets
  where name = 'ticket_backend_token'
  order by created_at desc
  limit 1;

  if expected_token is null or p_token is null or p_token <> expected_token then
    raise insufficient_privilege using message = 'unauthorized';
  end if;
end;
$$;

revoke all on function public.assert_ticket_backend_token(text) from public, anon, authenticated;

create or replace function public.create_ticket_schedule(
  p_backend_token text,
  p_id text,
  p_name text,
  p_site text,
  p_url text,
  p_sale_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  result jsonb;
begin
  perform public.assert_ticket_backend_token(p_backend_token);

  if p_sale_at <= now() or p_sale_at > now() + interval '2 years' then
    raise exception 'sale_at must be in the next two years';
  end if;

  insert into public.schedules (id, name, site, url, sale_at, status)
  values (p_id, trim(p_name), trim(p_site), trim(p_url), p_sale_at, 'active');

  insert into public.schedule_reminders (schedule_id, offset_minutes, due_at, next_attempt_at)
  select p_id, minutes, p_sale_at - make_interval(mins => minutes), p_sale_at - make_interval(mins => minutes)
  from unnest(array[1440, 60, 30, 15, 5]) as minutes;

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

create or replace function public.ticket_list_schedules(p_backend_token text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  result jsonb;
begin
  perform public.assert_ticket_backend_token(p_backend_token);

  select coalesce(jsonb_agg(
    to_jsonb(s) || jsonb_build_object(
      'schedule_reminders', coalesce((
        select jsonb_agg(to_jsonb(r) order by r.offset_minutes desc)
        from public.schedule_reminders r
        where r.schedule_id = s.id
      ), '[]'::jsonb)
    ) order by s.sale_at
  ), '[]'::jsonb)
  into result
  from public.schedules s;

  return result;
end;
$$;

create or replace function public.delete_ticket_schedule(p_backend_token text, p_id text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  deleted_count integer;
begin
  perform public.assert_ticket_backend_token(p_backend_token);
  delete from public.schedules where id = p_id;
  get diagnostics deleted_count = row_count;
  return deleted_count > 0;
end;
$$;

drop function if exists public.create_ticket_schedule(text, text, text, text, timestamptz);

revoke all on function public.create_ticket_schedule(text, text, text, text, text, timestamptz) from public, anon, authenticated;
revoke all on function public.ticket_list_schedules(text) from public, anon, authenticated;
revoke all on function public.delete_ticket_schedule(text, text) from public, anon, authenticated;
grant execute on function public.create_ticket_schedule(text, text, text, text, text, timestamptz) to anon;
grant execute on function public.ticket_list_schedules(text) to anon;
grant execute on function public.delete_ticket_schedule(text, text) to anon;
