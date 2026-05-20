-- Run this in your Supabase SQL editor to create the job_results table.
-- After creating, set up Row Level Security as needed.

create table if not exists job_results (
  job_url              text primary key,
  title                text,
  company              text,
  location             text,
  site                 text,
  date_posted          text,
  fit_score            integer,
  job_sector           text,
  seniority_match      text,
  fit_reasoning        text,
  matching_skills      text,
  concerns             text,
  assessed_at          date,
  is_active            text,
  last_active_check    date,
  description          text,
  created_at           timestamptz default now()
);

-- Index for fast sorting by score and date
create index if not exists job_results_fit_score_idx on job_results (fit_score desc);
create index if not exists job_results_assessed_at_idx on job_results (assessed_at desc);

-- Enable Row Level Security (optional but recommended)
-- alter table job_results enable row level security;
-- create policy "Allow all reads" on job_results for select using (true);
