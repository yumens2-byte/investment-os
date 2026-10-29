-- Comic Novel Facebook production schema; approved deployment 2026-10-12.
-- PostgreSQL 14+ (native sha256); no pgcrypto extension required.
BEGIN;
ALTER TABLE public.comic_novels ADD COLUMN IF NOT EXISTS publish_fb_data date;
CREATE INDEX IF NOT EXISTS ix_comic_novels_fb_pending ON public.comic_novels(publish_date) WHERE publish_fb_data IS NULL;
-- 2026-10-30: Y/N view of publish_fb_data. Derived, so it can never disagree with the date; write only publish_fb_data.
ALTER TABLE public.comic_novels ADD COLUMN IF NOT EXISTS publish_fb_yn char(1) GENERATED ALWAYS AS (CASE WHEN publish_fb_data IS NULL THEN 'N' ELSE 'Y' END) STORED;
CREATE INDEX IF NOT EXISTS ix_comic_novels_fb_yn_pending ON public.comic_novels(publish_date,id) WHERE publish_fb_yn='N';
CREATE TABLE IF NOT EXISTS public.comic_novel_channel_history (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 channel text NOT NULL CHECK(channel IN ('x','facebook')),
 source_date date NOT NULL REFERENCES public.comic_novels(publish_date),
 target_key text NOT NULL,
 slot_week_start date NOT NULL,
 status text NOT NULL CHECK(status IN ('claimed','sending','ok','failed','unknown')),
 expected_count integer NOT NULL DEFAULT 1 CHECK(expected_count>0),
 confirmed_count integer NOT NULL DEFAULT 0 CHECK(confirmed_count>=0),
 post_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
 root_post_id text,
 content_hash text NOT NULL,
 source_content_hash text NOT NULL,
 cover_asset_uri text,
 cover_asset_hash text,
 run_id text NOT NULL DEFAULT '',
 claimed_at timestamptz NOT NULL DEFAULT now(),
 sending_at timestamptz,
 confirmed_at timestamptz,
 updated_at timestamptz NOT NULL DEFAULT now(),
 actor text NOT NULL DEFAULT '',
 note text NOT NULL DEFAULT '',
 error_type text,
 UNIQUE(channel, source_date, target_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uix_novel_fb_active_slot ON public.comic_novel_channel_history(target_key,slot_week_start) WHERE channel='facebook' AND status IN ('claimed','sending','unknown','ok');
CREATE INDEX IF NOT EXISTS ix_novel_history_slot ON public.comic_novel_channel_history(channel,target_key,slot_week_start,status);
ALTER TABLE public.comic_novel_channel_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.comic_novel_channel_history FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.comic_novel_channel_history TO service_role;
GRANT USAGE,SELECT ON SEQUENCE public.comic_novel_channel_history_id_seq TO service_role;

-- User confirmed on 2026-10-12 that every retained published source was actually posted to X.
-- Keep this legacy status confirmation separate from actual numeric X post IDs.
CREATE TABLE IF NOT EXISTS public.comic_novel_legacy_x_confirmations (
 source_date date PRIMARY KEY REFERENCES public.comic_novels(publish_date),
 source_content_hash text NOT NULL,
 confirmed_by text NOT NULL,
 note text NOT NULL,
 confirmed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.comic_novel_legacy_x_confirmations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.comic_novel_legacy_x_confirmations FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.comic_novel_legacy_x_confirmations TO service_role;

CREATE OR REPLACE FUNCTION public.novel_fb_claim(p_source_date date,p_target text,p_slot date,p_run_id text,p_content_hash text,p_source_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE n public.comic_novels%ROWTYPE; h public.comic_novel_channel_history%ROWTYPE; v_hash text; v_oldest date;
BEGIN
 IF coalesce(p_target,'')='' OR coalesce(p_run_id,'')='' OR coalesce(p_content_hash,'')='' OR coalesce(p_source_hash,'')='' OR p_slot IS NULL THEN RAISE EXCEPTION 'invalid_claim_input'; END IF;
 IF p_slot<>date_trunc('week',p_slot::timestamp)::date THEN RAISE EXCEPTION 'slot_must_be_monday'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('novel_fb:'||p_target,0));
 SELECT * INTO n FROM public.comic_novels WHERE publish_date=p_source_date FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','skip','reason','source_missing'); END IF;
 IF n.status IS DISTINCT FROM 'published' OR n.publish_date>(now() AT TIME ZONE 'Asia/Seoul')::date THEN RETURN jsonb_build_object('status','hold','reason','source_not_published_or_future'); END IF;
 IF n.publish_fb_yn='Y' THEN RETURN jsonb_build_object('status','skip','reason','already_published'); END IF;
 v_hash:=encode(sha256(convert_to(coalesce(n.title,'')||E'\n'||coalesce(n.episode_range,'')||E'\n'||coalesce(n.novel_text,''),'UTF8')),'hex');
 IF v_hash<>p_source_hash OR NOT EXISTS(SELECT 1 FROM public.comic_novel_channel_history WHERE channel='x' AND source_date=p_source_date AND status='ok' AND confirmed_count=expected_count AND source_content_hash=v_hash) AND NOT EXISTS(SELECT 1 FROM public.comic_novel_legacy_x_confirmations WHERE source_date=p_source_date AND source_content_hash=v_hash) THEN RETURN jsonb_build_object('status','hold','reason','x_evidence_missing_or_source_changed'); END IF;
 IF EXISTS(SELECT 1 FROM public.comic_novel_channel_history WHERE channel='facebook' AND target_key=p_target AND (status IN ('sending','unknown') OR (status='claimed' AND run_id<>p_run_id))) THEN RETURN jsonb_build_object('status','hold','reason','unresolved_claim'); END IF;
 IF EXISTS(SELECT 1 FROM public.comic_novel_channel_history WHERE channel='facebook' AND target_key=p_target AND status='ok' AND (slot_week_start=p_slot OR date_trunc('week',confirmed_at AT TIME ZONE 'Asia/Seoul')::date=p_slot)) THEN RETURN jsonb_build_object('status','skip','reason','week_already_published'); END IF;
 SELECT min(c.publish_date) INTO v_oldest FROM public.comic_novels c WHERE c.publish_fb_yn='N' AND c.status='published' AND c.publish_date<=(now() AT TIME ZONE 'Asia/Seoul')::date;
 IF v_oldest IS DISTINCT FROM p_source_date THEN RETURN jsonb_build_object('status','hold','reason','not_oldest'); END IF;
 SELECT * INTO h FROM public.comic_novel_channel_history WHERE channel='facebook' AND source_date=p_source_date AND target_key=p_target FOR UPDATE;
 IF FOUND THEN
  IF h.status='claimed' AND h.run_id=p_run_id AND h.slot_week_start=p_slot AND h.content_hash=p_content_hash AND h.source_content_hash=p_source_hash THEN RETURN jsonb_build_object('id',h.id,'status','claimed'); END IF;
  IF h.status<>'failed' THEN RETURN jsonb_build_object('status','hold','reason','claim_exists'); END IF;
  UPDATE public.comic_novel_channel_history SET status='claimed',slot_week_start=p_slot,run_id=p_run_id,content_hash=p_content_hash,source_content_hash=p_source_hash,claimed_at=now(),sending_at=NULL,error_type=NULL,updated_at=now() WHERE id=h.id RETURNING * INTO h;
 ELSE
  INSERT INTO public.comic_novel_channel_history(channel,source_date,target_key,slot_week_start,status,content_hash,source_content_hash,run_id) VALUES('facebook',p_source_date,p_target,p_slot,'claimed',p_content_hash,p_source_hash,p_run_id) RETURNING * INTO h;
 END IF;
 RETURN jsonb_build_object('id',h.id,'status',h.status);
END $$;

CREATE OR REPLACE FUNCTION public.novel_fb_mark_sending(p_claim_id bigint,p_content_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE h public.comic_novel_channel_history%ROWTYPE; n public.comic_novels%ROWTYPE; v_hash text;
BEGIN
 SELECT * INTO h FROM public.comic_novel_channel_history WHERE id=p_claim_id AND channel='facebook';
 IF NOT FOUND THEN RAISE EXCEPTION 'claim_missing'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('novel_fb:'||h.target_key,0));
 SELECT * INTO n FROM public.comic_novels WHERE publish_date=h.source_date FOR UPDATE;
 SELECT * INTO h FROM public.comic_novel_channel_history WHERE id=p_claim_id FOR UPDATE;
 v_hash:=encode(sha256(convert_to(coalesce(n.title,'')||E'\n'||coalesce(n.episode_range,'')||E'\n'||coalesce(n.novel_text,''),'UTF8')),'hex');
 IF h.status<>'claimed' OR h.content_hash<>p_content_hash OR n.publish_fb_yn='Y' OR v_hash<>h.source_content_hash THEN RETURN jsonb_build_object('status','hold','reason','claim_or_content_changed'); END IF;
 IF NOT EXISTS(SELECT 1 FROM public.comic_novel_channel_history WHERE channel='x' AND source_date=h.source_date AND status='ok' AND confirmed_count=expected_count AND source_content_hash=v_hash) AND NOT EXISTS(SELECT 1 FROM public.comic_novel_legacy_x_confirmations WHERE source_date=h.source_date AND source_content_hash=v_hash) THEN RETURN jsonb_build_object('status','hold','reason','x_evidence_changed'); END IF;
 IF EXISTS(SELECT 1 FROM public.comic_novels c WHERE c.publish_date<h.source_date AND c.publish_fb_yn='N' AND c.status='published' AND c.publish_date<=(now() AT TIME ZONE 'Asia/Seoul')::date) THEN RETURN jsonb_build_object('status','hold','reason','not_oldest'); END IF;
 IF EXISTS(SELECT 1 FROM public.comic_novel_channel_history o WHERE o.channel='facebook' AND o.target_key=h.target_key AND o.id<>h.id AND (o.status IN ('sending','unknown','claimed') OR (o.status='ok' AND (o.slot_week_start=h.slot_week_start OR date_trunc('week',o.confirmed_at AT TIME ZONE 'Asia/Seoul')::date=h.slot_week_start)))) THEN RETURN jsonb_build_object('status','hold','reason','target_blocked'); END IF;
 UPDATE public.comic_novel_channel_history SET status='sending',sending_at=now(),updated_at=now() WHERE id=h.id;
 RETURN jsonb_build_object('id',h.id,'status','sending');
END $$;

CREATE OR REPLACE FUNCTION public.novel_fb_finalize(p_claim_id bigint,p_post_id text,p_confirmed_at timestamptz,p_actor text DEFAULT '',p_note text DEFAULT '')
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE h public.comic_novel_channel_history%ROWTYPE; n public.comic_novels%ROWTYPE; v_date date; v_week date;
BEGIN
 IF coalesce(p_post_id,'') !~ '^[1-9][0-9]*(_[1-9][0-9]*)?$' OR p_confirmed_at IS NULL OR p_confirmed_at>now() THEN RAISE EXCEPTION 'invalid_publication_proof'; END IF;
 SELECT * INTO h FROM public.comic_novel_channel_history WHERE id=p_claim_id AND channel='facebook';
 IF NOT FOUND THEN RAISE EXCEPTION 'claim_missing'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('novel_fb:'||h.target_key,0));
 SELECT * INTO n FROM public.comic_novels WHERE publish_date=h.source_date FOR UPDATE;
 SELECT * INTO h FROM public.comic_novel_channel_history WHERE id=p_claim_id FOR UPDATE;
 IF h.status='ok' AND h.root_post_id=p_post_id THEN RETURN jsonb_build_object('id',h.id,'status','ok'); END IF;
 IF h.status NOT IN ('sending','unknown') THEN RAISE EXCEPTION 'invalid_finalize_transition'; END IF;
 IF h.status='unknown' AND (coalesce(p_actor,'')='' OR coalesce(p_note,'')='') THEN RAISE EXCEPTION 'reconcile_requires_actor_and_note'; END IF;
 v_date:=(p_confirmed_at AT TIME ZONE 'Asia/Seoul')::date; v_week:=date_trunc('week',v_date::timestamp)::date;
 IF n.publish_fb_yn='Y' OR EXISTS(SELECT 1 FROM public.comic_novel_channel_history WHERE channel='facebook' AND target_key=h.target_key AND status='ok' AND id<>h.id AND (root_post_id=p_post_id OR date_trunc('week',confirmed_at AT TIME ZONE 'Asia/Seoul')::date=v_week)) THEN RAISE EXCEPTION 'duplicate_publication'; END IF;
 UPDATE public.comic_novels SET publish_fb_data=v_date WHERE publish_date=h.source_date;
 UPDATE public.comic_novel_channel_history SET status='ok',confirmed_count=1,post_ids=jsonb_build_array(p_post_id),root_post_id=p_post_id,confirmed_at=p_confirmed_at,updated_at=now(),actor=p_actor,note=p_note,error_type=NULL WHERE id=h.id;
 RETURN jsonb_build_object('id',h.id,'status','ok','publish_fb_data',v_date);
END $$;

CREATE OR REPLACE FUNCTION public.novel_fb_mark_result(p_claim_id bigint,p_status text,p_error_type text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE h public.comic_novel_channel_history%ROWTYPE;
BEGIN
 IF p_status NOT IN ('failed','unknown') THEN RAISE EXCEPTION 'invalid_result_status'; END IF;
 SELECT * INTO h FROM public.comic_novel_channel_history WHERE id=p_claim_id AND channel='facebook' FOR UPDATE;
 IF NOT FOUND OR h.status<>'sending' THEN RAISE EXCEPTION 'invalid_result_transition'; END IF;
 UPDATE public.comic_novel_channel_history SET status=p_status,error_type=p_error_type,updated_at=now() WHERE id=h.id;
 RETURN jsonb_build_object('id',h.id,'status',p_status);
END $$;

CREATE OR REPLACE FUNCTION public.novel_fb_release(p_claim_id bigint,p_actor text,p_note text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE h public.comic_novel_channel_history%ROWTYPE;
BEGIN
 IF coalesce(trim(p_actor),'')='' OR coalesce(trim(p_note),'')='' THEN RAISE EXCEPTION 'release_requires_actor_and_evidence'; END IF;
 SELECT * INTO h FROM public.comic_novel_channel_history WHERE id=p_claim_id AND channel='facebook' FOR UPDATE;
 IF NOT FOUND OR h.status NOT IN ('claimed','sending','unknown') THEN RAISE EXCEPTION 'invalid_release_transition'; END IF;
 UPDATE public.comic_novel_channel_history SET status='failed',actor=p_actor,note=p_note,error_type='operator_verified_not_posted',updated_at=now() WHERE id=h.id;
 RETURN jsonb_build_object('id',h.id,'status','failed');
END $$;

CREATE OR REPLACE FUNCTION public.novel_x_confirm(p_source_date date,p_post_ids jsonb,p_expected_count int,p_source_hash text,p_cover_uri text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE n public.comic_novels%ROWTYPE; h public.comic_novel_channel_history%ROWTYPE; v_hash text;
BEGIN
 IF p_post_ids IS NULL OR jsonb_typeof(p_post_ids)<>'array' OR p_expected_count IS NULL OR p_expected_count<=0 THEN RAISE EXCEPTION 'invalid_x_proof'; END IF;
 IF jsonb_array_length(p_post_ids)<>p_expected_count OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_post_ids) v WHERE jsonb_typeof(v)<>'string' OR coalesce(v#>>'{}','') !~ '^[1-9][0-9]*$') OR (SELECT count(DISTINCT v) FROM jsonb_array_elements_text(p_post_ids) v)<>p_expected_count THEN RAISE EXCEPTION 'incomplete_x_proof'; END IF;
 SELECT * INTO n FROM public.comic_novels WHERE publish_date=p_source_date FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'source_missing'; END IF;
 v_hash:=encode(sha256(convert_to(coalesce(n.title,'')||E'\n'||coalesce(n.episode_range,'')||E'\n'||coalesce(n.novel_text,''),'UTF8')),'hex');
 IF v_hash IS DISTINCT FROM p_source_hash THEN RAISE EXCEPTION 'source_changed'; END IF;
 INSERT INTO public.comic_novel_channel_history(channel,source_date,target_key,slot_week_start,status,expected_count,confirmed_count,post_ids,root_post_id,content_hash,source_content_hash,cover_asset_uri,confirmed_at)
 VALUES('x',p_source_date,'x',date_trunc('week',p_source_date::timestamp)::date,'ok',p_expected_count,p_expected_count,p_post_ids,p_post_ids->>0,v_hash,v_hash,p_cover_uri,now())
 ON CONFLICT(channel,source_date,target_key) DO UPDATE SET status='ok',expected_count=EXCLUDED.expected_count,confirmed_count=EXCLUDED.confirmed_count,post_ids=EXCLUDED.post_ids,root_post_id=EXCLUDED.root_post_id,content_hash=EXCLUDED.content_hash,source_content_hash=EXCLUDED.source_content_hash,cover_asset_uri=EXCLUDED.cover_asset_uri,confirmed_at=now(),updated_at=now() RETURNING * INTO h;
 UPDATE public.comic_novels SET status='published' WHERE publish_date=p_source_date;
 RETURN jsonb_build_object('id',h.id,'status','ok');
END $$;

REVOKE ALL ON FUNCTION public.novel_fb_claim(date,text,date,text,text,text),public.novel_fb_mark_sending(bigint,text),public.novel_fb_finalize(bigint,text,timestamptz,text,text),public.novel_fb_mark_result(bigint,text,text),public.novel_fb_release(bigint,text,text),public.novel_x_confirm(date,jsonb,int,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.novel_fb_claim(date,text,date,text,text,text),public.novel_fb_mark_sending(bigint,text),public.novel_fb_finalize(bigint,text,timestamptz,text,text),public.novel_fb_mark_result(bigint,text,text),public.novel_fb_release(bigint,text,text),public.novel_x_confirm(date,jsonb,int,text,text) TO service_role;
COMMIT;
