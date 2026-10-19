-- nuna_003_least_privilege (적용 완료 2026-10-19, 마스터 확인 후) — 재적용 금지
REVOKE ALL ON public.nuna_episodes, public.nuna_publish_targets, public.nuna_cost_ledger, public.nuna_step_runs
  FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.nuna_publish_targets_id_seq, public.nuna_cost_ledger_id_seq, public.nuna_step_runs_id_seq
  FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.nuna_reserve_cost(TEXT,TEXT,TEXT,TEXT,NUMERIC,TEXT,NUMERIC,NUMERIC,NUMERIC,NUMERIC,TEXT)
  FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.nuna_touch_updated_at() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.nuna_cost_ledger_guard() FROM PUBLIC, anon, authenticated;
NOTIFY pgrst, 'reload schema';
