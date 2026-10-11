// Optional isolated SQL regression harness; npm install @electric-sql/pglite@0.5.8.
// PGLITE_MODULE may point to an already-installed module. No production connection.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {PGlite}=await import(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const db=new PGlite();
let checks=0;
const check=(value)=>{assert.ok(value);checks++;};
const row=async(sql,params=[]) => (await db.query(sql,params)).rows[0];
const rpc=async(name,args)=>{
 const placeholders=args.map((_,i)=>'$'+(i+1)).join(',');
 const value=await row(`select public.${name}(${placeholders}) as result`,args);
 return value.result;
};
try {
 await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
 create table public.comic_novels(id serial primary key,publish_date date not null unique,
 title text not null,episode_range text not null,novel_text text not null,status varchar(30));`);
 const schema=await fs.readFile(fileURLToPath(new URL('../../db/comic_novel_fb_schema.sql',import.meta.url)),'utf8');
 await db.exec(schema);
 await db.exec(schema); // Deploy script must be safe to reapply.
 check((await row(`select data_type from information_schema.columns where table_name='comic_novels' and column_name='publish_fb_data'`)).data_type==='date');
 await db.exec(`insert into comic_novels(publish_date,title,episode_range,novel_text,status) values
 ('2026-04-05','First','EP.01~04','Body A','published'),
 ('2026-04-12','Second','EP.05~09','Body B','published');`);
 const sourceHash=async(date)=>(await row(`select encode(sha256(convert_to(title||E'\\n'||episode_range||E'\\n'||novel_text,'UTF8')),'hex') as h from comic_novels where publish_date=$1`,[date])).h;
 const h1=await sourceHash('2026-04-05'),h2=await sourceHash('2026-04-12');
 check(h1===createHash('sha256').update('First\nEP.01~04\nBody A').digest('hex'));
 // Legacy published is not enough. No external side effect exists in this harness.
 const noProof=await rpc('novel_fb_claim',['2026-04-05','123','2026-10-05','run-no-proof','fbhash',h1]);
 check(noProof.status!=='claimed');
 check((await rpc('novel_x_confirm',['2026-04-05',['100','101'],2,h1,null])).status==='ok');
 check((await rpc('novel_x_confirm',['2026-04-12',['102','103'],2,h2,null])).status==='ok');
 await db.exec("insert into comic_novels(publish_date,title,episode_range,novel_text,status) values('2026-04-01','Unconfirmed head','EP.0','body','published')");
 const cannotOvertake=await rpc('novel_fb_claim',['2026-04-05','123','2026-10-05','run-head','fbhash',h1]);
 check(cannotOvertake.status!=='claimed');
 await db.exec("delete from comic_novels where publish_date='2026-04-01'");
 const claim=await rpc('novel_fb_claim',['2026-04-05','123','2026-10-05','run-1','fbhash',h1]);
 check(claim.status==='claimed');
 let slotUnique=false;
 try {await db.exec("insert into comic_novel_channel_history(channel,source_date,target_key,slot_week_start,status,content_hash,source_content_hash) values('facebook','2026-04-12','123','2026-10-05','claimed','h','h')");} catch {slotUnique=true;}
 check(slotUnique);
 const competing=await rpc('novel_fb_claim',['2026-04-05','123','2026-10-05','run-2','fbhash',h1]);
 check(competing.status!=='claimed');
 check((await rpc('novel_fb_mark_sending',[claim.id,'fbhash'])).status==='sending');
 check((await rpc('novel_fb_mark_result',[claim.id,'unknown','timeout'])).status==='unknown');
 const laterBlocked=await rpc('novel_fb_claim',['2026-04-12','123','2026-10-12','run-3','hash2',h2]);
 check(laterBlocked.status!=='claimed');
 let futureRejected=false;
 try {await rpc('novel_fb_finalize',[claim.id,'123_456','2099-10-16T22:18:00+09:00','operator','future invalid']);} catch {futureRejected=true;}
 check(futureRejected);
 check((await rpc('novel_fb_finalize',[claim.id,'123_456','2026-10-09T22:18:00+09:00','operator','Page checked'])).status==='ok');
 check((await row('select publish_fb_data::text as d from comic_novels where publish_date=$1',['2026-04-05'])).d==='2026-10-09');
 const weekBlocked=await rpc('novel_fb_claim',['2026-04-12','123','2026-10-05','run-4','hash2',h2]);
 check(weekBlocked.status!=='claimed');
 const next=await rpc('novel_fb_claim',['2026-04-12','123','2026-10-12','run-5','hash2',h2]);
 check(next.status==='claimed');
 check((await rpc('novel_fb_release',[next.id,'operator','Verified no post before retry'])).status==='failed');
 const retry=await rpc('novel_fb_claim',['2026-04-12','123','2026-10-12','run-6','hash2',h2]);
 check(retry.status==='claimed');
 await db.exec(`update comic_novels set novel_text='Changed' where publish_date='2026-04-12'`);
 let changedBlocked=false;
 try {const sent=await rpc('novel_fb_mark_sending',[retry.id,'hash2']);changedBlocked=sent.status!=='sending';} catch {changedBlocked=true;}
 check(changedBlocked);
 check((await row('select publish_fb_data from comic_novels where publish_date=$1',['2026-04-12'])).publish_fb_data===null);
 // Public clients must not be allowed to execute writing RPCs.
 check((await row(`select has_function_privilege('anon','public.novel_fb_claim(date,text,date,text,text,text)','execute') as allowed`)).allowed===false);
 console.log(JSON.stringify({sql_checks:checks,status:'PASS',engine:(await row('select version() as v')).v}));
} finally {await db.close();}
