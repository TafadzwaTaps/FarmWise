-- =============================================================================
-- FarmWise AI — Field reports (additive migration)
-- =============================================================================
-- Run this in: Supabase Dashboard → SQL Editor → New query → Run
--
-- Additive only — does not touch any existing table.
--
-- Backs the worker-facing field-reporting system: notes, live data, and
-- photo/video captures of livestock and crops that farm managers/owners
-- review and give feedback on. Any farm member can submit a report (not
-- just workers — a manager might log a field note too); only farmer/
-- farm_manager roles can add feedback and mark a report reviewed.
--
-- ALSO REQUIRES a Supabase Storage bucket for media (SQL alone can't
-- create one) — see the note at the bottom of this file.
-- =============================================================================

create extension if not exists pgcrypto;

create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create table if not exists field_reports (
    id               uuid primary key default gen_random_uuid(),
    farm_id          uuid not null references farms(id) on delete cascade,
    worker_id        uuid not null references users(id) on delete cascade,  -- the submitter
    report_type      text not null
                        check (report_type in ('livestock', 'crop', 'general')),
    batch_id         uuid references animal_batches(id) on delete set null,  -- set when report_type='livestock'
    subject          text,   -- free text for crop/general reports with no batch to link (e.g. "North field maize")
    notes            text not null,
    media             jsonb not null default '[]'::jsonb,  -- [{"url": "...", "type": "image"|"video"}, ...]
    status           text not null default 'pending'
                        check (status in ('pending', 'reviewed')),
    manager_feedback text,
    reviewed_by      uuid references users(id) on delete set null,
    reviewed_at      timestamptz,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index if not exists field_reports_farm_id_idx      on field_reports (farm_id, created_at desc);
create index if not exists field_reports_worker_id_idx    on field_reports (worker_id, created_at desc);
create index if not exists field_reports_status_idx       on field_reports (farm_id, status);
create index if not exists field_reports_batch_id_idx     on field_reports (batch_id);

drop trigger if exists trg_field_reports_updated_at on field_reports;
create trigger trg_field_reports_updated_at
    before update on field_reports
    for each row execute function set_updated_at();

-- =============================================================================
-- MANUAL STEP — Supabase Storage bucket for media (SQL can't create this):
--   1. Supabase Dashboard → Storage → New bucket
--   2. Name it exactly:  field-reports
--   3. Make it a PUBLIC bucket (so captured photos/videos are viewable via
--      a plain URL in the app without needing signed-URL logic)
--   File size/type limits are enforced by the backend, not the bucket —
--   see routes/field_report_routes.py.
-- =============================================================================

-- Done. Verify with:
--   select table_name from information_schema.tables
--   where table_schema='public' and table_name = 'field_reports';
-- =============================================================================
