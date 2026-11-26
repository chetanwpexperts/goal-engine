"use strict";
const {invoke}=window.__TAURI__.core;
const {listen}=window.__TAURI__.event;
const el=id=>document.getElementById(id);
const status=text=>{el("status").textContent=text;};
let pending=null;
function button(label,action){const b=document.createElement("button");b.textContent=label;b.onclick=async()=>{b.disabled=true;try{await action();}catch(e){status(String(e));}finally{b.disabled=false;}};return b;}
async function reload(){
 const rows=await invoke("list");el("connections").replaceChildren();
 for(const [row,live] of rows){
  const a=document.createElement("article"),h=document.createElement("strong"),p=document.createElement("p");
  h.textContent=row.label;p.textContent=(row.ssh?"SSH · "+row.ssh.host:"This computer")+" · "+(live?"Connected":"Disconnected");
  a.append(h,p,button("Open",()=>open(row.id)),button("Disconnect",async()=>{await invoke("disconnect",{id:row.id});await reload();}),button("Remove",async()=>{if(window.confirm("Remove this connection and its API key?")){await invoke("remove",{id:row.id});await reload();}}));
  el("connections").append(a);
 }
}
async function open(id){
 status("Checking connection…");
 const fingerprint=await invoke("fingerprint",{id});
 if(fingerprint){pending={id,fingerprint};el("fp").textContent=fingerprint;el("verify").showModal();status("Verify the server fingerprint.");return;}
 status("Opening connection…");await invoke("connect",{id});status("Connection opened.");await reload();
}
el("trust").onclick=async()=>{const p=pending;pending=null;el("verify").close();if(p)try{await invoke("accept",p);await open(p.id);}catch(e){status(String(e));}};
el("cancel").onclick=()=>{pending=null;el("verify").close();status("Connection cancelled.");};
el("verify").addEventListener("cancel",()=>{pending=null;});
el("kind").onchange=()=>{const ssh=el("kind").value==="ssh";el("ssh").hidden=!ssh;for(const id of ["host","user","keypath"])el(id).required=ssh;};
el("add").onsubmit=async e=>{e.preventDefault();const key=el("key").value;el("key").value="";try{await invoke("add",{label:el("label").value.trim(),port:Number(el("port").value),ssh:el("kind").value==="ssh"?{host:el("host").value.trim(),user:el("user").value.trim(),key_path:el("keypath").value}:null,key});el("add").reset();el("kind").onchange();status("Connection saved.");await reload();}catch(e){status(String(e));}};
listen("connections-changed",()=>reload().catch(e=>status(String(e))));
listen("connection-error",e=>status(String(e.payload)));
listen("verify-host",e=>open(e.payload).catch(err=>status(String(err))));
reload().catch(e=>status(String(e)));
