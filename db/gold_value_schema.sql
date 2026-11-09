-- db/gold_value_schema.sql — 금으로 잰 화폐가치 패널 이력 (DN2026_0109)
-- 적용: Supabase(ccomoimhhttaklfadaos). 여러 번 실행해도 안전(멱등, STD-DB-02).
-- 권한: kr_sleeve_* 와 같은 패턴(RLS 비활성 + anon 권한, STD-DB-03).
-- series: krx_gold_krw_g(KRX 금시장 원/g) · gc_gold_usd_oz(COMEX 근월물 달러/oz) · usdkrw · jpykrw100

BEGIN;

CREATE TABLE IF NOT EXISTS public.gold_value_daily (
  day         DATE NOT NULL,
  series      TEXT NOT NULL CHECK (series IN ('krx_gold_krw_g', 'gc_gold_usd_oz', 'usdkrw', 'jpykrw100')),
  close       NUMERIC(14, 4) NOT NULL CHECK (close > 0),
  source      TEXT NOT NULL,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, series)
);

CREATE INDEX IF NOT EXISTS gold_value_daily_series_day ON public.gold_value_daily (series, day);

ALTER TABLE public.gold_value_daily DISABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON public.gold_value_daily TO anon, service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- 적용 확인:
-- SELECT c.relrowsecurity, has_table_privilege('anon', 'public.gold_value_daily', 'INSERT')
--   FROM pg_class c WHERE c.relname = 'gold_value_daily';
