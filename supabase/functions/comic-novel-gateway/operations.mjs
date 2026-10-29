import {GatewayError} from './auth.mjs';
const reject = () => { throw new GatewayError('PARAMETERS_REJECTED',400); };
const string = (v,max=1000) => typeof v === 'string' && v.length > 0 && v.length <= max;
const hash = v => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const date = v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && !isNaN(Date.parse(v)) && new Date(v).toISOString().slice(0,10) === v;
const id = v => Number.isSafeInteger(v) && v > 0;
export function validateParams(op,p,now=new Date()) {
  if (!p || typeof p !== 'object' || Array.isArray(p)) reject();
  const definitions = {
    verify_history_access:[[],{}],
    select_oldest_pending:[['today_kst'],{today_kst:date}],
    get_x_evidence:[['source_date'],{source_date:date}],
    get_week_state:[['target','slot'],{target:v=>string(v,100),slot:date}],
    novel_fb_claim:[['p_source_date','p_target','p_slot','p_run_id','p_content_hash','p_source_hash'],{p_source_date:date,p_target:v=>string(v,100),p_slot:date,p_run_id:v=>string(v,200),p_content_hash:hash,p_source_hash:hash}],
    novel_fb_mark_sending:[['p_claim_id','p_content_hash'],{p_claim_id:id,p_content_hash:hash}],
    novel_fb_finalize:[['p_claim_id','p_post_id','p_confirmed_at'],{p_claim_id:id,p_post_id:v=>typeof v==='string'&&/^[1-9]\d*(?:_[1-9]\d*)?$/.test(v),p_confirmed_at:v=>string(v,100)&&/(Z|[+-]\d{2}:\d{2})$/.test(v)&&!isNaN(Date.parse(v))&&Date.parse(v)<=now.getTime(),p_actor:v=>string(v,200),p_note:v=>string(v,4000)}],
    novel_fb_mark_result:[['p_claim_id','p_status','p_error_type'],{p_claim_id:id,p_status:v=>['failed','unknown'].includes(v),p_error_type:v=>typeof v==='string'&&v.length<=200}],
    novel_fb_release:[['p_claim_id','p_actor','p_note'],{p_claim_id:id,p_actor:v=>string(v,200),p_note:v=>string(v,4000)}],
    novel_x_confirm:[['p_source_date','p_post_ids','p_expected_count','p_source_hash'],{p_source_date:date,p_post_ids:v=>Array.isArray(v)&&v.length<=100&&v.every(x=>typeof x==='string'&&/^[1-9]\d*$/.test(x)),p_expected_count:v=>id(v)&&v<=100,p_source_hash:hash,p_cover_uri:v=>v===null||(string(v,2000)&&v.startsWith('storage://'))}]
  };
  const d=definitions[op]; if(!d||d[0].some(k=>!(k in p))||Object.keys(p).some(k=>!d[1][k]||!d[1][k](p[k]))) reject();
  if(op==='novel_x_confirm' && (p.p_post_ids.length!==p.p_expected_count||new Set(p.p_post_ids).size!==p.p_expected_count)) reject();
  if(op==='novel_fb_finalize' && (('p_actor'in p)!==('p_note'in p))) reject();
  if(op==='novel_fb_claim') {
    const kst=new Date(now.getTime()+9*3600000);const today=kst.toISOString().slice(0,10);
    const monday=new Date(kst);monday.setUTCDate(monday.getUTCDate()-((monday.getUTCDay()+6)%7));
    if(kst.getUTCDay()!==0||today<'2026-11-01'||p.p_source_date>today||p.p_slot!==monday.toISOString().slice(0,10)) reject();
  }
  return p;
}
export async function executeOperation(op,p,rest) {
  if(op.startsWith('novel_')) return rest(`rpc/${op}`,{},'POST',p);
  if(op==='verify_history_access') {await rest('comic_novel_channel_history',{select:'id',limit:'1'}); return true;}
  if(op==='select_oldest_pending') return (await rest('comic_novels',{select:'id,publish_date,title,episode_range,novel_text,status,publish_fb_data,publish_fb_yn',publish_fb_yn:'eq.N',status:'eq.published',publish_date:`lte.${p.today_kst}`,order:'publish_date.asc,id.asc',limit:'1'}))[0]??null;
  if(op==='get_x_evidence') {
    const rows=await rest('comic_novel_channel_history',{select:'*',channel:'eq.x',source_date:`eq.${p.source_date}`,status:'eq.ok',limit:'1'});
    if(rows.length)return rows[0];
    const legacy=await rest('comic_novel_legacy_x_confirmations',{select:'*',source_date:`eq.${p.source_date}`,limit:'1'});
    return legacy.length?{...legacy[0],status:'ok',verification_basis:'legacy_published_status'}:{};
  }
  if(op==='get_week_state') {
    const base={select:'*',channel:'eq.facebook',target_key:`eq.${p.target}`,limit:'1'};
    const unresolved=await rest('comic_novel_channel_history',{...base,status:'in.(sending,unknown,claimed)',order:'id.asc'});if(unresolved.length)return unresolved[0];
    const ok=await rest('comic_novel_channel_history',{...base,slot_week_start:`eq.${p.slot}`,status:'eq.ok'});if(ok.length)return ok[0];
    const end=new Date(p.slot);end.setUTCDate(end.getUTCDate()+7);
    const rows=await rest('comic_novels',{select:'publish_date,publish_fb_data',and:`(publish_fb_data.gte.${p.slot},publish_fb_data.lt.${end.toISOString().slice(0,10)})`,limit:'1'});
    return rows.length?{status:'ok',reason:'actual_week_done'}:{};
  }
  throw new GatewayError('OPERATION_REJECTED');
}
