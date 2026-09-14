-- =============================================================================
-- FarmWise AI — Password reset tokens (additive migration)
-- =============================================================================
-- Run this in: Supabase Dashboard → SQL Editor → New query → Run
--
-- Additive only — does not touch any existing table. Backs the new
-- link-based password reset flow for email destinations (matches
-- WaziBot's token design). Phone-based reset keeps using the existing
-- otp_codes table unchanged — nothing about that path is touched here.
-- =============================================================================

create extension if not exists pgcrypto;

create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create table if not exists password_reset_tokens (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references users(id) on delete cascade,
    token_hash   text not null unique,   -- SHA-256 hex digest; the raw token is NEVER stored
    expires_at   timestamptz not null,
    used_at      timestamptz,
    ip_address   text,
    user_agent   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index if not exists password_reset_tokens_user_id_idx     on password_reset_tokens (user_id);
create index if not exists password_reset_tokens_token_hash_idx  on password_reset_tokens (token_hash);
create index if not exists password_reset_tokens_pending_idx     on password_reset_tokens (user_id) where used_at is null;

drop trigger if exists trg_password_reset_tokens_updated_at on password_reset_tokens;
create trigger trg_password_reset_tokens_updated_at
    before update on password_reset_tokens
    for each row execute function set_updated_at();

-- =============================================================================
-- Done. Verify with:
--   select table_name from information_schema.tables
--   where table_schema='public' and table_name = 'password_reset_tokens';
-- =============================================================================
