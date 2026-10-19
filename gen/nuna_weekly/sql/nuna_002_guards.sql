-- nuna_002_guards (적용 완료 2026-10-19) — 재적용 금지
CREATE OR REPLACE FUNCTION public.nuna_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_nuna_episodes_touch BEFORE UPDATE ON public.nuna_episodes
  FOR EACH ROW EXECUTE FUNCTION public.nuna_touch_updated_at();
CREATE OR REPLACE TRIGGER trg_nuna_targets_touch BEFORE UPDATE ON public.nuna_publish_targets
  FOR EACH ROW EXECUTE FUNCTION public.nuna_touch_updated_at();

CREATE OR REPLACE FUNCTION public.nuna_cost_ledger_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'NUNA_COST_LEDGER_APPEND_ONLY: delete not allowed (id=%)', OLD.id;
  END IF;
  IF OLD.state <> 'reserved' THEN
    RAISE EXCEPTION 'NUNA_COST_LEDGER_SETTLED: row % already %', OLD.id, OLD.state;
  END IF;
  IF NEW.state = 'reserved' OR NEW.episode_id <> OLD.episode_id OR NEW.provider <> OLD.provider
     OR NEW.step <> OLD.step OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'NUNA_COST_LEDGER_INVALID_UPDATE: row %', OLD.id;
  END IF;
  NEW.settled_at := now();
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_nuna_cost_guard BEFORE UPDATE OR DELETE ON public.nuna_cost_ledger
  FOR EACH ROW EXECUTE FUNCTION public.nuna_cost_ledger_guard();

CREATE OR REPLACE FUNCTION public.nuna_reserve_cost(
  p_episode_id TEXT, p_step TEXT, p_provider TEXT, p_model TEXT,
  p_units NUMERIC, p_unit TEXT, p_usd NUMERIC, p_fx_rate NUMERIC,
  p_episode_cap_krw NUMERIC, p_month_cap_krw NUMERIC, p_request_id TEXT DEFAULT NULL)
RETURNS BIGINT LANGUAGE plpgsql SET search_path = public AS $$
DECLARE
  v_krw NUMERIC := round(p_usd * p_fx_rate);
  v_ep NUMERIC;
  v_month NUMERIC;
  v_month_start TIMESTAMPTZ := (date_trunc('month', now() AT TIME ZONE 'Asia/Seoul')) AT TIME ZONE 'Asia/Seoul';
  v_id BIGINT;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('nuna_cost_ledger'));
  SELECT COALESCE(SUM(krw),0) INTO v_ep FROM public.nuna_cost_ledger
   WHERE episode_id = p_episode_id AND state IN ('reserved','charged');
  SELECT COALESCE(SUM(krw),0) INTO v_month FROM public.nuna_cost_ledger
   WHERE created_at >= v_month_start AND state IN ('reserved','charged');
  IF v_ep + v_krw > p_episode_cap_krw THEN
    RAISE EXCEPTION 'NUNA_BUDGET_EXCEEDED: episode % used % + need % > cap %', p_episode_id, v_ep, v_krw, p_episode_cap_krw;
  END IF;
  IF v_month + v_krw > p_month_cap_krw THEN
    RAISE EXCEPTION 'NUNA_BUDGET_EXCEEDED: month used % + need % > cap %', v_month, v_krw, p_month_cap_krw;
  END IF;
  INSERT INTO public.nuna_cost_ledger (episode_id, step, provider, model, units, unit, usd, fx_rate, krw, state, request_id)
  VALUES (p_episode_id, p_step, p_provider, p_model, p_units, p_unit, p_usd, p_fx_rate, v_krw, 'reserved', p_request_id)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

GRANT ALL ON public.nuna_episodes, public.nuna_publish_targets, public.nuna_cost_ledger, public.nuna_step_runs TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.nuna_publish_targets_id_seq, public.nuna_cost_ledger_id_seq, public.nuna_step_runs_id_seq TO service_role;
GRANT EXECUTE ON FUNCTION public.nuna_reserve_cost(TEXT,TEXT,TEXT,TEXT,NUMERIC,TEXT,NUMERIC,NUMERIC,NUMERIC,NUMERIC,TEXT) TO service_role;

COMMENT ON TABLE public.nuna_episodes IS 'NUNA weekly news shorts episode ledger (v1.0.0, 2026-10-19)';
COMMENT ON TABLE public.nuna_cost_ledger IS 'NUNA paid-call cost ledger: append-only, settle once (v1.0.0)';

NOTIFY pgrst, 'reload schema';
