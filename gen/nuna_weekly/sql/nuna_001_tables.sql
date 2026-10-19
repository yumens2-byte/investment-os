-- nuna_001_tables (적용 완료 2026-10-19, project ccomoimhhttaklfadaos) — 재적용 금지
CREATE TABLE IF NOT EXISTS public.nuna_episodes (
  episode_id        TEXT PRIMARY KEY CHECK (episode_id ~ '^nuna-[0-9]{4}-W[0-9]{2}$'),
  publish_date      DATE NOT NULL UNIQUE,
  us_days           DATE[] NOT NULL DEFAULT '{}',
  duration_target_sec INT NOT NULL DEFAULT 60 CHECK (duration_target_sec BETWEEN 3 AND 90),
  status            TEXT NOT NULL DEFAULT 'PLANNED'
                    CHECK (status IN ('PLANNED','SKIPPED','COLLECTED','SCRIPTED','VOICED','IMAGED','CLIPPED',
                                      'ASSEMBLED','QC_PASSED','STAGED','NOTIFIED','HELD','SCHEDULED',
                                      'PUBLISHED','PARTIAL','FAILED')),
  skip_reason       TEXT,
  facts_json        JSONB,
  script_json       JSONB,
  assets_json       JSONB,
  qc_json           JSONB,
  media_json        JSONB,
  notified_at       TIMESTAMPTZ,
  hold_at           TIMESTAMPTZ,
  hold_by           TEXT,
  scheduled_for     TIMESTAMPTZ,
  last_error        TEXT,
  pipeline_version  TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.nuna_publish_targets (
  id                BIGSERIAL PRIMARY KEY,
  episode_id        TEXT NOT NULL REFERENCES public.nuna_episodes(episode_id),
  platform          TEXT NOT NULL CHECK (platform IN ('instagram','threads','youtube','facebook')),
  key_alias         TEXT NOT NULL CHECK (key_alias IN ('main','sub')),
  variant           TEXT NOT NULL DEFAULT 'main' CHECK (variant IN ('main','fb_cut')),
  status            TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','scheduled','published','failed','blocked','skipped')),
  idempotency_key   TEXT NOT NULL UNIQUE CHECK (char_length(idempotency_key) <= 255),
  zernio_post_id    TEXT,
  platform_post_url TEXT,
  attempts          INT NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
  last_error        TEXT,
  request_json      JSONB,
  response_json     JSONB,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (episode_id, platform)
);

CREATE TABLE IF NOT EXISTS public.nuna_cost_ledger (
  id          BIGSERIAL PRIMARY KEY,
  episode_id  TEXT NOT NULL REFERENCES public.nuna_episodes(episode_id),
  step        TEXT NOT NULL CHECK (step IN ('script','tts','images','clips','poc')),
  provider    TEXT NOT NULL CHECK (provider IN ('anthropic','gemini','fal')),
  model       TEXT NOT NULL,
  units       NUMERIC(12,3) NOT NULL CHECK (units >= 0),
  unit        TEXT NOT NULL CHECK (unit IN ('token','sec','image')),
  usd         NUMERIC(10,4) NOT NULL CHECK (usd >= 0),
  fx_rate     NUMERIC(10,2) NOT NULL CHECK (fx_rate > 0),
  krw         NUMERIC(10,0) NOT NULL CHECK (krw >= 0),
  state       TEXT NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','charged','released')),
  request_id  TEXT UNIQUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  settled_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_nuna_cost_created ON public.nuna_cost_ledger (created_at);
CREATE INDEX IF NOT EXISTS idx_nuna_cost_episode ON public.nuna_cost_ledger (episode_id);

CREATE TABLE IF NOT EXISTS public.nuna_step_runs (
  id           BIGSERIAL PRIMARY KEY,
  episode_id   TEXT NOT NULL,
  step         TEXT NOT NULL,
  gh_run_id    TEXT,
  status       TEXT NOT NULL CHECK (status IN ('started','ok','failed','skipped')),
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  duration_ms  INT,
  error        TEXT,
  version      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nuna_step_runs_ep ON public.nuna_step_runs (episode_id, step);

ALTER TABLE public.nuna_episodes        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nuna_publish_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nuna_cost_ledger     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nuna_step_runs       ENABLE ROW LEVEL SECURITY;
