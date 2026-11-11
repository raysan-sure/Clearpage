'use strict';
history.replaceState(null,'','/');
const $=id=>document.getElementById(id);
let documentJob=null, working=false, page=0, revision=0;
let results=[], selected=new Set(), previewed=new Set();
const modal=$('previewDialog');
let screen='setup';
function openWorkflow(){modal.hidden=false;document.body.classList.add('workflow-open');}
function closeWorkflow(push=true){
 if(working)return;
 modal.hidden=true;document.body.classList.remove('workflow-open');
 if(push)history.pushState(null,'','/');
 $('dropzone').focus();
}
function setScreen(next,push=true){
 const moveFocus=screen!==next||!modal.contains(document.activeElement);
 screen=next;modal.dataset.screen=next;
 const stages=['setup','compare','select-pages'],step=stages.indexOf(next);
 $('backLabel').textContent=['Back to upload','Back to setup','Back to comparison'][step];
 $('stageTitle').textContent=['Set up your PDF','Compare the result','Choose your pages'][step];
 $('stageEyebrow').textContent=`STEP ${step+1} OF 3`;
 $('selection').hidden=next!=='select-pages'||!results.length;
 document.querySelectorAll('[data-stage]').forEach(item=>{
  const index=stages.indexOf(item.dataset.stage);item.classList.toggle('active',index===step);item.classList.toggle('complete',index<step);
  if(index===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');
 });
 if(push&&location.hash!==`#${next}`)history.pushState(null,'',`#${next}`);
 $('optionsPanel').scrollTop=0;modal.scrollTop=0;
 showPage();
 if(moveFocus){
  const heading=next==='select-pages'?$('selectionTitle'):$('stageTitle');
  heading.tabIndex=-1;heading.focus({preventScroll:true});
 }
}
window.addEventListener('popstate',()=>{
 if(!documentJob)return;
 const next=location.hash.slice(1);
 if(!next){closeWorkflow(false);return;}
 openWorkflow();
 if(next==='select-pages'&&results.length)setScreen(next,false);
 else if(next==='compare'&&previewed.size)setScreen(next,false);
 else setScreen('setup',false);
 status(screen==='setup'?'Choose your PDF type, then try the first page.':screen==='compare'?'Compare Original and Cleaned, then clean all pages.':'Choose your cleaned pages, then download.');
});
async function request(path,body,raw=false,filename=''){
 const options=body===undefined?{}:{method:'POST',headers:{'X-ClearPage':'local'},body:raw?body:JSON.stringify(body)};
 if(body!==undefined)options.headers['Content-Type']=raw?'application/pdf':'application/json';
 if(filename)options.headers['X-Filename']=encodeURIComponent(filename);
 const response=await fetch(path,options),data=await response.json();
 if(!response.ok)throw new Error(data.error||'Something went wrong. Please try again.');
 return data;
}
function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
function setWorking(value){
 working=value;
 for(const id of ['file','method','tryPage','cleanAll','selectAll','previewPage','backPage'])$(id).disabled=value;
 $('download').disabled=value||!results.length;
 $('tryPage').disabled=value||!$('method').value;
 $('cleanAll').disabled=value||!$('method').value;
 $('pdfTypes').disabled=value;
 $('dropzone').setAttribute('aria-disabled',String(value));modal.setAttribute('aria-busy',String(value));
 $('pageSelections').querySelectorAll('input').forEach(input=>input.disabled=value);
 if(value)$('progress').removeAttribute('value');
 $('progress').hidden=!value;
}
function settings(){return {method:$('method').value,mode:'careful',strokes:[],page:0,use_reviewed:false,auto_restore:false,scan_inversion:false,fix_negative:false,polarity_pages:[]};}
function showPage(){
 if(!documentJob)return;
 $('before').src=`/api/page/${documentJob.id}/${page}/before?v=${revision}`;
 const hasPreview=screen!=='setup'&&previewed.has(page);
 $('afterPanel').hidden=!hasPreview;$('previewGrid').classList.toggle('comparing',hasPreview);
 if(hasPreview)$('after').src=`/api/page/${documentJob.id}/${page}/after?v=${revision}`;
 $('previewPage').value=String(page);
}
function syncSelection(){
 const all=selected.size===documentJob.count;
 $('selectAll').querySelector('.button-label').textContent=all?'Clear selection':'Select full PDF';$('selectAll').setAttribute('aria-pressed',String(all));
 $('selectionCount').textContent=`${selected.size} of ${documentJob.count} pages selected · All ${documentJob.count} pages stay in your PDF.`;
 $('pageSelections').querySelectorAll('input').forEach(input=>input.checked=selected.has(Number(input.value)));
}
function renderSelection(){
 $('pageSelections').replaceChildren();
 for(let index=0;index<documentJob.count;index++){
  const result=results.find(result=>result.page===index);
  const label=document.createElement('label');label.className='page-choice';
  const input=document.createElement('input');input.type='checkbox';input.value=String(index);input.setAttribute('aria-label',`Use cleaned page ${index+1}`);
  const thumbnail=document.createElement('span');thumbnail.className='thumbnail-preview';
  const image=document.createElement('img');image.alt=`Cleaned page ${index+1} thumbnail`;image.loading='lazy';image.decoding='async';image.width=180;image.height=240;
  image.addEventListener('load',()=>{
   if(image.naturalWidth&&image.naturalHeight)label.style.setProperty('--page-aspect',`${image.naturalWidth} / ${image.naturalHeight}`);
  });
  image.addEventListener('error',()=>{image.hidden=true;thumbnail.classList.add('unavailable');thumbnail.textContent=`Page ${index+1}`;});
  image.src=`/api/page/${documentJob.id}/${index}/after?v=${revision}`;
  thumbnail.append(image);
  const text=document.createElement('span');text.append(document.createTextNode(`Page ${index+1}`));
  text.className='thumbnail-caption';
  const note=document.createElement('small');note.textContent=result?.changed?(result.uncertain?'Review carefully':'Cleaned'):'Unchanged';text.append(note);
  input.addEventListener('change',()=>{input.checked?selected.add(index):selected.delete(index);syncSelection();});
  label.append(input,thumbnail,text);$('pageSelections').append(label);
 }
 syncSelection();$('selection').hidden=false;
}
async function upload(file){
 if(working||!file)return;
 $('uploadStatus').textContent='';
 if(!/\.pdf$/i.test(file.name)){ $('uploadStatus').textContent='Please choose a PDF file.';return; }
 if(file.size>200*1024*1024){$('uploadStatus').textContent='Please choose a PDF under 200 MB.';return;}
 setWorking(true);$('uploadStatus').textContent='Opening your PDF…';
 try{
  const previous=documentJob;
  documentJob=await request('/api/upload',file,true,file.name);
  page=0;revision++;results=[];selected.clear();previewed.clear();
  $('filename').textContent=file.name;$('filemeta').textContent=`${documentJob.count} ${documentJob.count===1?'page':'pages'} · ${(file.size/1024/1024).toFixed(1)} MB`;
  $('method').value='';
  document.querySelectorAll('input[name=pdfType]').forEach(input=>input.checked=false);
  $('selection').hidden=true;$('pageSelections').replaceChildren();$('previewPage').replaceChildren();
  $('previewPage').parentElement.hidden=documentJob.count===1;
  for(let index=0;index<documentJob.count;index++){
   const option=document.createElement('option');option.value=String(index);option.textContent=String(index+1);$('previewPage').append(option);
  }
  status('Select an example above, then try the first page.');openWorkflow();setScreen('setup');
  modal.scrollTop=0;$('optionsPanel').scrollTop=0;$('uploadStatus').textContent='';
  if(previous)await request(`/api/clear/${previous.id}`,{}).catch(()=>{});
 }catch(error){$('uploadStatus').textContent=error.message;}
 finally{setWorking(false);$('file').value='';}
}
async function waitForJob(expected='review'){
 for(;;){
  const state=await request(`/api/job/${documentJob.id}`);
  $('progress').max=state.count||1;$('progress').value=state.progress||0;
  if(['processing','exporting','learning','restoring'].includes(state.state)){
   status(state.message);await new Promise(resolve=>setTimeout(resolve,600));continue;
  }
  if(state.state!==expected)throw new Error(state.message||'The operation could not be completed. Please try again.');
  documentJob=state;return state;
 }
}
$('tryPage').addEventListener('click',async()=>{
 if(working||!documentJob)return;
 setWorking(true);status('Trying cleanup on the first page…');$('selection').hidden=true;results=[];selected.clear();
 try{
  const result=await request(`/api/preview/${documentJob.id}`,settings());
  page=0;previewed=new Set([0]);revision++;setScreen('compare');
  status(result.changed?'Compare Original and Cleaned, then clean all pages.':'No supported watermark found on page 1. You can clean all pages, or use browser Back to change the PDF type.');
 }catch(error){status(error.message,true);}
 finally{setWorking(false);}
});
$('cleanAll').addEventListener('click',async()=>{
 if(working||!documentJob)return;
 setWorking(true);status('Cleaning your PDF…');results=[];selected.clear();$('selection').hidden=true;
 try{
  await request(`/api/process/${documentJob.id}`,settings());const state=await waitForJob();results=state.results||[];
  selected=new Set(results.filter(result=>result.changed&&!result.uncertain).map(result=>result.page));
  previewed=new Set(results.map(result=>result.page));page=0;revision++;renderSelection();setScreen('select-pages');
  status(results.some(result=>result.changed)?`Cleanup complete. Review the preview, choose your pages, then download.${results.some(result=>result.uncertain)?' Pages needing extra review start unchecked.':''}`:'No supported watermarks found. You can download the original pages or try another PDF type.');
 }catch(error){status(error.message,true);}
 finally{setWorking(false);}
});
$('selectAll').addEventListener('click',()=>{
 if(working)return;
 selected=selected.size===documentJob.count?new Set():new Set(Array.from({length:documentJob.count},(_,index)=>index));syncSelection();
});
$('download').addEventListener('click',async()=>{
 if(working||!results.length)return;
 setWorking(true);status('Preparing your cleaned PDF…');
 try{
  await request(`/api/export/${documentJob.id}`,{keep:[...selected]});await waitForJob('done');
  const link=document.createElement('a');link.href=`/api/download/${documentJob.id}`;link.download='';document.body.append(link);link.click();link.remove();
  status('Your PDF is ready. The download has started.');
 }catch(error){status(error.message,true);}
 finally{setWorking(false);}
});
$('pdfTypes').addEventListener('change',event=>{
 if(working||event.target.name!=='pdfType')return;
 $('method').value=event.target.value;
 $('tryPage').disabled=false;$('cleanAll').disabled=false;
 results=[];selected.clear();previewed.clear();revision++;$('selection').hidden=true;
 if(screen!=='setup')setScreen('setup');else showPage();
 status('PDF type updated. Try the first page.');
});
$('previewPage').addEventListener('change',()=>{page=Number($('previewPage').value);showPage();});
$('backPage').addEventListener('click',()=>{
 if(working)return;
 history.back();
});
$('file').addEventListener('change',()=>upload($('file').files[0]));
$('dropzone').addEventListener('click',event=>{if(working)event.preventDefault();});
$('dropzone').addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();if(!working)$('file').click();}});
for(const name of ['dragover','dragenter'])$('dropzone').addEventListener(name,event=>{event.preventDefault();if(!working)$('dropzone').classList.add('drag');});
$('dropzone').addEventListener('dragleave',()=>$('dropzone').classList.remove('drag'));
$('dropzone').addEventListener('drop',event=>{
 event.preventDefault();$('dropzone').classList.remove('drag');
 if(event.dataTransfer.files.length>1){$('uploadStatus').textContent='Please drop one PDF at a time.';return;}
 upload(event.dataTransfer.files[0]);
});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!modal.hidden&&!working){event.preventDefault();closeWorkflow();}});
for(const id of ['before','after'])$(id).addEventListener('error',()=>status('Could not load the page preview. Try selecting the page again.',true));
