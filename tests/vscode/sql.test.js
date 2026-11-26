'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {findQueries,correction,replacement}=require('../../vscode/sql');
const {check,baseURL}=require('../../vscode/api');
const tick=String.fromCharCode(96);
const fixtures=[
 ['sql','SELECT id FROM t; UPDATE t SET id=1;',2],
 ['php','<?php $q="SELECT id FROM t WHERE id=$id";',1],
 ['php',"<?php $q=<<<SQL\n SELECT id FROM t WHERE id={$x}\nSQL;\n",1],
 ['python','q = """SELECT id FROM t\n WHERE id=1"""',1],
 ['python','q = f"SELECT id FROM t WHERE id={obj.id}"',1],
 ['javascript','const q = '+tick+'SELECT id FROM t WHERE id=$'+'{obj.id}'+tick+';',1],
 ['typescript','const q: string = "SELECT id FROM t";',1],
 ['java','String q = """\n SELECT id FROM t\n """;',1],
 ['java','String q = "SELECT id FROM t";',1]
];
for(const [lang,source,count] of fixtures)test(lang+' '+source,()=>{const qs=findQueries(source,lang);assert.equal(qs.length,count);for(const q of qs)assert.equal(source.slice(q.start,q.end),q.text);});
test('interpolations become placeholders',()=>{for(const [lang,source] of [fixtures[1],fixtures[2],fixtures[4],fixtures[5]]){const [q]=findQueries(source,lang);assert.equal(q.dynamic,true);assert.match(q.sql,/id=\?/);assert.equal(replacement(q,'SELECT id FROM t;'),null);}});
test('comments and ordinary strings ignored',()=>assert.equal(findQueries('// "SELECT id FROM t"\nconst s="hello";','javascript').length,0));
test('mock API and replacement preserve surrounding quotes',async()=>{
 const source='const q = "SELECT old FROM t";', [q]=findQueries(source,'javascript'),calls=[];
 const fetcher=async(url,options)=>{calls.push({url,options});return {ok:true,headers:{getSetCookie:()=>['ge_vid=test; Path=/']},json:async()=>calls.length===1?{ok:true}:{reply:'Fixed 1 mistake\nCorrected query:\nSELECT id FROM t;\nExplanation'}};};
 const reply=await check({url:'http://127.0.0.1:8765',database:'mysql',key:'test-only'},q,fetcher);
 assert.equal(calls[0].url,'http://127.0.0.1:8765/api/new-chat');
 assert.equal(calls[1].options.headers.Cookie,'ge_vid=test');
 assert.equal(JSON.parse(calls[1].options.body).text,'MySQL: SELECT old FROM t');
 assert.equal(calls[1].options.redirect,'error');
 assert.equal(source.slice(0,q.start)+replacement(q,correction(reply))+source.slice(q.end),'const q = "SELECT id FROM t;";');
});
test('change queries never automatically replaced',()=>{for(const sql of ['UPDATE t SET x=1','DELETE FROM t','INSERT INTO t VALUES (1)','WITH x AS (SELECT id FROM t) DELETE FROM t']){const [q]=findQueries(sql,'sql');assert.equal(replacement(q,sql),null);}});
test('remote opt in and redirect safety',()=>{assert.throws(()=>baseURL('https://example.org',false));assert.equal(baseURL('https://example.org',true),'https://example.org');assert.throws(()=>baseURL('http://localhost@host',true));});
