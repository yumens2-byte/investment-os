-- One-time snapshot of existing published originals, explicitly confirmed by the user on 2026-10-12.
-- No X post IDs or Facebook success dates are manufactured.
INSERT INTO public.comic_novel_legacy_x_confirmations(source_date,source_content_hash,confirmed_by,note)
SELECT publish_date,encode(sha256(convert_to(coalesce(title,'')||E'\n'||coalesce(episode_range,'')||E'\n'||coalesce(novel_text,''),'UTF8')),'hex'),
'user-confirmed-2026-10-12','User confirms all retained X originals actually published; status=published is authoritative for this existing snapshot.'
FROM public.comic_novels WHERE status='published' AND publish_date<='2026-10-12'::date
ON CONFLICT(source_date) DO NOTHING;
