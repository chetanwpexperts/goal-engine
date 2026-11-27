"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const {readFileSync}=require("node:fs");
const {join}=require("node:path");
const vm=require("node:vm");
const http=require("node:http");
const source=readFileSync(join(__dirname,"../../desktop/ui/trial.js"),"utf8");
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.value="";this.textContent="";this.hidden=false;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
}
function descendants(n){return [n,...n.children.flatMap(descendants)];}
function byText(n,text){const b=descendants(n).find(x=>x.textContent===text);assert.ok(b,text);return b;}
function field(n,name){const i=descendants(n).find(x=>x.name===name);assert.ok(i,name);return i;}
async function fixture(t){
 const calls=[];let done=false, acknowledged=false;
 const server=http.createServer(async(req,res)=>{
  const chunks=[];for await(const c of req)chunks.push(c);
  const body=chunks.length?JSON.parse(Buffer.concat(chunks)):null;
  calls.push([req.url,body]);
  const setup=req.url.startsWith("/api/setup/");
  if(req.url!=="/api/health" && (setup?req.headers["x-dbaba-setup"]!=="fixture-token":req.headers.authorization!=="Bearer fixture-key")){res.writeHead(401).end();return;}
  if(setup&&done){res.writeHead(409).end();return;}
  let result;
  switch(req.url){
   case "/api/health":result={ok:true,version:"1.0.0"};break;
   case "/api/setup/detect":result={found:[{engine:"mysql",host:"127.0.0.1",port:3306,how:"Detected locally"}]};break;
   case "/api/setup/test":result={ok:body.password==="fixture-password",version:"8.0",reason:"Connection failed"};break;
   case "/api/setup/save":done=true;result={ok:true,api_key:"fixture-key"};break;
   case "/api/state":result={setup_done:done,alerts:[{id:"a",level:"warning",title:"Example alert",text:"Check indexes",at:"2026-01-01T00:00:00Z",acknowledged},{id:"b",level:"info",title:"Older alert",text:"Example",at:"2026-01-01T00:00:00Z",acknowledged:true}]};break;
   case "/api/alerts/a/ack":acknowledged=true;result={ok:true};break;
   case "/api/report.html":res.setHeader("Content-Type","text/html");res.end("<html>Example report</html>");return;
   case "/api/new-chat":result={ok:true};break;
   case "/api/message":result={reply:"Example answer"};break;
   case "/api/report":result={score:70,sections:[{title:"Indexes",items:[{severity:"info",title:"Info",what:"Example",why:"Example",fix:null},{severity:"critical",title:"Missing index",what:"Scan",why:"Cost",fix:{sql:"CREATE INDEX example ON sample(id)",needs_approval:true}}]}]};break;
   case "/api/report/scan":result={started:true};break;
   case "/api/jobs":result={jobs:[{id:"scan",name:"Scan",every:"day",enabled:true,at:"08:00",last_run:null}]};break;
   case "/api/jobs/scan":result={id:"scan",...body};break;
   default:res.writeHead(404).end();return;
  }
  res.setHeader("Content-Type","application/json");res.end(JSON.stringify(result));
 });
 await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
 t.after(()=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections();}));
 const base="http://127.0.0.1:"+server.address().port;
 const elements=Object.fromEntries(["trial-content","status","start","trial","server-panel","server","server-back","trial-back","try"].map(id=>[id,new Element("div")]));
 let stopped=false,poll=null,key=null;
 async function request(path,body,setup=false,method="GET"){
  const headers={"Content-Type":"application/json"};
  if(setup)headers["X-DBaba-Setup"]="fixture-token";else if(key)headers.Authorization="Bearer "+key;
  const res=await fetch(base+path,{method,headers,body:body===null?undefined:JSON.stringify(body),redirect:"error"});
  assert.ok(res.ok,"mock HTTP "+res.status);return path==="/api/report.html"?res.text():res.json();
 }
 const paths={detect:"/api/setup/detect",test:"/api/setup/test",save:"/api/setup/save",state:"/api/state",report:"/api/report",scan:"/api/report/scan",jobs:"/api/jobs",new_chat:"/api/new-chat",message:"/api/message"};
 async function invoke(command,args){
  if(command==="trial_start"){await request("/api/health",null);return {setup_done:done};}
  if(command==="trial_stop"){stopped=true;return;}
  assert.equal(command,"trial_call");
  const {action,input}=args,setup=["detect","test","save"].includes(action);
  const path=action==="ack"?"/api/alerts/"+input.id+"/ack":action==="report_html"?"/api/report.html":action==="job"?"/api/jobs/"+input.id:paths[action];
  const method=["state","report","jobs","report_html"].includes(action)?"GET":"POST";
  const body=action==="ack"?null:action==="job"?{enabled:input.enabled,at:input.at}:input;
  const result=await request(path,body,setup,method);
  if(action==="save"){key=result.api_key;return {ok:result.ok};}
  if(action==="report_html"){assert.equal(result,"<html>Example report</html>");return {saved:true};}
  return result;
 }
 vm.runInNewContext(source,{window:{__TAURI__:{core:{invoke}}},document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag)},setInterval:(fn,ms)=>{assert.equal(ms,60000);poll=fn;return 1;},clearInterval:()=>{}});
 return {elements,calls,root:elements["trial-content"],poll:()=>poll(),stopped:()=>stopped};
}
test("desktop wizard, tabs, alerts polling and stop use mock contract",async t=>{
 const f=await fixture(t),{elements,root,calls}=f;
 elements.server.onclick();assert.equal(elements["server-panel"].hidden,false);
 elements["server-back"].onclick();assert.equal(elements.start.hidden,false);
 await elements.try.onclick();assert.equal(elements.trial.hidden,false);
 await byText(root,"mysql · 127.0.0.1:3306 · Detected locally").onclick();
 field(root,"database").value="sample";field(root,"user").value="sample";field(root,"password").value="incorrect";
 let form=descendants(root).find(x=>x.tagName==="form");
 await form.onsubmit({preventDefault(){}});assert.equal(elements.status.textContent,"Connection failed");
 field(root,"password").value="fixture-password";await form.onsubmit({preventDefault(){}});
 field(root,"screen_password").value="fixture-screen";
 form=descendants(root).find(x=>x.tagName==="form");await form.onsubmit({preventDefault(){}});
 byText(root,"Alerts: 1");await f.poll();
 await byText(root,"Alerts").onclick();byText(root,"2026-01-01T00:00:00Z");
 await byText(root,"Acknowledge").onclick();byText(root,"Alerts: 0");
 await byText(root,"Report").onclick();
 const titles=descendants(root).filter(x=>x.tagName==="strong").map(x=>x.textContent);
 assert.deepEqual(titles,["critical: Missing index","info: Info"]);
 await byText(root,"Start scan").onclick();
 await byText(root,"Save HTML report").onclick();assert.equal(elements.status.textContent,"HTML report saved.");
 await byText(root,"Ask about this").onclick();byText(root,"DBaba: Example answer");
 assert.ok(calls.some(([p,b])=>p==="/api/message"&&b.text==="Missing index"));
 await byText(root,"Automation").onclick();field(root,"at").value="09:30";await byText(root,"Save").onclick();
 assert.ok(calls.some(([p,b])=>p==="/api/jobs/scan"&&b.at==="09:30"));
 await elements["trial-back"].onclick();assert.ok(f.stopped());
 assert.equal(elements.start.hidden,false);
});
