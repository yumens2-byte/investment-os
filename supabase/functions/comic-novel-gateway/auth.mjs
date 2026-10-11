const ISSUER = 'https://token.actions.githubusercontent.com';
const AUDIENCE = 'comic-novel:ccomoimhhttaklfadaos';
const REPO = 'yumens2-byte/investment-os';
const REF = 'refs/heads/main';
const SUBS = [`repo:${REPO}:ref:${REF}`, 'repo:yumens2-byte@221240519/investment-os@1194564014:ref:refs/heads/main'];
export const JWKS_URL = `${ISSUER}/.well-known/jwks`;
export class GatewayError extends Error {
  constructor(code, status = 403) { super(code); this.code = code; this.status = status; }
}
const fail = () => { throw new GatewayError('OIDC_REJECTED', 401); };
function decode(s) {
  if (!/^[A-Za-z0-9_-]+$/.test(s)) fail();
  const raw = atob(s.replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}
export async function verifyGithubToken(token, {fetchJwks, cryptoProvider = globalThis.crypto, now = Math.floor(Date.now()/1000)} = {}) {
  try {
    if (typeof token !== 'string' || token.length > 20000) fail();
    const parts = token.split('.'); if (parts.length !== 3) fail();
    const header = JSON.parse(new TextDecoder().decode(decode(parts[0])));
    if (header.alg !== 'RS256' || typeof header.kid !== 'string' || header.kid.length > 200 || header.crit) fail();
    // Ignore all caller-provided key URLs; only the canonical GitHub JWKS is used.
    const jwks = await fetchJwks();
    const keys = jwks.keys.filter(k => k.kid === header.kid && k.kty === 'RSA' && (!k.alg || k.alg === 'RS256') && (!k.use || k.use === 'sig'));
    if (keys.length !== 1) fail();
    const key = await cryptoProvider.subtle.importKey('jwk', keys[0], {name:'RSASSA-PKCS1-v1_5', hash:'SHA-256'}, false, ['verify']);
    const valid = await cryptoProvider.subtle.verify('RSASSA-PKCS1-v1_5', key, decode(parts[2]), new TextEncoder().encode(parts.slice(0,2).join('.')));
    if (!valid) fail();
    const c = JSON.parse(new TextDecoder().decode(decode(parts[1])));
    if (c.iss !== ISSUER || c.aud !== AUDIENCE || c.repository !== REPO || c.repository_id !== '1194564014' || c.repository_owner_id !== '221240519' || c.ref !== REF || !SUBS.includes(c.sub)) fail();
    if (![c.exp,c.iat,c.nbf].every(Number.isInteger) || c.exp <= now || c.nbf > now + 30 || c.iat > now + 30 || now-c.iat > 600 || c.exp-c.iat > 600 || c.exp <= c.iat) fail();
    const allowed = ['comic_novel.yml','comic_novel_fb.yml','ci_alert_tests.yml'];
    const workflow = allowed.find(w => c.workflow_ref === `${REPO}/.github/workflows/${w}@${REF}`);
    if (!workflow || !['schedule','workflow_dispatch','push'].includes(c.event_name)) fail();
    if (workflow === 'ci_alert_tests.yml' ? !['push','workflow_dispatch'].includes(c.event_name) : !['schedule','workflow_dispatch'].includes(c.event_name)) fail();
    return {workflow, claims:c};
  } catch (e) { if (e instanceof GatewayError) throw e; fail(); }
}
export function authorizeOperation(identity, operation) {
  const reads = ['select_oldest_pending','get_x_evidence','get_week_state','verify_history_access'];
  if (reads.includes(operation)) return;
  if (operation === 'novel_x_confirm' && identity.workflow === 'comic_novel.yml') return;
  if (['novel_fb_claim','novel_fb_mark_sending','novel_fb_finalize','novel_fb_mark_result','novel_fb_release'].includes(operation) && identity.workflow === 'comic_novel_fb.yml') return;
  throw new GatewayError('OPERATION_REJECTED');
}
