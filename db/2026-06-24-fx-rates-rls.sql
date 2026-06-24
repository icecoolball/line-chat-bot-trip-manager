-- The Cloudflare Worker is the only runtime owner of the FX cache and uses
-- SUPABASE_SERVICE_ROLE_KEY. Browser/anon clients do not need direct access.
alter table public.fx_rates enable row level security;
revoke all on public.fx_rates from anon, authenticated;
grant select, insert, update, delete on public.fx_rates to service_role;
