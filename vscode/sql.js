'use strict';
const tick=String.fromCharCode(96);
const isSQL=s=>/^\s*(SELECT|WITH|UPDATE|DELETE|INSERT)\b/i.test(s)&&/\b(FROM|SET|INTO|VALUES)\b/i.test(s);
function statements(s){
 const out=[];let a=0,q='';
 function add(b){let x=a,y=b;while(x<y&&/\s/.test(s[x]))x++;while(y>x&&/\s/.test(s[y-1]))y--;if(x<y)out.push({start:x,end:y,text:s.slice(x,y)});}
 for(let i=0;i<s.length;i++){const c=s[i];if(q){if(c==='\\')i++;else if(c===q){if(s[i+1]===q)i++;else q='';}continue;}
 if(c==="'"||c==='"'||c===tick)q=c;
 else if(s.startsWith('--',i)){while(i<s.length&&s[i]!=='\n')i++;}
 else if(s.startsWith('/*',i)){const e=s.indexOf('*/',i+2);i=e<0?s.length:e+1;}
 else if(c===';'){add(i+1);a=i+1;}}
 add(s.length);return out;
}
function endExpr(s,i){let depth=1,q='';for(;i<s.length;i++){const c=s[i];if(q){if(c==='\\')i++;else if(c===q)q='';continue;}if(c==="'"||c==='"'||c===tick)q=c;else if(c==='{')depth++;else if(c==='}'&&--depth===0)return i+1;}return -1;}
function interpolation(s,i,mode){
 if(mode==='js'&&s.startsWith('$'+'{',i))return endExpr(s,i+2);
 if(mode==='python'&&s[i]==='{')return endExpr(s,i+1);
 if(mode==='php'){
 if(s.startsWith('{$',i)||s.startsWith('$'+'{',i))return endExpr(s,i+2);
 const m=/^\$[a-z_]\w*(?:(?:->\w+)|(?:\[[^\]\n]*\]))*/i.exec(s.slice(i));if(m)return i+m[0].length;}
 return -1;
}
function placeholders(s,mode){let text='',dynamic=false;for(let i=0;i<s.length;){if(s[i]==='\\'&&i+1<s.length&&mode!=='python'){text+=s.slice(i,i+2);i+=2;continue;}if(mode==='python'&&(s.startsWith('{{',i)||s.startsWith('}}',i))){text+=s[i];i+=2;continue;}const e=interpolation(s,i,mode);if(e>i){text+='?';dynamic=true;i=e;}else text+=s[i++];}return {text,dynamic};}
function findQueries(s,language){
 if(language==='sql')return statements(s).filter(q=>isSQL(q.text)).map(q=>({...q,sql:q.text,dynamic:false,delimiter:''}));
 if(!['php','python','javascript','javascriptreact','typescript','typescriptreact','java'].includes(language))return [];
 const out=[];
 function add(a,b,delimiter,mode){const original=s.slice(a,b);a+=original.match(/^\s*/)[0].length;b-=original.match(/\s*$/)[0].length;if(b<=a)return;const text=s.slice(a,b),p=placeholders(text,mode);if(isSQL(p.text))out.push({start:a,end:b,text,sql:p.text,dynamic:p.dynamic,delimiter});}
 for(let i=0;i<s.length;){
 if(s.startsWith('//',i)||((language==='python'||language==='php')&&s[i]==='#')){while(i<s.length&&s[i]!=='\n')i++;continue;}
 if(s.startsWith('/*',i)){const e=s.indexOf('*/',i+2);i=e<0?s.length:e+2;continue;}
 if(language==='php'&&s.startsWith('<<<',i)){const m=/^<<<[ \t]*(?:'([A-Za-z_]\w*)'|"([A-Za-z_]\w*)"|([A-Za-z_]\w*))[ \t]*\r?\n/.exec(s.slice(i));if(m){const a=i+m[0].length,re=new RegExp('^[ \\t]*'+(m[1]||m[2]||m[3])+'(?=\\s|;|,|\\)|$)','gm');re.lastIndex=a;const e=re.exec(s);if(e){add(a,e.index,'heredoc',m[1]?'':'php');i=e.index+e[0].length;continue;}}}
 const c=s[i];if(c!=="'"&&c!=='"'&&c!==tick){i++;continue;}
 const prefix=language==='python'?(/[a-z]*$/i.exec(s.slice(0,i))[0]):'';
 const d=(language==='python'||language==='java')&&s.startsWith(c.repeat(3),i)?c.repeat(3):c,a=i+d.length;
 const mode=language==='php'&&c==='"'?'php':language==='python'&&/f/i.test(prefix)?'python':c===tick&&/^(javascript|typescript)/.test(language)?'js':'';
 let j=a;while(j<s.length){if(s[j]==='\\'){j+=2;continue;}if(mode==='python'&&(s.startsWith('{{',j)||s.startsWith('}}',j))){j+=2;continue;}const e=interpolation(s,j,mode);if(e>j){j=e;continue;}if(s.startsWith(d,j))break;j++;}
 if(j<s.length){add(a,j,d,mode);i=j+d.length;}else break;
 }return out;
}
function correction(reply){const m=/Corrected query:\s*/i.exec(reply);if(!m)return null;let s=reply.slice(m.index+m[0].length).trimStart();if(s.startsWith(tick.repeat(3))||s.startsWith('~~~')){const fence=s.slice(0,3),nl=s.indexOf('\n');if(nl<0)return null;s=s.slice(nl+1);const e=s.indexOf(fence);if(e>=0)s=s.slice(0,e);}const q=statements(s)[0];return q&&isSQL(q.text)?q.text:null;}
function change(s){return !/^\s*SELECT\b/i.test(s)||/\b(?:UPDATE|DELETE|INSERT|INTO)\b/i.test(s);}
function replacement(q,c){if(q.dynamic||change(q.sql)||change(c))return null;if(q.delimiter&&(c.includes('\\')||c.includes(q.delimiter[0])||/[$\x60{}]/.test(c)))return null;if(q.delimiter.length===1&&/[\r\n]/.test(c))return null;const indent=/\n([ \t]+)\S/.exec(q.text)?.[1]||'';return c.replace(/\r?\n/g,'\n'+indent);}
module.exports={findQueries,correction,replacement,change,placeholders};
