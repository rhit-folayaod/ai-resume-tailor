-- resume-tailor hosted mode (invite-only friends)
-- Run this in the Supabase SQL editor or via `supabase db push`.

-- Emails allowed to use the app. Insert a row before inviting a friend.
create table if not exists public.allowed_users (
  email text primary key,
  created_at timestamptz not null default now(),
  note text
);

alter table public.allowed_users enable row level security;

-- Per-user content store. JSONB matches ResumeStore; server validates with Pydantic.
create table if not exists public.resume_stores (
  user_id uuid primary key references auth.users (id) on delete cascade,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.resume_stores enable row level security;

-- Usage events for daily rate limits (parse = JD LLM call, compile = PDF).
create table if not exists public.usage_events (
  id bigserial primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  kind text not null check (kind in ('parse', 'compile')),
  created_at timestamptz not null default now()
);

create index if not exists usage_events_user_day_idx
  on public.usage_events (user_id, kind, created_at desc);

alter table public.usage_events enable row level security;

-- The Fly app talks to PostgREST with the service role key and bypasses RLS.
-- No policies for anon/authenticated: browsers never hit these tables directly.

comment on table public.allowed_users is
  'Invite list. Insert lowercased emails; only these users can sign in and use the API.';
comment on table public.resume_stores is
  'One ResumeStore JSON document per auth user.';
comment on table public.usage_events is
  'Append-only counters for per-user daily parse/compile limits.';
