"use strict";
(() => {
 const invoke = window.__TAURI__.core.invoke;
 const el = id => document.getElementById(id);
 const root = el("trial-content");
 const status = text => { el("status").textContent = text; };
 const call = (action,input=null) => invoke("trial_call",{action,input});
 let timer=null, active=false, credentials=null, chatStarted=false;
 function node(tag,text) { const n=document.createElement(tag); if(text!==undefined)n.textContent=String(text); return n; }
 function action(label,fn) { const b=node("button",label);b.type="button";b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){status(String(e));}finally{b.disabled=false;}};return b; }
 function input(form,label,name,type,max,value="") {
  const l=node("label",label), i=node("input");i.name=name;i.type=type;i.maxLength=max;i.value=value;i.required=name!=="password";if(type==="password")i.autocomplete="off";
  l.append(i);form.append(l);return i;
 }
 function screen(title) { root.replaceChildren(node("h2",title)); }
 function choice(panel) { for(const id of ["start","trial","server-panel"])el(id).hidden=id!==panel; }
 function reset() { active=false;clearInterval(timer);timer=null;credentials=null;chatStarted=false;root.replaceChildren(); }
 el("server").onclick=()=>choice("server-panel");
 el("server-back").onclick=()=>choice("start");
 el("trial-back").onclick=async()=>{reset();try{await invoke("trial_stop");}catch(e){status(String(e));}choice("start");};
 el("try").onclick=async()=>{
  el("try").disabled=true;el("trial-back").disabled=true;choice("trial");screen("Starting DBaba on this computer…");
  try{const result=await invoke("trial_start");active=true;status("");if(result.setup_done)await dashboard();else await detect();}
  catch(e){status(String(e));screen("Could not start local DBaba");}
  finally{el("try").disabled=false;el("trial-back").disabled=false;}
 };
 async function detect(){
  screen("1 of 3 — Find your database");
  root.append(node("p","Choose a detected database or enter its details yourself."));
  const found=await call("detect");
  for(const db of found.found||[])root.append(action(db.engine+" · "+db.host+":"+db.port+" · "+db.how,()=>connection(db)));
  root.append(action("Enter database details manually",()=>connection({engine:"mysql",host:"127.0.0.1",port:3306})));
 }
 function connection(db){
  credentials=null;screen("2 of 3 — Connect to your database");
  const form=node("form"), label=node("label","Database engine"), engine=node("select");engine.name="engine";
  for(const value of ["mysql","mariadb","postgresql"]){const o=node("option",value);o.value=value;engine.append(o);}
  engine.value=db.engine;label.append(engine);form.append(label);
  const host=input(form,"Database host","host","text",253,db.host);
  const port=input(form,"Database port","port","number",5,String(db.port));port.min="1";port.max="65535";
  const name=input(form,"Database name","database","text",255);
  const user=input(form,"Database user","user","text",255);
  const password=input(form,"Database password","password","password",4096);
  const tunnelLabel=node("label","Connect through SSH (database runs on the SSH server)");
  const tunnel=node("input");tunnel.type="checkbox";tunnelLabel.append(tunnel);form.append(tunnelLabel);
  const sshFields=node("fieldset");sshFields.hidden=true;form.append(sshFields);
  const sshHost=input(sshFields,"SSH host","ssh_host","text",253);
  const sshUser=input(sshFields,"SSH user","ssh_user","text",253);
  const sshKey=input(sshFields,"Absolute key file path (blank uses SSH agent)","ssh_key","text",4096);
  for(const i of [sshHost,sshUser,sshKey])i.required=false;
  tunnel.onchange=()=>{sshFields.hidden=!tunnel.checked;sshHost.required=tunnel.checked;sshUser.required=tunnel.checked;};
  const submit=node("button","Test connection");submit.type="submit";form.append(submit);
  form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;
   const data={engine:engine.value,host:host.value.trim(),port:Number(port.value),database:name.value,user:user.value,password:password.value};
   try{
    if(tunnel.checked){
     const ssh={host:sshHost.value.trim(),user:sshUser.value.trim(),key_path:sshKey.value,port:data.port};
     const cmd=(action,input)=>invoke("trial_ssh",{action,input});
     const check=await cmd("fingerprint",ssh);
     if(check.fingerprint){
      if(!window.confirm("Verify this fingerprint with your server administrator before accepting:\n"+check.fingerprint)){status("SSH connection cancelled.");return;}
      await cmd("accept",{...ssh,fingerprint:check.fingerprint});
     }
     const forwarded=await cmd("open",ssh);data.host=forwarded.host;data.port=forwarded.port;
    }else await invoke("trial_ssh",{action:"close",input:{}});
    const result=await call("test",data);if(!result.ok){status(result.reason||"Connection failed.");return;}credentials=data;password.value="";status(result.warning||"Connection works.");saveScreen();}
   catch(err){status(String(err));}finally{submit.disabled=false;}
  };
  root.append(form,action("Back to detection",detect));
 }
 function saveScreen(){
  screen("3 of 3 — Protect your DBaba screen");
  const form=node("form"),pass=input(form,"Screen password","screen_password","password",4096);
  pass.minLength=8;
  const submit=node("button","Save and open DBaba");submit.type="submit";form.append(submit);
  form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;try{
   const result=await call("save",{...credentials,screen_password:pass.value});if(!result.ok){status(result.reason||"Setup not saved.");return;}
   credentials=null;pass.value="";status(result.warning||"");await dashboard();
  }catch(err){status(String(err));}finally{submit.disabled=false;}};
  root.append(form,action("Back to connection",()=>connection(credentials)));
 }
 async function dashboard(){
  screen("Your database");
  const badge=node("p","Alerts: checking…"),nav=node("nav"),panel=node("section");
  root.append(badge,nav,panel);
  let tab="chat", messages=[];
  async function send(text){
   if(!chatStarted){await call("new_chat");chatStarted=true;}
   const reply=await call("message",{text});messages.push(["You",text],["DBaba",reply.reply]);renderChat();
  }
  function renderChat(){
   tab="chat";panel.replaceChildren(node("h3","Chat"));
   for(const [who,text] of messages)panel.append(node("p",who+": "+text));
   const form=node("form"),question=input(form,"Ask a database question","question","text",10000),submit=node("button","Send");submit.type="submit";form.append(submit);
   form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;try{await send(question.value);}catch(err){status(String(err));}finally{submit.disabled=false;}};
   panel.append(form,action("New chat",async()=>{await call("new_chat");chatStarted=true;messages=[];renderChat();}));
  }
  async function report(){
   tab="report";panel.replaceChildren(node("h3","Report"),action("Start scan",async()=>{const r=await call("scan");status(r.started?"Scan started. Refresh when it finishes.":"Scan not started.");}),action("Refresh report",report),action("Save HTML report",async()=>{const r=await call("report_html");status(r.saved?"HTML report saved.":"Save cancelled.");}));
   const data=await call("report");if(tab!=="report")return;
   panel.append(node("p",data.at==null||data.score==null?"No scan yet":"Score: "+data.score+"/100"));if(data.pending)panel.append(node("p","Scan in progress."));
   const rank={critical:0,warning:1,info:2};
   const items=(data.sections||[]).flatMap(s=>(s.items||[]).map(i=>({...i,section:s.title}))).sort((a,b)=>(rank[a.severity]??3)-(rank[b.severity]??3));
   for(const item of items){const a=node("article");a.append(node("strong",item.severity+": "+item.title),node("p",item.section+" — "+item.what),node("p","Why: "+item.why));
    if(item.fix)a.append(node("pre",item.fix.sql),node("p",item.fix.needs_approval?"Requires approval. SQL not executed.":"Suggested SQL; not executed."));
    a.append(action("Ask about this",()=>send(item.title)));panel.append(a);}
  }
  async function jobs(){
   tab="jobs";panel.replaceChildren(node("h3","Automation"));const data=await call("jobs");if(tab!=="jobs")return;
   for(const job of data.jobs||[]){const form=node("form"),on=node("input");on.type="checkbox";on.checked=job.enabled;const l=node("label","Enabled");l.append(on);
    form.append(node("strong",job.name+" · "+job.every),l);const at=input(form,"Run time","at","time",5,job.at||"");at.required=false;at.disabled=job.every==="5min";
    form.append(node("p",job.last_run?("Last run: "+job.last_run.at+" — "+(job.last_run.ok?"OK":"Failed")+" — "+job.last_run.text):"Not run yet"));
    form.append(action("Save",async()=>{await call("job",{id:job.id,enabled:on.checked,at:at.value||null});status("Job saved.");await jobs();}));panel.append(form);}
  }
  async function alerts(){
   tab="alerts";panel.replaceChildren(node("h3","Alerts"));const state=await call("state");if(tab!=="alerts")return;
   for(const alert of state.alerts||[]){const a=node("article");a.append(node("strong",alert.level+": "+alert.title),node("p",alert.text),node("p",alert.at));if(alert.acknowledged)a.append(node("p","Acknowledged"));else a.append(action("Acknowledge",async()=>{await call("ack",{id:alert.id});await poll();await alerts();}));panel.append(a);}
  }
  async function poll(){try{const state=await call("state");if(active)badge.textContent="Alerts: "+(state.alerts||[]).filter(a=>!a.acknowledged).length;}catch(e){if(active)badge.textContent="Alerts: unavailable";}}
  nav.append(action("Chat",renderChat),action("Report",report),action("Automation",jobs),action("Alerts",alerts));renderChat();await poll();clearInterval(timer);timer=setInterval(poll,60000);
 }
})();
