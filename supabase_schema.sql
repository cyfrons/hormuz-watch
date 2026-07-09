-- Run this once in Supabase: your project -> SQL Editor -> New query -> paste -> Run.
-- Creates the table that stores periodic historical snapshots.

create table snapshots (
  id            bigint generated always as identity primary key,
  taken_at      timestamptz not null default now(),
  total_vessels int not null,
  underway      int not null,
  at_anchor     int not null,
  other_status  int not null,
  vessels       jsonb not null default '[]'
);

-- Row Level Security, on by default in Supabase. These policies are
-- deliberately permissive: anyone can read (needed so the public map page
-- can show history) and anyone can insert (needed because we're using the
-- anon key from the backend, not a private service key). That's an
-- acceptable tradeoff for a public vessel-tracking hobby project with no
-- sensitive data - not something you'd want for anything involving user
-- accounts, payments, or private information.

alter table snapshots enable row level security;

create policy "Allow public read" on snapshots
  for select using (true);

create policy "Allow insert" on snapshots
  for insert with check (true);

-- Speeds up "give me the last N days" queries, which is what the
-- eventual historical chart will run most often.
create index snapshots_taken_at_idx on snapshots (taken_at desc);
