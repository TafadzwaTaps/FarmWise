-- =============================================================================
-- FarmWise AI — Worker management tables (additive migration)
-- =============================================================================
-- Run this in: Supabase Dashboard → SQL Editor → New query → Run
--
-- This does NOT touch any existing table — it only adds three new ones.
-- Safe to run against your current database; nothing here drops data.
--
-- Same conventions as the main schema: TEXT + CHECK instead of native
-- Postgres enums (see farmwise_schema.sql for why), full indexing, and an
-- updated_at trigger reusing the set_updated_at() function that schema
-- already created.
-- =============================================================================

create extension if not exists pgcrypto;

-- If you're running this against a database that somehow doesn't have the
-- trigger function yet (e.g. you only ran an older schema version), this
-- recreates it harmlessly — CREATE OR REPLACE is idempotent.
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;


-- =============================================================================
-- workers — farm employees. Deliberately separate from `users`/`farm_members`:
-- most farm workers never log into the app at all, so this is a plain
-- employee record, not an account.
-- =============================================================================
create table if not exists workers (
    id            uuid primary key default gen_random_uuid(),
    farm_id       uuid not null references farms(id) on delete cascade,
    full_name     text not null,
    position      text,
    phone_number  text,
    wage_amount   numeric(12,2),
    wage_type     text
                    check (wage_type in ('daily', 'weekly', 'monthly')),
    hire_date     date,
    status        text not null default 'active'
                    check (status in ('active', 'inactive')),
    notes         text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists workers_farm_id_idx      on workers (farm_id);
create index if not exists workers_farm_status_idx    on workers (farm_id, status);

drop trigger if exists trg_workers_updated_at on workers;
create trigger trg_workers_updated_at
    before update on workers
    for each row execute function set_updated_at();


-- =============================================================================
-- worker_attendance — one row per worker per day
-- =============================================================================
create table if not exists worker_attendance (
    id          uuid primary key default gen_random_uuid(),
    worker_id   uuid not null references workers(id) on delete cascade,
    date        date not null,
    status      text not null
                  check (status in ('present', 'absent', 'half_day')),
    notes       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    constraint worker_attendance_one_record_per_day unique (worker_id, date)
);

create index if not exists worker_attendance_worker_id_idx  on worker_attendance (worker_id);
create index if not exists worker_attendance_date_idx        on worker_attendance (worker_id, date desc);

drop trigger if exists trg_worker_attendance_updated_at on worker_attendance;
create trigger trg_worker_attendance_updated_at
    before update on worker_attendance
    for each row execute function set_updated_at();


-- =============================================================================
-- worker_payments — payroll / salary history
-- =============================================================================
create table if not exists worker_payments (
    id             uuid primary key default gen_random_uuid(),
    worker_id      uuid not null references workers(id) on delete cascade,
    amount         numeric(12,2) not null check (amount > 0),
    payment_date   date not null,
    period_start   date,
    period_end     date,
    notes          text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create index if not exists worker_payments_worker_id_idx     on worker_payments (worker_id);
create index if not exists worker_payments_payment_date_idx   on worker_payments (worker_id, payment_date desc);

drop trigger if exists trg_worker_payments_updated_at on worker_payments;
create trigger trg_worker_payments_updated_at
    before update on worker_payments
    for each row execute function set_updated_at();

-- =============================================================================
-- Done. Verify with:
--   select table_name from information_schema.tables
--   where table_schema='public' and table_name in
--     ('workers','worker_attendance','worker_payments');
-- =============================================================================
