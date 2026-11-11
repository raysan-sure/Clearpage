'use strict';
let files=[], queueRunning=false, queueTimer=null, reviewedPages=new Set(), fileConfirmed=false;
let draftDirty=false, presets=[], changingFile=false;
const busyStates=['processing','exporting','learning','restoring'];
const baseSetBusy=setBusy, baseInvalidate=invalidate, baseChangedReview=changedReview, basePoll=poll;
setBusy=function(value){baseSetBusy(value);syncWorkspace();};
invalidate=function(){draftDirty=true;fileConfirmed=false;reviewedPages.clear();baseInvalidate();renderReviewTools();};
changedReview=function(){fileConfirmed=false;reviewedPages.delete(page);baseChangedReview();renderReviewTools();};
poll=async function(){const previousState=job?.state;await basePoll();if(!busy&&job){draftDirty=false;await refreshQueue();renderReviewTools();if(typeof navigateWorkspaceSection==='function'&&busyStates.includes(previousState)&&['review','done'].includes(job.state))navigateWorkspaceSection(job.state==='done'?'review':'compareSection');}};

function settingsOnScreen(value){
 const o=value||{};
 $('method').value=o.method||'preset';$('mode').value=o.mode==='strong'?'careful':o.mode||'careful';
 $('fixNegative').checked=!!o.fix_negative;polarityPages=new Set(o.polarity_pages||[]);
 $('useReviewed').checked=o.use_reviewed!==false;$('autoRestore').checked=!!o.auto_restore;
 $('scanInversion').checked=!!o.scan_inversion;strokes=o.strokes||[];
 const manual=$('method').value==='manual';$('brushTools').hidden=!manual;
 $('originalPaper').classList.toggle('painting',manual);syncOverlayControls();updateOptionExamples();
}
async function flushView(){
 if(!job||busy||queueRunning)return;
 job=await api('/api/view/'+job.id,{page,keep:[...keep],reviewed:[...reviewedPages],
      confirmed:fileConfirmed,options:options(),dirty:draftDirty});
 draftDirty=false;
 const i=files.findIndex(f=>f.id===job.id);if(i>=0)files[i]=job;
}
async function activateJob(data){
 clearTimeout(polling);loadingPage++;job=data;page=Math.min(data.ui?.page||0,data.count-1);
 results=data.results||[];previewed=new Set(results.map(r=>r.page));
 keep=new Set(data.ui?.keep??results.filter(r=>r.changed&&!r.uncertain).map(r=>r.page));keep.initialized=true;
 reviewedPages=new Set(data.ui?.reviewed||[]);fileConfirmed=!!data.ui?.confirmed;draftDirty=false;
 clearQuality();settingsOnScreen(data.ui?.options||data.options||{method:data.overlays?.some(r=>r.overlays)?'overlay':'preset',auto_restore:false,use_reviewed:true});
 $('welcome').hidden=true;$('workspace').hidden=false;$('controls').hidden=false;
 $('filename').textContent=data.name;$('filemeta').textContent=`${data.count} pages · ${(data.size/1024/1024).toFixed(1)} MB · Original retained`;
 $('inputNote').textContent=data.input_type!=='PDF'?data.input_note||'':'';
 $('total').textContent=`of ${data.count}`;$('pageNumber').max=data.count;
 $('review').hidden=!['review','done'].includes(data.state);$('progressBox').hidden=true;
 $('download').href='/api/download/'+data.id;$('download').hidden=data.state!=='done';$('export').hidden=data.state==='done';
 $('showMask').checked=false;$('showChanges').checked=false;$('needsReviewOnly').checked=false;
 revision++;setBusy(false);await loadPage();renderReviewTools();renderQueue();
 if(busyStates.includes(data.state)&&!queueRunning){setBusy(true);$('progressBox').hidden=false;poll();}
}
async function refreshQueue(){
 const state=await api('/api/workspace');files=state.jobs;queueRunning=state.queue.active;
 $('queueStatus').textContent=state.queue.message;
 $('downloadQueue').hidden=!state.queue.zip_ready;$('exportQueue').hidden=state.queue.zip_ready;
 renderQueue();return state;
}
function renderQueue(){
 $('fileQueue').hidden=!files.length;$('queueCount').textContent=`(${files.length}/20)`;
 $('queueList').replaceChildren();
 for(const item of files){
  const button=document.createElement('button');button.className='queue-file'+(item.id===job?.id?' selected':'');
  const title=document.createElement('strong');title.textContent=item.name;
  const status=document.createElement('span');status.textContent=item.ui?.confirmed?'Review finished':({ready:'Ready to clean',review:'Needs review',done:'PDF ready',processing:'Cleaning…',restoring:'Restoring words…',error:'Needs another try',cancelled:'Stopped',exporting:'Creating PDF…'}[item.state]||item.state);
  button.append(title,status);button.disabled=busy||queueRunning||changingFile;
  const action=document.createElement('span');action.className='queue-open';action.textContent=['review','done'].includes(item.state)?'Open review →':'Open cleanup →';button.append(action);
  button.onclick=async()=>{if(busy||queueRunning||changingFile)return;changingFile=true;syncWorkspace();try{await flushView();await activateJob(await api('/api/job/'+item.id));if(typeof navigateWorkspaceSection==='function')navigateWorkspaceSection(['review','done'].includes(job.state)?'compareSection':'typeSection');}catch(e){notice(e.message,true);}finally{changingFile=false;syncWorkspace();}};
  $('queueList').append(button);
 }
 syncWorkspace();
}
function syncWorkspace(){
 const locked=busy||queueRunning||changingFile;
 for(const id of ['addFiles','cleanQueue','saveWork','exportQueue','finishReview','markReviewed','nextReview','savePreset','applyPreset'])$(id).disabled=locked;
 $('saveWork').disabled=locked||!files.length;
 $('cleanQueue').disabled=locked||!files.some(f=>!['review','done'].includes(f.state));
 $('exportQueue').disabled=locked||!files.some(f=>f.ui?.confirmed);
 $('stopQueue').hidden=!queueRunning;$('quit').disabled=locked;
 for(const button of $('queueList').children)button.disabled=locked;
 $('showChanges').disabled=locked||!previewed.has(page);$('needsReviewOnly').disabled=locked;
 $('reset').disabled=locked;
 renderReviewTools();
}
async function uploadFiles(list){
 if(busy||queueRunning)return;
 try{await flushView();}catch(e){return notice(e.message,true);}
 const incoming=Array.from(list);
 if(files.length+incoming.length>20)return notice('You can open up to 20 files at once.',true);
 setBusy(true);let first=null,errors=[];
 try{
  for(const file of incoming){
   if(!/\.(pdf|jpe?g|png|tiff?)$/i.test(file.name)||file.size>200*1024*1024){errors.push(file.name+': choose a PDF or scan up to 200 MB');continue;}
   $('dropzone').querySelector('strong').textContent='Opening '+file.name+'…';
   const response=await fetch('/api/upload',{method:'POST',headers:{'X-ClearPage':'local','X-Filename':encodeURIComponent(file.name)},body:file});
   const data=await response.json();if(!response.ok){errors.push(file.name+': '+data.error);continue;}
   first??=data;
  }
  await refreshQueue();if(first)await activateJob(first);
  if(errors.length)notice(errors.join(' · '),true);
 }catch(e){notice(e.message,true);}finally{setBusy(false);$('file').value='';$('dropzone').querySelector('strong').textContent='Choose PDFs or scans';}
}
$('file').onchange=()=>uploadFiles($('file').files);
$('addFiles').onclick=()=>$('file').click();
// app.js delegates drag/drop to this multi-file handler as well.
$('reset').onclick=async()=>{
 if(!job||busy||queueRunning)return;
 if(!confirm('Remove this file from the open workspace? Save work first if you want to keep unfinished repairs.'))return;
 try{await api('/api/clear/'+job.id,{});job=null;await refreshQueue();if(files.length)await activateJob(files[0]);else{location.reload();}}catch(e){notice(e.message,true);}
};
async function queuePoll(){
 clearTimeout(queueTimer);
 try{
  const state=await refreshQueue();
  if(state.queue.active){setBusy(true);queueTimer=setTimeout(queuePoll,750);return;}
  setBusy(false);const selected=files.find(f=>f.id===job?.id)||files[0];if(selected)await activateJob(selected);
 }catch(e){notice(e.message,true);queueTimer=setTimeout(queuePoll,2000);}
}
$('cleanQueue').onclick=async()=>{
 try{await flushView();await refreshQueue();const ids=files.filter(f=>!['review','done'].includes(f.state)).map(f=>f.id);if(!ids.length)return;
  await api('/api/queue',{ids});queueRunning=true;setBusy(true);queuePoll();
 }catch(e){notice(e.message,true);setBusy(false);}
};
$('exportQueue').onclick=async()=>{
 try{await flushView();await refreshQueue();const ids=files.filter(f=>f.ui?.confirmed).map(f=>f.id);
  await api('/api/queue',{ids,export:true});queueRunning=true;setBusy(true);queuePoll();
 }catch(e){notice(e.message,true);setBusy(false);}
};
$('stopQueue').onclick=async()=>{try{await api('/api/queue/stop',{});}catch(e){notice(e.message,true);}};

function pendingReview(){return results.filter(r=>r.changed&&!reviewedPages.has(r.page)).sort((a,b)=>Number(!!b.uncertain)-Number(!!a.uncertain)||a.page-b.page);}
function renderReviewTools(){
 $('reviewTools').hidden=!results.length||!['review','done'].includes(job?.state);
 const remaining=pendingReview();$('reviewCount').textContent=fileConfirmed?'File review finished':`${remaining.length} changed pages to check`;
 $('markReviewed').textContent=reviewedPages.has(page)?`Page ${page+1} reviewed ✓`:`Mark page ${page+1} reviewed`;
 $('markReviewed').disabled=busy||queueRunning||!results.some(r=>r.page===page&&r.changed);
 $('finishReview').disabled=busy||queueRunning||remaining.length>0||!results.length;
 $('nextReview').disabled=busy||queueRunning||!remaining.length;
}
renderPageList=function(){
 syncSimpleView();const container=$('pageList');
 if(container.dataset.job!==job?.id){container.replaceChildren();container.dataset.job=job?.id||'';}
 const existing=new Map([...container.children].map(card=>[Number(card.dataset.page),card]));
 const reviewCandidates=$('needsReviewOnly').checked?pendingReview():results;
 const shown=$('changedPagesOnly').checked?reviewCandidates.filter(result=>result.changed):reviewCandidates;
 const cards=[];
 for(const result of shown){
  let card=existing.get(result.page);
  if(!card){
   card=document.createElement('article');card.dataset.page=result.page;
   const button=document.createElement('button');button.type='button';button.className='thumbnail-open';
   const frame=document.createElement('span');frame.className='thumbnail-frame';
   const fallback=document.createElement('span');fallback.className='thumbnail-fallback';fallback.textContent='Preview unavailable';fallback.hidden=true;
   const image=document.createElement('img');image.alt=`Original page ${result.page+1}`;image.loading='lazy';image.decoding='async';image.width=180;image.height=240;
   image.onerror=()=>{image.hidden=true;fallback.hidden=false;};
   image.src=`/api/thumbnail/${job.id}/${result.page}`;
   const number=document.createElement('strong');number.className='thumbnail-number';number.textContent=`Page ${result.page+1}`;
   const state=document.createElement('span');state.className='thumbnail-status';
   frame.append(image,fallback);button.append(frame,number,state);
   button.onclick=async()=>{if(busy||queueRunning)return;page=result.page;await loadPage();if(typeof navigateWorkspaceSection==='function')navigateWorkspaceSection('compareSection');};
   const label=document.createElement('label');label.className='thumbnail-keep';
   const check=document.createElement('input');check.type='checkbox';check.setAttribute('aria-label',`Use cleaned page ${result.page+1}`);
   check.onchange=()=>{check.checked?keep.add(result.page):keep.delete(result.page);fileConfirmed=false;baseChangedReview();renderReviewTools();};
   label.append(check,document.createTextNode('Use cleaned'));card.append(button,label);
  }
  const status=!result.changed?'Unchanged':reviewedPages.has(result.page)?'Reviewed':result.uncertain?'Check text':'To review';
  card.className='page-thumbnail'+(page===result.page?' current':'')+(result.uncertain&&!reviewedPages.has(result.page)?' attention':'');
  const button=card.querySelector('.thumbnail-open');button.disabled=busy||queueRunning;button.setAttribute('aria-label',`Open page ${result.page+1} · ${status}`);
  if(page===result.page)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  card.querySelector('.thumbnail-status').textContent=status;
  const check=card.querySelector('input');check.checked=keep.has(result.page);check.disabled=busy||queueRunning||!result.changed;
  cards.push(card);
 }
 if(cards.length!==container.children.length||cards.some((card,index)=>container.children[index]!==card))container.replaceChildren(...cards);
 if(!cards.length){const message=document.createElement('p');message.className='thumbnail-empty';message.textContent=$('needsReviewOnly').checked?'All changed pages have been reviewed.':$('changedPagesOnly').checked?'No changed pages in this file.':'Clean this file to see its pages here.';container.append(message);}
 renderReviewTools();
};
$('markReviewed').onclick=()=>{reviewedPages.add(page);fileConfirmed=false;renderPageList();};
$('nextReview').onclick=async()=>{const list=pendingReview();if(list.length){page=(list.find(r=>r.page>page)||list[0]).page;await loadPage();if(typeof navigateWorkspaceSection==='function')navigateWorkspaceSection('compareSection');}};
$('needsReviewOnly').onchange=renderPageList;
$('changedPagesOnly').onchange=renderPageList;
$('finishReview').onclick=async()=>{if(pendingReview().length)return;try{fileConfirmed=true;await flushView();await refreshQueue();renderReviewTools();notice('File review finished. It can now be included in the ZIP.');}catch(e){fileConfirmed=false;notice(e.message,true);}};
$('showChanges').onchange=loadPage;
const scrollPanels=[...document.querySelectorAll('.paper-scroll')];let syncing=false;
scrollPanels.forEach((panel,index)=>panel.addEventListener('scroll',()=>{
 if(syncing||!$('syncScroll').checked)return;syncing=true;const other=scrollPanels[1-index];
 other.scrollTop=panel.scrollTop/Math.max(1,panel.scrollHeight-panel.clientHeight)*Math.max(0,other.scrollHeight-other.clientHeight);
 other.scrollLeft=panel.scrollLeft/Math.max(1,panel.scrollWidth-panel.clientWidth)*Math.max(0,other.scrollWidth-other.clientWidth);
 requestAnimationFrame(()=>{syncing=false;});
}));
const oldProcess=$('process').onclick, oldPreview=$('preview').onclick;
const oldRestoration=runRestoration;
runRestoration=async function(clear){reviewedPages.clear();fileConfirmed=false;await oldRestoration(clear);};
$('process').onclick=async()=>{try{await flushView();reviewedPages.clear();fileConfirmed=false;draftDirty=false;await oldProcess();}catch(e){notice(e.message,true);}};
$('preview').onclick=async()=>{try{await flushView();reviewedPages.clear();fileConfirmed=false;draftDirty=false;await oldPreview();}catch(e){notice(e.message,true);}};

async function saveWorkspace(){
 await flushView();setBusy(true);
 try{await api('/api/save-work',{});$('saveStatus').textContent='Work saved on this computer at '+new Date().toLocaleTimeString()+'. Save again after further changes.';}
 finally{setBusy(false);}
}
$('saveWork').onclick=async()=>{try{await saveWorkspace();notice('Files, review choices and accepted repairs saved.');}catch(e){notice(e.message,true);}};
$('quit').onclick=async()=>{
 try{if(files.length)await saveWorkspace();await api('/api/quit',{});clearTimeout(polling);clearTimeout(queueTimer);document.body.replaceChildren();const p=document.createElement('p');p.textContent='ClearPage is closed. Reopen the app and choose Continue saved work to resume.';document.body.append(p);}catch(e){notice('Could not save and close: '+e.message,true);}
};
async function showSaved(){
 const saved=await api('/api/saved');$('savedWork').hidden=!saved.length;$('savedList').replaceChildren();
 for(const item of saved){
  const row=document.createElement('div');row.className='saved-row';const label=document.createElement('span');label.textContent=`${item.names.length} files · ${item.names[0]} · ${new Date(item.saved_at*1000).toLocaleString()}`;
  const resume=document.createElement('button');resume.textContent='Resume';resume.className='small';resume.onclick=async()=>{try{await api('/api/resume/'+item.id,{});await refreshQueue();await activateJob(files[0]);}catch(e){notice(e.message,true);}};
  const remove=document.createElement('button');remove.textContent='Delete saved copy';remove.className='small';remove.onclick=async()=>{if(!confirm('Delete this saved workspace? Your original input files are not affected.'))return;try{await api('/api/saved/'+item.id+'/delete',{});await showSaved();}catch(e){notice(e.message,true);}};
  row.append(label,resume,remove);$('savedList').append(row);
 }
}
async function loadPresets(){presets=await api('/api/presets');$('presetChoice').replaceChildren(new Option('Choose a saved setup',''));presets.forEach((p,i)=>$('presetChoice').add(new Option(p.name,String(i))));}
$('savePreset').onclick=async()=>{try{await api('/api/presets',{name:$('presetName').value,options:options()});await loadPresets();notice('Cleanup setup saved on this computer.');}catch(e){notice(e.message,true);}};
$('applyPreset').onclick=()=>{const choice=$('presetChoice').value;if(choice==='')return;settingsOnScreen(presets[Number(choice)].options);invalidate();setBusy(false);notice('Setup applied. Try one page and check the watermark position.');};
$('checkUpdate').onclick=async()=>{if(busy||queueRunning)return;$('checkUpdate').disabled=true;try{const result=await api('/api/check-update',{});$('updateStatus').textContent=result.message;$('releaseLink').hidden=!result.available;if(result.package)$('updatePackage').textContent='Installed: '+result.current_version+' · Published: '+result.version+' · Package: '+result.package;$('releaseNotes').textContent=result.notes||result.message;}catch(e){$('updateStatus').textContent=e.message;}finally{$('checkUpdate').disabled=false;}};
async function bootWorkspace(){try{await loadPresets();const state=await refreshQueue();if(files.length)await activateJob(files[0]);else await showSaved();if(state.queue.active)queuePoll();}catch(e){notice(e.message,true);}}
bootWorkspace();
