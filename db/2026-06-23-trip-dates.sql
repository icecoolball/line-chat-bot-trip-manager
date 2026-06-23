-- Optional trip date range, captured at trip creation.
-- Used for Excel "Day N" sheet labels and per-day sheets across the range.
alter table trips add column if not exists start_date date;
alter table trips add column if not exists end_date date;
