-- db/kr_sleeve_schema.sql — KR 슬리브 기록 테이블 (DN2026_0108)
-- 적용: 마스터가 Supabase(ccomoimhhttaklfadaos) SQL Editor에서 실행. 여러 번 실행해도 안전(멱등, STD-DB-02).
-- 권한: 이 레포는 SUPABASE_KEY(anon 계열)로 접근한다. 기존 public.daily_analysis·daily_snapshots와 같은
--       패턴(RLS 비활성 + anon 권한)으로 맞춘다(2026-11-09 운영 DB 실측: 두 테이블 rls=false, anon INSERT·SELECT 가능).
--       RLS를 켜고 정책이 없으면 anon SELECT가 200·0건으로 조용히 실패한다(STD-DB-03).

BEGIN;

CREATE TABLE IF NOT EXISTS public.kr_sleeve_daily (
  id                  BIGSERIAL PRIMARY KEY,
  run_date            DATE NOT NULL UNIQUE,
  status              TEXT NOT NULL CHECK (status IN ('computed', 'skipped', 'published')),
  reason              TEXT,
  mode                TEXT NOT NULL CHECK (mode IN ('shadow', 'live')),
  input_timestamp     TIMESTAMPTZ,
  regime              TEXT,
  risk_level          TEXT,
  k_pct               INT,
  internal_a          JSONB,
  internal_b          JSONB,
  total_allocation    JSONB,
  total_allocation_b  JSONB,
  lookthrough_us_pct  NUMERIC(5, 1),
  kr_prices           JSONB,
  kr_market_closed    BOOLEAN,
  version             TEXT NOT NULL,
  run_id              TEXT,
  sha                 TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kr_sleeve_publish (
  id          BIGSERIAL PRIMARY KEY,
  run_date    DATE NOT NULL,
  channel     TEXT NOT NULL CHECK (channel IN ('x', 'telegram', 'facebook')),
  status      TEXT NOT NULL CHECK (status IN ('pending', 'ok', 'failed', 'unknown', 'dry_run', 'disabled')),
  post_id     TEXT,
  reason      TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_date, channel)
);

ALTER TABLE public.kr_sleeve_daily   DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.kr_sleeve_publish DISABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON public.kr_sleeve_daily   TO anon, service_role;
GRANT SELECT, INSERT, UPDATE ON public.kr_sleeve_publish TO anon, service_role;
GRANT USAGE, SELECT ON SEQUENCE public.kr_sleeve_daily_id_seq   TO anon, service_role;
GRANT USAGE, SELECT ON SEQUENCE public.kr_sleeve_publish_id_seq TO anon, service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- 적용 확인(배포 전 점검, STD-DB-03·04):
-- SELECT c.relname, c.relrowsecurity, has_table_privilege('anon', 'public.'||c.relname, 'INSERT') AS anon_ins
--   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'public' AND c.relname LIKE 'kr_sleeve%';
