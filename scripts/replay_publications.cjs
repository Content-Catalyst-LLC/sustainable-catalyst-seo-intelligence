const {JSDOM,VirtualConsole}=require('jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const [, , capture, script, output] = process.argv;
assert.ok(capture && script && output, 'Pass captured HTML, trusted Git-extracted script and private output directory');
process.umask(0o077);
const url='https://sustainablecatalyst.com/publications/';
const doc=new JSDOM(fs.readFileSync(capture,'utf8'),{
  url,runScripts:'outside-only',virtualConsole:new VirtualConsole()
});
const win=doc.window;
win.matchMedia=()=>({matches:true,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
const uniq=()=>new Set([...win.document.querySelectorAll('main a[href]')].map(a=>a.href));
const before=uniq();
win.eval(fs.readFileSync(script,'utf8'));
win.addEventListener('DOMContentLoaded',()=>{
 const initial=uniq();
 fs.writeFileSync(path.join(output, 'publications-initial-dom.html'),doc.serialize());
 const articleUrls=new Set([...win.document.querySelectorAll('script[type="application/json"]')]
   .flatMap(x=>{try{return JSON.parse(x.textContent).field?.panels?.flatMap(p=>p.articles.map(a=>a.url))||[]}catch{return [];}}));
 let after;
 let proof;
 for(const button of win.document.querySelectorAll('[data-panel-next]')) {
   button.click();
   after=uniq();
   const added=[...after].filter(x=>!initial.has(x)&&articleUrls.has(x));
   if(added.length){proof=added;break;}
 }
 assert.ok(proof?.length);
 fs.writeFileSync(path.join(output, 'publications-after-next.html'),doc.serialize());
 const outcome={before:before.size,initial:initial.size,after:after.size,
   added_at_initial:[...initial].filter(x=>!before.has(x)),
   added_after_next:[...after].filter(x=>!initial.has(x)), added_article_targets:proof};
 fs.writeFileSync(path.join(output, 'publications-dom-replay.json'),JSON.stringify(outcome,null,2)+'\n');
 console.log(JSON.stringify(outcome)); win.close();
});
