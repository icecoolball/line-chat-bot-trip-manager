-- Edge Function reads LINE configuration through this service-role-only RPC.
-- Secret values are stored separately in Supabase Vault and never in Git.
create or replace function public.get_ticket_line_config()
returns table (channel_access_token text, target_id text)
language sql
security definer
set search_path = public, vault
as $$
  select
    (select decrypted_secret from vault.decrypted_secrets where name = 'ticket_line_token' limit 1),
    (select decrypted_secret from vault.decrypted_secrets where name = 'ticket_line_target' limit 1);
$$;

revoke all on function public.get_ticket_line_config() from public, anon, authenticated;
grant execute on function public.get_ticket_line_config() to service_role;
