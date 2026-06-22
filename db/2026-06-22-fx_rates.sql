-- FX rate cache for live THB conversion in trip summaries.
-- Read/written by worker getRateThb(): cached rate is reused while < 12h old,
-- otherwise refreshed from the FX API. One row per currency.
create table if not exists fx_rates (
  currency   text primary key,
  rate_thb   numeric not null,
  updated_at timestamptz not null default now()
);
