'use strict';
const names={mysql:'MySQL',mariadb:'MariaDB',postgresql:'PostgreSQL'};
function baseURL(value,allowRemote){
 const u=new URL(value);
 if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.pathname!=='/'||u.search||u.hash||/[\s\\]/.test(value))throw new Error('Use an HTTP or HTTPS service URL without credentials or a path.');
 if(!['127.0.0.1','localhost','[::1]'].includes(u.hostname)){
 if(!allowRemote)throw new Error('Remote DBaba URLs require dbaba.allowRemote.');
 if(u.protocol!=='https:')throw new Error('Remote DBaba URLs require HTTPS.');
 }
 return u.origin;
}
async function ask(config,text,fetcher=globalThis.fetch){
 const base=baseURL(config.url,config.allowRemote),cookies=new Map();
 if(!config.key)throw new Error('Use DBaba: Set API Key first.');
 if(!text.trim()||text.trim().length>10000)throw new Error('Enter between 1 and 10,000 characters.');
 async function post(path,body){
 const headers={Authorization:'Bearer '+config.key};
 if(cookies.size)headers.Cookie=Array.from(cookies,([k,v])=>k+'='+v).join('; ');
 if(body!==undefined)headers['Content-Type']='application/json';
 const res=await fetcher(base+path,{method:'POST',headers,body:body===undefined?undefined:JSON.stringify(body),redirect:'error',signal:AbortSignal.timeout(120000)});
 if(!res.ok)throw new Error('DBaba returned HTTP '+res.status+'.');
 for(const cookie of res.headers.getSetCookie()){const pair=cookie.split(';',1)[0],i=pair.indexOf('=');if(i<0)continue;const name=pair.slice(0,i);if(/;\s*max-age=0(?:;|$)/i.test(cookie))cookies.delete(name);else cookies.set(name,pair.slice(i+1));}
 return res.json();
 }
 await post('/api/new-chat');
 const result=await post('/api/message',{text});
 if(typeof result.reply!=='string')throw new Error('DBaba returned an invalid reply.');
 return result.reply;
}
async function check(config,query,fetcher){return ask(config,(names[config.database]||names.mysql)+': '+query.sql,fetcher);}
module.exports={ask,check,baseURL};
