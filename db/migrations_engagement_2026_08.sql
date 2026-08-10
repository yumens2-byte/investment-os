-- ============================================================
-- 참여형 콘텐츠 A/C/D/F안 마이그레이션 (2026-08-10)
-- 적용: Supabase apply_migration (프로젝트 ccomoimhhttaklfadaos)
-- 적용 후: NOTIFY pgrst, 'reload schema';
-- ============================================================

-- ── A안: character_votes ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.character_votes (
    id              BIGSERIAL PRIMARY KEY,
    vote_date       DATE NOT NULL UNIQUE,
    candidates      JSONB NOT NULL,
    header_tweet_id TEXT,
    winner          TEXT,
    status          VARCHAR(10) NOT NULL DEFAULT 'open',   -- open|closed|skipped
    retry_count     INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_character_votes_status
    ON public.character_votes(status);

-- ── C안: prediction_rounds ───────────────────────────────────
CREATE TABLE IF NOT EXISTS public.prediction_rounds (
    id               BIGSERIAL PRIMARY KEY,
    week_key         DATE NOT NULL UNIQUE,          -- 해당 주 월요일
    question         TEXT NOT NULL,
    metric           VARCHAR(20) NOT NULL DEFAULT 'SPY',
    baseline_value   NUMERIC(12,4) NOT NULL,
    baseline_date    DATE NOT NULL,
    settle_date      DATE NOT NULL,
    open_tweet_id    TEXT,
    option_tweets    JSONB,                          -- {"up": id, "down": id}
    result           VARCHAR(10),                    -- up|down|flat
    final_value      NUMERIC(12,4),
    settle_tweet_id  TEXT,
    status           VARCHAR(10) NOT NULL DEFAULT 'open',  -- open|settled|void
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_prediction_rounds_status
    ON public.prediction_rounds(status);

-- ── D안: daily_alerts followup 컬럼 ──────────────────────────
ALTER TABLE public.daily_alerts
    ADD COLUMN IF NOT EXISTS followup_tweet_id TEXT;

-- ── F안: quiz_rounds ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.quiz_rounds (
    id               BIGSERIAL PRIMARY KEY,
    quiz_date        DATE NOT NULL UNIQUE,
    metric_key       VARCHAR(30) NOT NULL,
    snapshot_date    DATE NOT NULL,
    question_text    TEXT NOT NULL,
    answer           VARCHAR(1) NOT NULL,             -- A|B|C|D
    answer_value     TEXT NOT NULL,
    answer_label     TEXT NOT NULL,
    tweet_id         TEXT NOT NULL,
    answer_tweet_id  TEXT,
    status           VARCHAR(10) NOT NULL DEFAULT 'open',  -- open|answered
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_quiz_rounds_status
    ON public.quiz_rounds(status);
