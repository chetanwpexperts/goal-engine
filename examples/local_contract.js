// Node 22+, local S4 examples: node examples/local_contract.js GROUP
const fs=require("node:fs");
const base=(process.env.DBABA_URL||"http://127.0.0.1:8765").replace(/\/$/,"");
if(!/^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(base)||Number(base.split(":").pop())<1||Number(base.split(":").pop())>65535)throw Error("Use a loopback URL");
let cookie="";
async function call(path,method="GET",body,setup=false,html=false){
 const headers={"Content-Type":"application/json"};
 if(setup)headers["X-DBaba-Setup"]=process.env.DBABA_SETUP_TOKEN||"";
 else if(process.env.DBABA_API_KEY)headers.Authorization="Bearer "+process.env.DBABA_API_KEY;
 if(cookie)headers.Cookie=cookie;
 const res=await fetch(base+path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),redirect:"error",signal:AbortSignal.timeout(30000)});
 if(!res.ok)throw Error("HTTP "+res.status);
 const c=res.headers.get("set-cookie");if(c)cookie=c.split(";")[0];
 return html?Buffer.from(await res.arrayBuffer()):res.json();
}
function id(name){const v=process.env[name]||"";if(!/^[A-Za-z0-9_-]{1,128}$/.test(v))throw Error("Invalid ID");return v;}
function save(path,data){fs.writeFileSync(path,data,{flag:"wx",mode:0o600});}
async function main(){
 switch(process.argv[2]){
 case "health":console.log(await call("/api/health"));break;
 case "state":console.log(await call("/api/state"));break;
 case "chat":await call("/api/new-chat","POST");console.log((await call("/api/message","POST",{text:"How do indexes work?"})).reply);break;
 case "alerts":console.log(await call("/api/alerts/"+id("DBABA_ALERT_ID")+"/ack","POST"));break;
 case "report":console.log(await call("/api/report"));console.log(await call("/api/report/scan","POST"));save("report.html",await call("/api/report.html","GET",undefined,false,true));break;
 case "jobs":console.log(await call("/api/jobs"));console.log(await call("/api/jobs/"+id("DBABA_JOB_ID"),"POST",{enabled:true,at:"09:30"}));break;
 case "setup":{
 const body=JSON.parse(fs.readFileSync(0,"utf8"));const {screen_password,...test}=body;
 console.log(await call("/api/setup/detect","POST",undefined,true));
 if(!(await call("/api/setup/test","POST",test,true)).ok)throw Error("Database test failed");
 const result=await call("/api/setup/save","POST",body,true);if(!result.ok)throw Error("Setup failed");
 save("dbaba-key",result.api_key);console.log("Setup saved; key stored locally.");break;}
 case "session":{const body=JSON.parse(fs.readFileSync(0,"utf8"));await call("/api/login","POST",{password:body.password});console.log(await call("/api/state"));await call("/api/logout","POST");break;}
 case "reset":if(process.env.DBABA_CONFIRM_RESET!=="yes")throw Error("Confirm reset");console.log(await call("/api/setup/reset","POST"));break;
 default:throw Error("Unknown group");
 }
}
main().catch(()=>{console.error("Local request failed; check configuration and credentials.");process.exitCode=1;});
