create extension if not exists pgcrypto with schema extensions;
create extension if not exists pgmq;

alter table public.schedules
  add column if not exists created_by uuid;

create table if not exists public.ticket_access_members (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  invite_token_hash text not null unique,
  status text not null default 'active' check (status in ('active', 'revoked')),
  revoked_at timestamptz,
  last_used_at timestamptz,
  created_at timestamptz not null default now()
);

alter table public.ticket_access_members enable row level security;
revoke all on public.ticket_access_members from anon, authenticated;

create index if not exists ticket_access_members_status_idx
  on public.ticket_access_members (status, created_at desc);

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'schedules_created_by_fkey'
  ) then
    alter table public.schedules
      add constraint schedules_created_by_fkey
      foreign key (created_by) references public.ticket_access_members(id) on delete set null;
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_tables
    where schemaname = 'pgmq' and tablename = 'q_ticket_reminders'
  ) then
    perform pgmq.create('ticket_reminders');
  end if;
end $$;

create or replace function public.ticket_hash_invite_token(p_token text)
returns text
language sql
immutable
as $$
  select encode(extensions.digest('ticket-invite:' || coalesce(p_token, ''), 'sha256'), 'hex');
$$;

create or replace function public.ticket_exchange_invite(p_invite_token text)
returns table (id uuid, name text, status text, created_at timestamptz, revoked_at timestamptz, last_used_at timestamptz)
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.ticket_access_members
  set last_used_at = now()
  where invite_token_hash = public.ticket_hash_invite_token(p_invite_token)
    and status = 'active';

  return query
  select m.id, m.name, m.status, m.created_at, m.revoked_at, m.last_used_at
  from public.ticket_access_members m
  where m.invite_token_hash = public.ticket_hash_invite_token(p_invite_token)
    and m.status = 'active'
  limit 1;
end;
$$;

create or replace function public.ticket_bootstrap_legacy_member(p_bootstrap_token text)
returns table (id uuid, name text, status text, created_at timestamptz, revoked_at timestamptz, last_used_at timestamptz)
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  inserted public.ticket_access_members;
  expected_secret text;
begin
  select decrypted_secret
  into expected_secret
  from vault.decrypted_secrets
  where name = 'ticket_legacy_bootstrap_secret'
  order by created_at desc
  limit 1;

  if expected_secret is null or p_bootstrap_token is null or p_bootstrap_token <> expected_secret then
    raise insufficient_privilege using message = 'unauthorized';
  end if;

  if exists (select 1 from public.ticket_access_members as members where members.status = 'active') then
    return;
  end if;

  insert into public.ticket_access_members (name, invite_token_hash, last_used_at)
  values ('Family admin', public.ticket_hash_invite_token(gen_random_uuid()::text || clock_timestamp()::text), now())
  returning * into inserted;

  return query
  select inserted.id, inserted.name, inserted.status, inserted.created_at, inserted.revoked_at, inserted.last_used_at;
end;
$$;

create or replace function public.assert_ticket_backend_credential(p_backend_credential text, p_expected_scope text)
returns uuid
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  parts text[];
  member_id uuid;
  scope text;
  expires_at bigint;
  signature text;
  expected_signature text;
  signing_secret text;
begin
  select decrypted_secret
  into signing_secret
  from vault.decrypted_secrets
  where name = 'ticket_backend_token'
  order by created_at desc
  limit 1;

  if signing_secret is null or p_backend_credential is null then
    raise insufficient_privilege using message = 'unauthorized';
  end if;

  parts := string_to_array(p_backend_credential, '.');
  if array_length(parts, 1) <> 4 then
    raise insufficient_privilege using message = 'unauthorized';
  end if;

  member_id := parts[1]::uuid;
  scope := parts[2];
  expires_at := parts[3]::bigint;
  signature := parts[4];

  if scope <> p_expected_scope or expires_at < floor(extract(epoch from now())) then
    raise insufficient_privilege using message = 'unauthorized';
  end if;

  expected_signature := encode(
    extensions.hmac(format('ticket-rpc:%s:%s:%s', member_id::text, scope, expires_at::text), signing_secret, 'sha256'),
    'hex'
  );

  if signature <> expected_signature then
    raise insufficient_privilege using message = 'unauthorized';
  end if;

  if not exists (
    select 1 from public.ticket_access_members
    where id = member_id and status = 'active'
  ) then
    raise insufficient_privilege using message = 'unauthorized';
  end if;

  return member_id;
end;
$$;

create or replace function public.ticket_get_member_session(p_backend_credential text)
returns table (id uuid, name text, status text, created_at timestamptz, revoked_at timestamptz, last_used_at timestamptz)
language plpgsql
security definer
set search_path = public
as $$
declare
  member_id uuid;
begin
  member_id := public.assert_ticket_backend_credential(p_backend_credential, 'ticket:session');
  return query
  select m.id, m.name, m.status, m.created_at, m.revoked_at, m.last_used_at
  from public.ticket_access_members m
  where m.id = member_id and m.status = 'active'
  limit 1;
end;
$$;

create or replace function public.ticket_list_members(p_backend_credential text)
returns table (id uuid, name text, status text, created_at timestamptz, revoked_at timestamptz, last_used_at timestamptz)
language plpgsql
security definer
set search_path = public
as $$
begin
  perform public.assert_ticket_backend_credential(p_backend_credential, 'ticket:members:list');
  return query
  select m.id, m.name, m.status, m.created_at, m.revoked_at, m.last_used_at
  from public.ticket_access_members
  m
  order by m.status = 'active' desc, m.created_at asc;
end;
$$;

create or replace function public.ticket_create_member(p_backend_credential text, p_name text, p_invite_token text)
returns table (id uuid, name text, status text, created_at timestamptz, revoked_at timestamptz, last_used_at timestamptz)
language plpgsql
security definer
set search_path = public
as $$
declare
  caller_id uuid;
  created_member public.ticket_access_members;
begin
  caller_id := public.assert_ticket_backend_credential(p_backend_credential, 'ticket:members:create');
  if caller_id is null then
    raise insufficient_privilege using message = 'unauthorized';
  end if;

  if nullif(trim(p_name), '') is null or char_length(trim(p_name)) > 80 then
    raise exception 'member name must be 1-80 characters';
  end if;

  if nullif(trim(p_invite_token), '') is null then
    raise exception 'invite token is required';
  end if;

  insert into public.ticket_access_members (name, invite_token_hash)
  values (trim(p_name), public.ticket_hash_invite_token(p_invite_token))
  returning * into created_member;

  return query
  select created_member.id, created_member.name, created_member.status, created_member.created_at, created_member.revoked_at, created_member.last_used_at;
end;
$$;

create or replace function public.ticket_revoke_member(p_backend_credential text, p_member_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  caller_id uuid;
  active_count integer;
  changed_count integer;
begin
  caller_id := public.assert_ticket_backend_credential(p_backend_credential, 'ticket:members:revoke');
  if caller_id is null then
    raise insufficient_privilege using message = 'unauthorized';
  end if;

  select count(*) into active_count
  from public.ticket_access_members
  where status = 'active';

  if active_count <= 1 then
    raise exception 'cannot revoke the last active member';
  end if;

  update public.ticket_access_members
  set status = 'revoked', revoked_at = now()
  where id = p_member_id and status = 'active';

  get diagnostics changed_count = row_count;
  return changed_count > 0;
end;
$$;

create or replace function public.ticket_get_reminder_job(p_reminder_id bigint)
returns table (
  reminder_id bigint,
  schedule_id text,
  event_name text,
  event_site text,
  event_url text,
  sale_at timestamptz,
  offset_minutes integer,
  attempt_count integer,
  status text
)
language sql
security definer
set search_path = public
as $$
  select
    r.id,
    s.id,
    s.name,
    s.site,
    s.url,
    s.sale_at,
    r.offset_minutes,
    r.attempt_count,
    r.status
  from public.schedule_reminders r
  join public.schedules s on s.id = r.schedule_id
  where r.id = p_reminder_id;
$$;

create or replace function public.ticket_read_reminder_queue(p_limit integer default 10, p_visibility_seconds integer default 120)
returns table (
  msg_id bigint,
  read_ct integer,
  enqueued_at timestamptz,
  vt timestamptz,
  message jsonb,
  headers jsonb
)
language sql
security definer
set search_path = public, pgmq
as $$
  select *
  from pgmq.read('ticket_reminders', greatest(1, least(p_visibility_seconds, 3600)), greatest(1, least(p_limit, 100)));
$$;

create or replace function public.ticket_delete_reminder_queue_message(p_message_id bigint)
returns boolean
language sql
security definer
set search_path = public, pgmq
as $$
  select pgmq.delete('ticket_reminders', p_message_id);
$$;

drop function if exists public.create_ticket_schedule(text, text, text, text, text, timestamptz);
drop function if exists public.ticket_list_schedules(text);
drop function if exists public.delete_ticket_schedule(text, text);

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

create or replace function public.ticket_list_schedules(p_backend_credential text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  result jsonb;
begin
  perform public.assert_ticket_backend_credential(p_backend_credential, 'ticket:schedules:list');

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

create or replace function public.delete_ticket_schedule(p_backend_credential text, p_id text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  deleted_count integer;
begin
  perform public.assert_ticket_backend_credential(p_backend_credential, 'ticket:schedules:delete');
  delete from public.schedules where id = p_id;
  get diagnostics deleted_count = row_count;
  return deleted_count > 0;
end;
$$;

drop function if exists public.assert_ticket_backend_token(text);

revoke all on function public.ticket_exchange_invite(text) from public, anon, authenticated;
revoke all on function public.ticket_bootstrap_legacy_member(text) from public, anon, authenticated;
revoke all on function public.assert_ticket_backend_credential(text, text) from public, anon, authenticated;
revoke all on function public.ticket_get_member_session(text) from public, anon, authenticated;
revoke all on function public.ticket_list_members(text) from public, anon, authenticated;
revoke all on function public.ticket_create_member(text, text, text) from public, anon, authenticated;
revoke all on function public.ticket_revoke_member(text, uuid) from public, anon, authenticated;
revoke all on function public.ticket_get_reminder_job(bigint) from public, anon, authenticated;
revoke all on function public.ticket_read_reminder_queue(integer, integer) from public, anon, authenticated;
revoke all on function public.ticket_delete_reminder_queue_message(bigint) from public, anon, authenticated;
revoke all on function public.create_ticket_schedule(text, text, text, text, text, timestamptz) from public, anon, authenticated;
revoke all on function public.ticket_list_schedules(text) from public, anon, authenticated;
revoke all on function public.delete_ticket_schedule(text, text) from public, anon, authenticated;

grant execute on function public.ticket_exchange_invite(text) to anon;
grant execute on function public.ticket_bootstrap_legacy_member(text) to anon;
grant execute on function public.ticket_get_member_session(text) to anon;
grant execute on function public.ticket_list_members(text) to anon;
grant execute on function public.ticket_create_member(text, text, text) to anon;
grant execute on function public.ticket_revoke_member(text, uuid) to anon;
grant execute on function public.create_ticket_schedule(text, text, text, text, text, timestamptz) to anon;
grant execute on function public.ticket_list_schedules(text) to anon;
grant execute on function public.delete_ticket_schedule(text, text) to anon;

grant execute on function public.ticket_get_reminder_job(bigint) to service_role;
grant execute on function public.ticket_read_reminder_queue(integer, integer) to service_role;
grant execute on function public.ticket_delete_reminder_queue_message(bigint) to service_role;



