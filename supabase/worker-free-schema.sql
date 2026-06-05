-- Cloudflare Workers migration support tables.
-- Run this in Supabase SQL editor before switching the LINE webhook URL.

create table if not exists public.bot_states (
  user_id text primary key,
  group_id text,
  action text not null,
  payload jsonb not null default '{}'::jsonb,
  expires_at timestamptz,
  updated_at timestamptz not null default now()
);

create index if not exists bot_states_expires_at_idx on public.bot_states (expires_at);

create table if not exists public.export_jobs (
  id uuid primary key default gen_random_uuid(),
  trip_id text,
  trip_title text,
  target_id text not null,
  status text not null default 'queued',
  file_path text,
  public_url text,
  error text,
  requested_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists export_jobs_status_created_at_idx on public.export_jobs (status, created_at desc);

-- Optional cleanup for stale conversation state.
create or replace function public.delete_expired_bot_states()
returns void
language sql
as $$
  delete from public.bot_states where expires_at is not null and expires_at < now();
$$;
