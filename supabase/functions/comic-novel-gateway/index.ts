import {verifyGithubToken,authorizeOperation,JWKS_URL,GatewayError} from './auth.mjs';
import {validateParams,executeOperation} from './operations.mjs';
let cachedJwks: any = null;
let cachedAt = 0;
async function fetchJwks() {
  if(cachedJwks && Date.now()-cachedAt<60000)return cachedJwks;
  const response=await fetch(JWKS_URL,{redirect:'error',signal:AbortSignal.timeout(10000)});
  if(!response.ok)throw new GatewayError('OIDC_UNAVAILABLE',503);
  const keys=await response.json();if(!Array.isArray(keys.keys))throw new GatewayError('OIDC_UNAVAILABLE',503);
  cachedJwks=keys;cachedAt=Date.now();return keys;
}
const json=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
Deno.serve(async request=>{
  try {
    if(request.method!=='POST')return json({error:'METHOD_REJECTED'},405);
    const header=request.headers.get('authorization')??'';
    if(!header.startsWith('Bearer '))throw new GatewayError('OIDC_REQUIRED',401);
    const identity=await verifyGithubToken(header.slice(7),{fetchJwks});
    const raw=await request.text();if(raw.length>65536)throw new GatewayError('REQUEST_TOO_LARGE',413);
    let body:any;try{body=JSON.parse(raw);}catch{throw new GatewayError('INVALID_JSON',400);}
    if(!body||Object.keys(body).some(k=>!['operation','params'].includes(k))||typeof body.operation!=='string')throw new GatewayError('PARAMETERS_REJECTED',400);
    authorizeOperation(identity,body.operation);const p=validateParams(body.operation,body.params);
    const base=Deno.env.get('SUPABASE_URL');const key=Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
    if(base!=='https://ccomoimhhttaklfadaos.supabase.co'||!key)throw new GatewayError('SERVER_CONFIG',503);
    const rest=async(path:string,params:Record<string,string>={},method='GET',payload?:unknown)=>{
      const url=new URL(`${base}/rest/v1/${path}`);for(const [k,v]of Object.entries(params))url.searchParams.set(k,v);
      const r=await fetch(url,{method,redirect:'error',signal:AbortSignal.timeout(15000),headers:{apikey:key,Authorization:`Bearer ${key}`,'Content-Type':'application/json'},body:payload===undefined?undefined:JSON.stringify(payload)});
      if(!r.ok)throw new GatewayError('DATABASE_REJECTED',502);return r.json();
    };
    return json({data:await executeOperation(body.operation,p,rest)});
  }catch(e){const err=e instanceof GatewayError?e:new GatewayError('GATEWAY_FAILURE',500);return json({error:err.code},err.status);}
});
