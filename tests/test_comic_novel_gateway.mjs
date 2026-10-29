import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {verifyGithubToken,authorizeOperation} from '../supabase/functions/comic-novel-gateway/auth.mjs';
import {validateParams,executeOperation} from '../supabase/functions/comic-novel-gateway/operations.mjs';
const key=await webcrypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
const jwk={...await webcrypto.subtle.exportKey('jwk',key.publicKey),kid:'github-test',alg:'RS256',use:'sig'};
const now=1791840000;
const claims={iss:'https://token.actions.githubusercontent.com',aud:'comic-novel:ccomoimhhttaklfadaos',repository:'yumens2-byte/investment-os',repository_id:'1194564014',repository_owner_id:'221240519',ref:'refs/heads/main',sub:'repo:yumens2-byte/investment-os:ref:refs/heads/main',workflow_ref:'yumens2-byte/investment-os/.github/workflows/comic_novel_fb.yml@refs/heads/main',event_name:'workflow_dispatch',iat:now-10,nbf:now-10,exp:now+290};
const enc=x=>Buffer.from(typeof x==='string'?x:JSON.stringify(x)).toString('base64url');
async function token(c=claims,h={alg:'RS256',kid:jwk.kid}) {const payload=`${enc(h)}.${enc(c)}`;const sig=await webcrypto.subtle.sign('RSASSA-PKCS1-v1_5',key.privateKey,new TextEncoder().encode(payload));return `${payload}.${Buffer.from(sig).toString('base64url')}`;}
const verify=t=>verifyGithubToken(t,{now,cryptoProvider:webcrypto,fetchJwks:async()=>({keys:[jwk]})});
test('real RSA GitHub claims and immutable sub accepted',async()=>{
  assert.equal((await verify(await token())).workflow,'comic_novel_fb.yml');
  assert.equal((await verify(await token({...claims,sub:'repo:yumens2-byte@221240519/investment-os@1194564014:ref:refs/heads/main'}))).workflow,'comic_novel_fb.yml');
});
for(const [name,patch] of Object.entries({issuer:{iss:'https://attacker'},audience:{aud:'other'},array_audience:{aud:[claims.aud]},repository:{repository:'other/repo'},repo_id:{repository_id:'1'},owner_id:{repository_owner_id:'1'},branch:{ref:'refs/heads/feature'},subject:{sub:'repo:other/repo:ref:refs/heads/main'},fork_workflow:{workflow_ref:'other/repo/.github/workflows/comic_novel_fb.yml@refs/heads/main'},pull_request:{event_name:'pull_request'},expired:{exp:now},future:{nbf:now+31},stale:{iat:now-601},long_life:{exp:now+700},missing_time:{iat:undefined}})) {
  test(`reject signed ${name}`,async()=>{const t=await token({...claims,...patch});await assert.rejects(()=>verify(t));});
}
test('signed claim rejection matrix',async()=>{
  const patches=[{iss:'evil'},{aud:'wrong'},{repository:'other/repo'},{repository_id:'1'},{repository_owner_id:'1'},{ref:'refs/heads/dev'},{sub:'wrong'},{workflow_ref:'wrong'},{event_name:'pull_request'},{exp:now},{nbf:now+31},{iat:now-601},{exp:now+700}];
  for(const patch of patches){const t=await token({...claims,...patch});await assert.rejects(()=>verify(t));}
});
test('algorithm substitution, missing kid and forged signature rejected',async()=>{
  for(const h of [{alg:'none',kid:jwk.kid},{alg:'HS256',kid:jwk.kid},{alg:'RS256',kid:'other'},{alg:'RS256',kid:jwk.kid,crit:['b64']}]){const t=await token(claims,h);await assert.rejects(()=>verify(t));}
  const t=await token();const parts=t.split('.');parts[1]=enc({...claims,aud:'wrong'});await assert.rejects(()=>verify(parts.join('.')));
});
const wf=w=>`yumens2-byte/investment-os/.github/workflows/${w}@refs/heads/main`;
test('reusable FB job is identified by job_workflow_ref, not the caller',async()=>{
  const fbJob=await verify(await token({...claims,event_name:'schedule',workflow_ref:wf('comic_novel.yml'),job_workflow_ref:wf('comic_novel_fb.yml')}));
  assert.equal(fbJob.workflow,'comic_novel.yml');assert.equal(fbJob.jobWorkflow,'comic_novel_fb.yml');
  authorizeOperation(fbJob,'novel_fb_claim');
  assert.throws(()=>authorizeOperation(fbJob,'novel_x_confirm'));
  const xJob=await verify(await token({...claims,event_name:'schedule',workflow_ref:wf('comic_novel.yml'),job_workflow_ref:wf('comic_novel.yml')}));
  authorizeOperation(xJob,'novel_x_confirm');
  for(const op of ['novel_fb_claim','novel_fb_mark_sending','novel_fb_finalize','novel_fb_mark_result','novel_fb_release'])assert.throws(()=>authorizeOperation(xJob,op));
  for(const jobRef of ['other/repo/.github/workflows/comic_novel_fb.yml@refs/heads/main',wf('comic_novel_fb.yml').replace('main','feature'),wf('unknown.yml')]){
    const odd=await verify(await token({...claims,workflow_ref:wf('comic_novel.yml'),job_workflow_ref:jobRef}));
    assert.equal(odd.jobWorkflow,null);assert.throws(()=>authorizeOperation(odd,'novel_fb_claim'));
    authorizeOperation(odd,'novel_x_confirm'); // existing X job rule is unaffected by an unexpected job ref
  }
  const noJobRef=await verify(await token({...claims,workflow_ref:wf('comic_novel.yml')}));
  authorizeOperation(noJobRef,'novel_x_confirm');assert.throws(()=>authorizeOperation(noJobRef,'novel_fb_claim'));
  const ciCaller={workflow:'ci_alert_tests.yml',jobWorkflow:'comic_novel_fb.yml'};
  assert.throws(()=>authorizeOperation(ciCaller,'novel_fb_claim'));authorizeOperation(ciCaller,'select_oldest_pending');
});
test('CI cannot mutate; cross workflow writes denied',()=>{
  for(const w of ['ci_alert_tests.yml','comic_novel.yml'])assert.throws(()=>authorizeOperation({workflow:w},'novel_fb_claim'));
  assert.throws(()=>authorizeOperation({workflow:'comic_novel_fb.yml'},'novel_x_confirm'));
  assert.throws(()=>authorizeOperation({workflow:'comic_novel_fb.yml'},'arbitrary_rpc'));
  authorizeOperation({workflow:'ci_alert_tests.yml'},'verify_history_access');
});
test('RPC whitelist, extra parameters, invalid dates and future timestamps denied',()=>{
  assert.throws(()=>validateParams('drop_table',{}));assert.throws(()=>validateParams('verify_history_access',{table:'users'}));
  assert.throws(()=>validateParams('get_x_evidence',{source_date:'2026-02-30'}));
  assert.throws(()=>validateParams('novel_fb_finalize',{p_claim_id:1,p_post_id:'1',p_confirmed_at:'2099-01-01T00:00:00Z'}));
});
test('claim only current Sunday KST slot after firstdate',()=>{
  const p={p_source_date:'2026-04-05',p_target:'facebook:123',p_slot:'2026-10-26',p_run_id:'42',p_content_hash:'a'.repeat(64),p_source_hash:'b'.repeat(64)};
  validateParams('novel_fb_claim',p,new Date('2026-11-01T01:17:00Z'));
  validateParams('novel_fb_claim',p,new Date('2026-11-01T14:59:00Z')); // Sunday 23:59 KST
  assert.throws(()=>validateParams('novel_fb_claim',p,new Date('2026-11-01T15:00:00Z'))); // Monday KST
  assert.throws(()=>validateParams('novel_fb_claim',p,new Date('2026-10-30T13:17:00Z'))); // Friday KST
  assert.throws(()=>validateParams('novel_fb_claim',{...p,p_slot:'2026-10-19'},new Date('2026-10-25T01:17:00Z'))); // before first date
  assert.throws(()=>validateParams('novel_fb_claim',{...p,p_slot:'2026-10-19'},new Date('2026-11-01T01:17:00Z')));
});
test('oldest pending is selected by publish_fb_yn N in date then id order',async()=>{
  let seen;await executeOperation('select_oldest_pending',{today_kst:'2026-11-01'},async(path,params)=>{seen={path,params};return [];});
  assert.equal(seen.path,'comic_novels');assert.equal(seen.params.publish_fb_yn,'eq.N');
  assert.equal(seen.params.order,'publish_date.asc,id.asc');assert.equal(seen.params.status,'eq.published');
  assert.ok(!('publish_fb_data' in seen.params));
});
test('history access and legacy fallback contracts',async()=>{
  assert.equal(await executeOperation('verify_history_access',{},async()=>[]),true);
  const calls=[];const result=await executeOperation('get_x_evidence',{source_date:'2026-04-05'},async(path)=>{calls.push(path);return path==='comic_novel_legacy_x_confirmations'?[{source_date:'2026-04-05'}]:[];});
  assert.equal(result.verification_basis,'legacy_published_status');assert.equal(calls.length,2);
});
