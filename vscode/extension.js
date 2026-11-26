'use strict';
const vscode=require('vscode');
const crypto=require('node:crypto');
const {findQueries,correction,replacement}=require('./sql');
const api=require('./api');
function activate(context){
 const diagnostics=vscode.languages.createDiagnosticCollection('DBaba'),records=new Map(),keyName='dbaba.apiKey';
 context.subscriptions.push(diagnostics);
 function command(name,fn){context.subscriptions.push(vscode.commands.registerCommand('dbaba.'+name,async(...args)=>{try{return await fn(...args);}catch(e){vscode.window.showErrorMessage('DBaba: '+(e instanceof Error?e.message:'Could not complete the request.'));}}));}
 async function config(){const c=vscode.workspace.getConfiguration('dbaba');return {url:c.get('url'),allowRemote:c.get('allowRemote'),database:c.get('database'),key:await context.secrets.get(keyName)};}
 command('setKey',async()=>{const key=await vscode.window.showInputBox({title:'DBaba API key',password:true,ignoreFocusOut:true,validateInput:v=>v.trim()?undefined:'Enter an API key.'});if(key!==undefined){await context.secrets.store(keyName,key.trim());vscode.window.showInformationMessage('DBaba API key saved.');}});
 command('removeKey',async()=>{await context.secrets.delete(keyName);vscode.window.showInformationMessage('DBaba API key removed.');});
 function clear(doc){const id=doc.uri.toString();records.delete(id);diagnostics.delete(doc.uri);}
 context.subscriptions.push(vscode.workspace.onDidChangeTextDocument(e=>clear(e.document)),vscode.workspace.onDidCloseTextDocument(clear));
 async function run(all){
 const editor=vscode.window.activeTextEditor;if(!editor)return;
 const doc=editor.document,version=doc.version,id=doc.uri.toString();
 let queries=findQueries(doc.getText(),doc.languageId);
 if(!all){const offset=doc.offsetAt(editor.selection.active);queries=queries.filter(q=>q.start<=offset&&offset<=q.end).slice(0,1);}
 if(!queries.length){vscode.window.showInformationMessage('No SQL query found.');return;}
 const cfg=await config();
 await vscode.window.withProgress({location:vscode.ProgressLocation.Notification,title:'Checking SQL with DBaba',cancellable:true},async(progress,token)=>{
 let entries=all?[]:(records.get(id)||[]).filter(e=>!queries.some(q=>q.start===e.query.start));
 for(const query of queries){
 if(token.isCancellationRequested||doc.version!==version||doc.isClosed)break;
 const reply=await api.check(cfg,query);
 if(token.isCancellationRequested||doc.version!==version||doc.isClosed)break;
 const corrected=correction(reply);
 if(/\bFixed\s+[1-9]\d*\s+mistake/i.test(reply)||corrected){
 const range=new vscode.Range(doc.positionAt(query.start),doc.positionAt(query.end));
 const note=query.dynamic?' The ? placeholders were your app variables.':'';
 const d=new vscode.Diagnostic(range,reply.trim().split(/\r?\n/).slice(0,3).join('\n')+note,vscode.DiagnosticSeverity.Warning);
 d.source='DBaba';entries.push({query,corrected,diagnostic:d,version});
 }
 records.set(id,entries);diagnostics.set(doc.uri,entries.map(e=>e.diagnostic));
 progress.report({increment:100/queries.length});
 }
 if(!entries.length&&!token.isCancellationRequested&&doc.version===version)vscode.window.showInformationMessage('DBaba reported no mistakes.');
 });
 }
 command('check',()=>run(false));command('checkAll',()=>run(true));
 command('applyCorrection',async(uri,entry)=>{
 const doc=await vscode.workspace.openTextDocument(uri);
 if(doc.version!==entry.version||doc.getText().slice(entry.query.start,entry.query.end)!==entry.query.text)throw new Error('The query changed. Check it again.');
 const corrected=entry.corrected;if(!corrected)return;
 if(entry.query.dynamic){await vscode.env.clipboard.writeText(corrected);vscode.window.showInformationMessage('Copied. The ? placeholders were your app variables.');return;}
 const text=replacement(entry.query,corrected);
 if(text===null){const preview=await vscode.workspace.openTextDocument({language:'sql',content:corrected});await vscode.window.showTextDocument(preview,{preview:true});return;}
 const edit=new vscode.WorkspaceEdit();edit.replace(uri,new vscode.Range(doc.positionAt(entry.query.start),doc.positionAt(entry.query.end)),text);if(!await vscode.workspace.applyEdit(edit))throw new Error('Could not replace the query.');
 });
 context.subscriptions.push(vscode.languages.registerCodeActionsProvider([{language:'sql'},{language:'php'},{language:'python'},{language:'javascript'},{language:'javascriptreact'},{language:'typescript'},{language:'typescriptreact'},{language:'java'}],{
 provideCodeActions(doc,range){return (records.get(doc.uri.toString())||[]).filter(e=>e.corrected&&e.version===doc.version&&e.diagnostic.range.intersection(range)).map(e=>{
 const title=e.query.dynamic?"Copy DBaba's corrected query":replacement(e.query,e.corrected)===null?"Preview DBaba's corrected query":"Use DBaba's corrected query";
 const action=new vscode.CodeAction(title,vscode.CodeActionKind.QuickFix);action.diagnostics=[e.diagnostic];action.command={command:'dbaba.applyCorrection',title,arguments:[doc.uri,e]};return action;
 });}
 },{providedCodeActionKinds:[vscode.CodeActionKind.QuickFix]}));
 context.subscriptions.push(vscode.window.registerWebviewViewProvider('dbaba.ask',{resolveWebviewView(view){
 const nonce=crypto.randomBytes(16).toString('hex');view.webview.options={enableScripts:true,localResourceRoots:[]};
 view.webview.html='<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'nonce-'+nonce+'\'; style-src \'unsafe-inline\'"><style>body{padding:10px}textarea{width:100%;box-sizing:border-box;min-height:100px;background:var(--vscode-input-background);color:var(--vscode-input-foreground)}button{margin-top:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body><label for="question">Ask DBaba</label><textarea id="question" maxlength="10000"></textarea><button id="send">Ask</button><pre id="reply" aria-live="polite"></pre><script nonce="'+nonce+'">const vscode=acquireVsCodeApi(),q=document.getElementById("question"),b=document.getElementById("send"),r=document.getElementById("reply");b.onclick=()=>{if(!q.value.trim())return;b.disabled=true;r.textContent="Checking…";vscode.postMessage({type:"ask",text:q.value});};window.addEventListener("message",e=>{r.textContent=e.data.reply;b.disabled=false;});</script></body></html>';
 let busy=false;const listener=view.webview.onDidReceiveMessage(async msg=>{if(busy||msg.type!=='ask'||typeof msg.text!=='string')return;busy=true;try{const cfg=await config();await view.webview.postMessage({reply:await api.ask(cfg,msg.text)});}catch(e){await view.webview.postMessage({reply:'DBaba: '+e.message});}finally{busy=false;}});view.onDidDispose(()=>listener.dispose());
 }}));
 command('ask',()=>vscode.commands.executeCommand('dbaba.ask.focus'));
}
module.exports={activate};
