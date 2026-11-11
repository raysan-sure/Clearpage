'use strict';
history.replaceState(null, '', '/');
const $ = id => document.getElementById(id);
let job = null, page = 0, strokes = [], activeStroke = null, erasing = false, busy = false;
let keep = new Set(), results = [], previewed = new Set(), polling = null, revision = 0;
let noticeTimer, loadingPage = 0;
let polarityPages = new Set(), qualityReport = null;
function notice(message, error=false) { if(!$('notice'))return; $('notice').textContent=message; $('notice').classList.toggle('error',error); $('notice').hidden=false; clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>{if($('notice'))$('notice').hidden=true;},8000); }
async function api(path, body, raw=false) {
 const options=body===undefined?{}:{method:'POST',headers:{'X-ClearPage':'local'},body:raw?body:JSON.stringify(body)};
 if(body!==undefined&&!raw) options.headers['Content-Type']='application/json';
 const response=await fetch(path,options); const data=await response.json();
 if(!response.ok) throw Error(data.error||'The operation could not be completed.'); return data;
}
function setBusy(value) { busy=value; for(const id of ['method','mode','preview','process','reset','erase','undo','clearBrush','brushSize','export','allOriginal','allCleaned','previous','next','pageNumber','useReviewed','importReviewed','autoRestore','scanInversion','restoreNow','clearAutomatic','forcePolarity','fixNegative']) $(id).disabled=value; if(!value&&job){$('previous').disabled=page===0;$('next').disabled=page===job.count-1;} $('brushCanvas').style.pointerEvents=value?'none':($('method').value==='manual'?'auto':'none'); syncOverlayControls(); syncSimpleView(); if(typeof syncRepair==='function')syncRepair(); }
function syncOverlayControls() {
 const polarity=$('method').value==='polarity';
 if($('method').value==='repeated_diagonal_ai')$('mode').value='lama';
 const dotted=['huntington_background','dotted_document','dotted_map','unofficial_copy','repeated_diagonal','repeated_diagonal_ai'].includes($('method').value);
 const huntington=$('method').value==='huntington_background';
 const overlay=['overlay','object'].includes($('method').value)||polarity||dotted;
 $('fixNegative').disabled=busy||polarity||dotted;
 $('polarityTools').hidden=dotted||!(polarity||$('fixNegative').checked);
 $('showMask').disabled=busy||polarity;if(polarity)$('showMask').checked=false;
 for(const id of ['mode','useReviewed','importReviewed','autoRestore','scanInversion'])$(id).disabled=busy||overlay;
 $('aiHelp').hidden=overlay||$('mode').value!=='lama';
 $('wordRepair').hidden=overlay&&!huntington;$('repairTools').hidden=overlay&&!huntington;
 if(huntington)$('autoRestore').disabled=busy;
 if(overlay){$('modeHelp').textContent='Original text and images are preserved. No pixel reconstruction or word replacement is applied.';$('methodHelp').textContent='Removes supported A-PDF text, Cook County and OKCountyRecords overlays. Unknown layouts remain unchanged.';}
 else {$('modeHelp').textContent={lama:'Local LaMa AI reconstructs selected watermark pixels. Review generated letters before export.',careful:'Preserves detected text lines and connected strokes. Some watermark fragments may remain.',light:'Only cleans lighter pixels, preserving dark strokes and nearby edges.'}[$('mode').value]||'';}
}
function options() { return {method:$('method').value,mode:$('mode').value,strokes,page,fix_negative:$('fixNegative').checked,polarity_pages:[...polarityPages],use_reviewed:$('useReviewed').checked,auto_restore:$('autoRestore').checked,scan_inversion:$('scanInversion').checked}; }
function invalidate() { clearQuality(); if(!job)return; sessionStorage.removeItem('keep-'+job.id); revision++; previewed.clear(); results=[]; keep.clear(); $('review').hidden=true; $('keepLabel').hidden=true; $('download').hidden=true; $('export').hidden=false; $('previewState').textContent='SETTINGS CHANGED'; $('pageResult').textContent='Preview again to see these settings.'; loadPage(); }
async function upload(file) {
 if(!file||busy)return;
 if(file.size>200*1024*1024) return notice('Maximum file size is 200 MB.',true);
 if(!/\.(pdf|jpe?g|png|tiff?)$/i.test(file.name))return notice('Choose a PDF, JPG, PNG or TIFF scan.',true);
 setBusy(true); $('dropzone').querySelector('strong').textContent='Opening your file…';
 try {
  const response=await fetch('/api/upload',{method:'POST',headers:{'X-ClearPage':'local','X-Filename':encodeURIComponent(file.name),'Content-Type':file.type||'application/octet-stream'},body:file});
  const data=await response.json(); if(!response.ok)throw Error(data.error);
  job=data;$('fixNegative').checked=false;polarityPages.clear();clearQuality(); $('method').value=job.overlays?.some(r=>r.overlays)?'overlay':'preset'; $('method').onchange(); page=0; strokes=[]; previewed.clear(); results=[]; keep.clear();
  $('welcome').hidden=true; $('workspace').hidden=false; $('controls').hidden=false;
  $('step1').classList.remove('active'); $('step2').classList.add('active');
  $('filename').textContent=file.name; $('filemeta').textContent=`${job.count} pages · ${(file.size/1024/1024).toFixed(1)} MB · Original retained`;
  $('inputNote').textContent=job.input_type&&job.input_type!=='PDF'?job.input_note:'';
  $('total').textContent=`of ${job.count}`; $('pageNumber').max=job.count;
  await loadPage();
 } catch(error) {notice(error.message,true);} finally {setBusy(false);$('dropzone').querySelector('strong').textContent='Choose a PDF or scan';$('file').value='';}
}
function imageURL(kind) { return `/api/page/${job.id}/${page}/${kind}?v=${revision}`; }
async function loadPage() {
 if(!job)return; clearQuality(); $('forcePolarity').checked=polarityPages.has(page); const current=++loadingPage; if(typeof repairPageLoaded==='function')repairPageLoaded(); $('paintLabel').textContent=$('method').value==='manual'?'PAINT THE WATERMARK':'SOURCE COPY';
 $('pageNumber').value=page+1; $('previous').disabled=page===0; $('next').disabled=page===job.count-1;
 const src=imageURL('before');
 await new Promise(resolve=>{ $('before').onload=resolve;$('before').onerror=()=>{notice('Could not load this page.',true);resolve();};$('before').src=src; });
 if(current!==loadingPage)return;
 $('after').src=previewed.has(page)&&$('showChanges')?.checked?`/api/changes/${job.id}/${page}?v=${revision}`:imageURL(previewed.has(page)?($('showMask').checked?'mask':'after'):'before');
 $('previewState').textContent=previewed.has(page)?($('showChanges')?.checked?'CHANGED PIXELS':$('showMask').checked?'SELECTION OVERLAY':'PREVIEW READY'):'NOT PROCESSED';
 $('originalPaper').style.setProperty('--natural-width',$('before').naturalWidth+'px');
 $('after').parentElement.style.setProperty('--natural-width',$('before').naturalWidth+'px');
 draw(); updatePageResult(); renderPageList(); if(typeof syncRepair==='function')syncRepair();
}
function draw() {
 const c=$('brushCanvas');c.width=$('before').naturalWidth||1192;c.height=$('before').naturalHeight||1684;
 const ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);
 if($('method').value!=='manual')return;
 for(const stroke of [...strokes,...(activeStroke?[activeStroke]:[])]) {
  ctx.globalCompositeOperation=stroke.erase?'destination-out':'source-over';ctx.strokeStyle='#ed6e43';ctx.fillStyle='#ed6e43';ctx.lineWidth=stroke.width*c.width;ctx.lineCap='round';ctx.lineJoin='round';
  if(stroke.points.length===1){ctx.beginPath();ctx.arc(stroke.points[0][0]*c.width,stroke.points[0][1]*c.height,ctx.lineWidth/2,0,Math.PI*2);ctx.fill();}
  else{ctx.beginPath();stroke.points.forEach(([x,y],i)=>i?ctx.lineTo(x*c.width,y*c.height):ctx.moveTo(x*c.width,y*c.height));ctx.stroke();}
 }
 c.style.opacity='.42';
}
function point(event) {const rect=$('brushCanvas').getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];}
$('brushCanvas').addEventListener('pointerdown',event=>{if(busy)return;event.preventDefault();event.target.setPointerCapture(event.pointerId);activeStroke={points:[point(event)],width:Number($('brushSize').value)/100,erase:erasing};draw();});
$('brushCanvas').addEventListener('pointermove',event=>{if(!activeStroke)return;if(activeStroke.points.length<2999)activeStroke.points.push(point(event));draw();});
function endStroke(){if(activeStroke){strokes.push(activeStroke);activeStroke=null;invalidate();draw();}}
$('brushCanvas').addEventListener('pointerup',endStroke);$('brushCanvas').addEventListener('pointercancel',endStroke);
$('erase').onclick=()=>{erasing=!erasing;$('erase').textContent=`Eraser: ${erasing?'on':'off'}`;};
$('undo').onclick=()=>{strokes.pop();invalidate();draw();};$('clearBrush').onclick=()=>{strokes=[];invalidate();draw();};
$('brushSize').oninput=()=>$('brushValue').textContent=$('brushSize').value+'%';
$('method').onchange=()=>{const manual=$('method').value==='manual';if(manual)$('scanInversion').checked=false;$('brushTools').hidden=!manual;$('originalPaper').classList.toggle('painting',manual);$('paintLabel').textContent=manual?'PAINT THE WATERMARK':'SOURCE COPY';$('methodHelp').textContent=manual?'Select an area yourself. Best for marks on blank backgrounds.':'Detects the recurring “IMAGES/COPY PROPERTY…” mark. Pages without a confident match stay unchanged.';invalidate();setBusy(false);};
$('mode').onchange=()=>{const ai=$('mode').value==='lama';$('aiHelp').hidden=!ai;if(ai)$('scanInversion').checked=false;$('modeHelp').textContent={lama:'Local LaMa AI reconstructs selected watermark pixels. Review generated letters before export.',careful:'Preserves detected text lines and connected strokes. Some watermark fragments may remain.',light:'Only cleans lighter pixels, preserving dark strokes and nearby edges.',strong:'Replaces every selected pixel. This can erase words, numbers, or signatures inside the selection. Review closely.'}[$('mode').value];invalidate();};
$('preview').onclick=async()=>{if(!job||busy)return;setBusy(true);$('previewState').textContent='PROCESSING';try{const result=await api(`/api/preview/${job.id}`,options());revision++;results=[];keep.clear();$('review').hidden=true;previewed=new Set([page]);await loadPage();showResult(result);}catch(error){notice(error.message,true);}finally{setBusy(false);}};
function showTechnicalResult(result){if(!result)return;if(result.scan_pattern){$('pageResult').textContent=result.pattern_note+(result.steps?' Passes: '+result.steps.map(s=>s.iteration+'. '+s.stage.replaceAll('_',' ')+': '+s.changed_pixels.toLocaleString()+' pixels').join('; ')+'.':'')+(result.automatic?' '+result.automatic+' exact scanned-word repairs.':'')+(result.repairs?' '+result.repairs+' confirmed word repairs.':'');return;}if(result.native_dots){$('pageResult').textContent=result.note;return;}if(result.color_correction?.applied){$('pageResult').textContent='Page colors corrected, then the selected cleanup was applied. '+(result.structural?`${result.overlays} separate overlays removed. `:'')+result.color_correction.note;return;}if(result.polarity){$('pageResult').textContent=result.polarity.note;return;}if(result.structural){$('pageResult').textContent=result.matched?`${result.providers.join(' / ')}: ${result.overlays} watermark overlays removed. Original text and images preserved; no words reconstructed.`:'No recognized PDF watermark on this page. Original page retained.';return;}$('pageResult').textContent=(result.inversion_note?`${result.inverted||0} pixels reverse-blended · ${result.inversion_note} `:'')+(result.automatic?`${result.automatic} automatically reconstructed words/phrases · `:'')+(result.repairs?`${result.repairs} confirmed word repairs · `:'')+(result.reviewed?'Exact source-page match: using your imported reviewed repairs.':!result.matched?(result.repairs?'Watermark cleanup left the source unchanged; confirmed word repairs are included.':'No confident watermark match. This page stays unchanged.'):`${result.uncertain?(result.ai?'AI RECONSTRUCTION: kept original by default. Check affected words before selecting this page. ':'UNCERTAIN PLACEMENT: kept original by default. Inspect the selected pixels before accepting. '):''}${result.changed.toLocaleString()} pixels cleaned · ${result.protected.toLocaleString()} selected pixels protected${result.confidence!==null?' · Watermark alignment '+Math.round(result.confidence*100)+'% (not text accuracy)':''}. Check any overlap with text.`);}
function updatePageResult(){const result=results.find(r=>r.page===page);$('keepLabel').hidden=!result;$('keepPage').checked=keep.has(page);$('keepPage').disabled=!result?.changed;showResult(result);}
$('process').onclick=async()=>{if(!job||busy)return;setBusy(true);try{job=await api(`/api/process/${job.id}`,options());sessionStorage.removeItem('keep-'+job.id);results=[];keep=new Set();$('review').hidden=true;$('keepLabel').hidden=true;$('progressBox').hidden=false;$('cancel').hidden=false;poll();}catch(error){notice(error.message,true);setBusy(false);}};
async function poll(){clearTimeout(polling);if(!job)return;try{const state=await api(`/api/job/${job.id}`);job=state;$('statusText').textContent=state.message;$('progress').value=state.progress;$('progress').max=state.count;
 if(['processing','exporting','learning','restoring'].includes(state.state)){polling=setTimeout(poll,650);return;}
 setBusy(false);$('cancel').hidden=true;
 if(state.state==='review'||state.state==='done'){
  results=state.results;previewed=new Set(results.map(r=>r.page));
  if(!keep.initialized){const saved=sessionStorage.getItem('keep-'+job.id);keep=new Set(saved?JSON.parse(saved):results.filter(r=>r.changed>0&&!r.uncertain).map(r=>r.page));keep.initialized=true;}
  revision++;$('review').hidden=false;$('step2').classList.remove('active');$('step3').classList.add('active');
  $('reviewSummary').textContent=`${results.filter(r=>r.changed>0).length} cleanup previews · ${results.filter(r=>r.structural&&r.matched).length} pages with PDF overlays removed · ${results.filter(r=>r.reviewed).length} reviewed matches · ${results.reduce((n,r)=>n+(r.automatic||0),0)} automatic word repairs · ${results.reduce((n,r)=>n+(r.inverted||0),0).toLocaleString()} PureScan pixels · ${results.filter(r=>r.uncertain).length} uncertain placements · ${results.filter(r=>!r.changed).length} unchanged`;
  const downloadable=state.state==='done'&&!sessionStorage.getItem('dirty-'+job.id);$('download').hidden=!downloadable;$('export').hidden=downloadable;$('download').href=`/api/download/${job.id}`;
  await loadPage();
 }else notice(state.message,state.state==='error');
 }catch(error){setBusy(false);notice(error.message,true);}}
function changedReview(){ sessionStorage.setItem('keep-'+job.id,JSON.stringify([...keep]));sessionStorage.setItem('dirty-'+job.id,'yes');$('download').hidden=true;$('export').hidden=false;updatePageResult();renderPageList(); }
function renderPageList(){syncSimpleView();const container=$('pageList');container.replaceChildren();for(const result of results){const chip=document.createElement('div');chip.className='page-chip'+(page===result.page?' current':'')+(!result.changed?' unchanged':'');const check=document.createElement('input');check.type='checkbox';check.checked=keep.has(result.page);check.disabled=!result.changed;check.setAttribute('aria-label',`Use cleaned page ${result.page+1}`);check.onchange=()=>{check.checked?keep.add(result.page):keep.delete(result.page);changedReview();};const button=document.createElement('button');button.textContent=String(result.page+1);button.title=result.changed?'Review cleaned page':'Original page retained';button.onclick=()=>{page=result.page;loadPage();};chip.append(check,button);container.append(chip);}}
$('keepPage').onchange=()=>{$('keepPage').checked?keep.add(page):keep.delete(page);changedReview();};
$('allOriginal').onclick=()=>{keep.clear();changedReview();};$('allCleaned').onclick=()=>{results.filter(r=>r.changed).forEach(r=>keep.add(r.page));changedReview();};
$('export').onclick=async()=>{try{if(typeof flushView==='function')await flushView();setBusy(true);await api(`/api/export/${job.id}`,{keep:[...keep]});sessionStorage.removeItem('dirty-'+job.id);$('progressBox').hidden=false;$('cancel').hidden=false;poll();}catch(error){notice(error.message,true);setBusy(false);}};
$('cancel').onclick=async()=>{try{await api(`/api/cancel/${job.id}`,{});$('statusText').textContent='Stopping after the current operation…';}catch(error){notice(error.message,true);}};
$('reset').onclick=async()=>{if(!job||busy)return;try{await api(`/api/clear/${job.id}`,{});location.reload();}catch(error){notice(error.message,true);}};
$('quit').onclick=async()=>{try{await api('/api/quit',{});clearTimeout(polling);document.body.replaceChildren();const message=document.createElement('p');message.textContent='ClearPage is closed. You can close this browser tab.';document.body.append(message);}catch(error){notice(error.message,true);}};
$('previous').onclick=()=>{if(page>0){page--;loadPage();}};$('next').onclick=()=>{if(page<job.count-1){page++;loadPage();}};
$('pageNumber').onchange=()=>{page=Math.max(0,Math.min(job.count-1,(parseInt($('pageNumber').value,10)||1)-1));loadPage();};
$('showMask').onchange=loadPage;$('zoom').onclick=()=>{const on=$('previewGrid').classList.toggle('zoomed');$('zoom').textContent=on?'Fit to screen':'Zoom in';};
$('file').onchange=()=>upload($('file').files[0]);
for(const event of ['dragover','dragenter'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('drag');});
$('dropzone').addEventListener('dragleave',()=>$('dropzone').classList.remove('drag'));
$('dropzone').addEventListener('drop',e=>{e.preventDefault();$('dropzone').classList.remove('drag');uploadFiles(e.dataTransfer.files);});
async function restoreSession(){
 try{
  const saved=await api('/api/session');if(!saved)return;job=saved;if(!job.options)$('method').value=job.overlays?.some(r=>r.overlays)?'overlay':'preset';
  $('welcome').hidden=true;$('workspace').hidden=false;$('controls').hidden=false;
  $('filename').textContent=job.name;$('filemeta').textContent=`${job.count} pages · ${(job.size/1024/1024).toFixed(1)} MB · Original retained`;
  $('inputNote').textContent=job.input_type&&job.input_type!=='PDF'?job.input_note:'';
  $('total').textContent=`of ${job.count}`;$('pageNumber').max=job.count;
  $('step1').classList.remove('active');$('step2').classList.add('active');
  if(job.options){$('fixNegative').checked=!!job.options.fix_negative;polarityPages=new Set(job.options.polarity_pages||[]);$('method').value=job.options.method;$('mode').value=job.options.mode==='strong'?'careful':job.options.mode;$('aiHelp').hidden=job.options.mode!=='lama';$('useReviewed').checked=job.options.use_reviewed!==false;$('autoRestore').checked=job.options.auto_restore!==false;$('scanInversion').checked=!!job.options.scan_inversion;strokes=job.options.strokes;const manual=job.options.method==='manual';$('brushTools').hidden=!manual;$('originalPaper').classList.toggle('painting',manual);$('paintLabel').textContent=manual?'PAINT THE WATERMARK':'SOURCE COPY';}
  if(job.options){$('modeHelp').textContent={lama:'Local LaMa AI reconstructs selected watermark pixels. Review generated letters before export.',careful:'Preserves detected text lines and connected strokes. Some watermark fragments may remain.',light:'Only cleans lighter pixels, preserving dark strokes and nearby edges.',strong:'Replaces every selected pixel. This can erase words, numbers, or signatures inside the selection. Review closely.'}[job.options.mode];if(job.options.method==='manual')$('methodHelp').textContent='Select an area yourself. Best for marks on blank backgrounds.';}
  if(['processing','exporting','learning','restoring','review','done'].includes(job.state)){$('progressBox').hidden=false;setBusy(true);poll();}else{setBusy(false);await loadPage();}
 }catch(error){notice(error.message,true);}
}
// workspace.js restores the complete multi-file workspace after all controls load.
$('useReviewed').onchange=invalidate;
$('importReviewed').onclick=()=>$('reviewedFile').click();
$('reviewedFile').onchange=async()=>{
 const file=$('reviewedFile').files[0];if(!file||!job||busy)return;
 if(file.size>200*1024*1024){notice('Maximum file size is 200 MB.',true);return;}
 setBusy(true);$('progressBox').hidden=false;$('statusText').textContent='Opening the reviewed copy…';
 try{await api('/api/reviewed/'+job.id,file,true);results=[];keep=new Set();previewed.clear();sessionStorage.removeItem('keep-'+job.id);$('review').hidden=true;$('keepLabel').hidden=true;$('cancel').hidden=false;poll();}catch(error){notice(error.message,true);setBusy(false);}finally{$('reviewedFile').value='';}
};
$('dropzone').tabIndex=0;$('dropzone').setAttribute('role','button');$('dropzone').setAttribute('aria-label','Open a PDF or scan');
$('dropzone').onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();$('file').click();}};

$('scanInversion').onchange=()=>{if($('scanInversion').checked&&$('method').value!=='preset'){$('scanInversion').checked=false;notice('Choose the Huntington preset to use PureScan.',true);return;}if($('scanInversion').checked&&$('mode').value==='lama'){$('mode').value='careful';$('mode').onchange();}invalidate();};

syncOverlayControls();

// Presentation only: request payloads, cleanup defaults and export selection are unchanged.
function syncSimpleView() {
 const reviewed=!$('review').hidden;
 document.body.classList.toggle('review-ready',reviewed);
 $('pageChoices').hidden=!reviewed;
 if(!busy&&(job?.state==='done'||(job?.state==='review'&&job.message==='Review the results, then save your complete PDF.')))$('progressBox').hidden=true;
 const method=$('method').value;
 $('simpleMethod').textContent=method==='huntington_background'?'Huntington steps: clean the background, refine text-line gaps, then restore matching words. Review remaining overlaps in Word repair tools; confirm exact wording before applying a repair.':method==='repeated_diagonal_ai'?'AI draft selected. Reconstructs detected diagonal overlaps locally. Check wording, numbers, signatures and barcodes; select each page and mark it reviewed before export.':method==='unofficial_copy'?'Gray Unofficial Copy cleanup selected. Check the preview, then select the pages to save.':method==='repeated_diagonal'?'Partial cleanup only: removes repeated diagonal lettering in blank areas. Marks crossing document content remain.':['dotted_document','dotted_map'].includes(method)?'Dotted scan cleanup selected. Try one page, check the details, then select the cleaned pages to save.':method==='polarity'?'Checks for white text on black paper. Review the proposed inversion before saving.':['overlay','object'].includes(method)?'PDF-object cleanup selected. Recognized watermarks are removed while preserving original text and images.':method==='manual'?'Paint the watermark on the Before page, then try the cleanup.':'Huntington cleanup selected. Change the PDF type above for other documents.';
 $('selectionSummary').textContent=job?`${keep.size} of ${job.count} pages selected`:'';
 $('saveTitle').textContent=!$('download').hidden?'Your PDF is ready':'Check the pages, then save';
 $('saveHelp').textContent=!$('download').hidden?'Download the new copy to your computer.':job?`All ${job.count} pages will be included. Only checked pages use the cleaned version.`:'All pages will be included.';
 $('step1').classList.toggle('active',!job);
 $('step2').classList.toggle('active',!!job&&!reviewed);
 $('step3').classList.toggle('active',reviewed);
 $('file').disabled=busy;
 $('compareReference').disabled=busy||!job||!['review','done'].includes(job.state)||!results.length;
 $('previewAgain').disabled=busy;$('cleanAgain').disabled=busy;
 updateOptionExamples();
}
function showResult(result) {
 showTechnicalResult(result);
 let message='Click “Try this page” to see a preview, or clean all pages.';
 let warning=false;
 if(result){
  if(result.color_correction?.applied){message='Colors corrected and cleanup ready. Check the text, then select this page to save the corrected version.';warning=true;}
  else if(result.polarity){message=result.polarity.note;warning=result.changed>0;}
  else if(result.structural)message=result.matched?'Watermark objects removed. Original text and images retained.':'No supported watermark found. This page is unchanged.';
  else if(result.reconstruction_unverified){message=result.pattern_note+' Select this page only after checking it, then click Mark page reviewed.';warning=true;}
  else if(result.partial){message=result.pattern_note;warning=true;}
  else if(result.uncertain){message=keep.has(page)?'You selected this cleaned page. Check the altered text carefully before saving.':'Check the text carefully. The original will be kept unless you select this cleaned page.';warning=true;}
  else if(!result.matched)message=result.repairs?'Word repairs applied. Check the wording before saving.':'No watermark match found. This page is unchanged.';
  else message='Cleanup ready. Check the words and numbers in the After page.';
  if(!result.uncertain&&result.changed&&results.some(r=>r.page===page)&&!keep.has(page))message='The saved PDF will keep the original page. Check the box to use this cleaned preview.';
 } else if(previewed.has(page)) message='Preview ready. Compare Before and After, then clean all pages.';
 $('simpleResult').textContent=message;
 $('simpleResult').classList.toggle('needs-review',warning);
}
syncSimpleView();
$('previewAgain').onclick=()=>$('preview').click();
$('cleanAgain').onclick=()=>$('process').click();

// Help text only. These examples never change the selected settings.
function updateOptionExamples() {
 const method=$('method').value;
 const types={
  huntington_background:'Use for CH search packages with the diagonal IMAGES / COPY PROPERTY Huntington watermark embedded in scan pixels. Matches the known phrase, clears gaps between text rows and protects local text, punctuation and graphic strokes. Separable watermark portions are removed; merged lettering remains. Preview and review before selecting pages.',
  repeated_diagonal_ai:'Use for repeated diagonal lettering crossing text when you accept AI reconstruction. Example: the repeated Printing or saving scan. LaMa runs locally and may change or omit original details. Try one page first. This is a generated draft, not a verified original; complete removal is not guaranteed.',
  unofficial_copy:'Use for the large gray diagonal “Unofficial Copy” on supported Warren County scans. Example: 117.pdf, POA.pdf, or 26NTNY28709_PLAT MAP.pdf. Matches supported portrait documents and landscape maps. Check words and fine map lines before saving.',
  repeated_diagonal:'Partial cleanup for repeated small diagonal lettering, such as “Printing or saving” across a scan. Removes matching lettering in blank areas only. Text, signatures and barcodes in the detected content rectangle stay unchanged, so overlapping watermark remains. Not a fully clean copy.',
  dotted_document:'Use for black-and-white scanned search packages with a dotted diagonal “Unofficial Document” mark through the center. Example: the 74-page Search Package. Keeps original scan resolution. Try one page and check small letters and punctuation before selecting cleaned pages.',
  dotted_map:'Use for large landscape plat maps with a dotted diagonal “Unofficial Document” mark through the center. Example: the Eastampton Farms PlatMap. Preserves native map resolution and connected boundary lines. Check lot numbers, symbols and measurements before selecting cleaned pages.',
  polarity:'Use when the whole scanned page has white text on a black background. The app looks for dark paper with small light marks. It reverses the existing colors without retyping words. Dark photos, mixed layouts and uncertain pages may need the per-page override. All proposed inversions need your review.',
  object:'First open Select a text watermark and find the objects on this page. Click the watermark or choose its wording. Preview, then accept only the pages you checked.',
  overlay:'Use for the supported pink repeated “www.apdf.com” or large blue diagonal “Sample” style, and supported Cook County / OKCountyRecords PDFs. Removes the separate watermark objects while preserving original text and images. Other wording or styles stay unchanged.',
  preset:'Use for Huntington County scans with the diagonal “IMAGES/COPY PROPERTY…” mark. Example: large diagonal words crossing a scanned deed. Try Careful first and check the words beneath the mark.',
  manual:'Use for another watermark that you can paint over yourself. Example: a small mark in an empty corner. Select only the watermark on the Before page. Marks covering text are harder to remove, and the same selection is applied to every page.'
 };
 const modes={
  careful:'First choice for scanned watermarks near text. Example: a diagonal mark touches an address. Careful tries to protect the existing letters, so some watermark fragments may remain.',
  light:'Use for a faint watermark near dark text. Example: pale grey lettering behind black print. Light protects dark strokes; it will not remove a solid black watermark.',
  lama:'Try when image cleanup still leaves a mark and you can check the text against a reliable copy. Example: a dark line crosses “mortgage”. AI fills in the selected area, but may change letters. Review carefully and select only pages you accept.'
 };
 $('methodHelp').textContent=types[method]||'';
 $('modeHelp').textContent=method==='polarity'?'This option reverses page colors only. Watermark cleanup and word repairs are disabled for this pass.':['overlay','object'].includes(method)?'No cleanup method is needed for Website PDF. The app removes the separate overlay, so these image-cleanup choices are disabled.':modes[$('mode').value]||'';
 if(['dotted_document','dotted_map'].includes(method))$('modeHelp').textContent='Removes isolated dots in the supported diagonal layout. Connected strokes are retained. Dots touching text may remain. AI repair and color inversion are disabled for this pass.';
 if(method==='huntington_background')$('modeHelp').textContent='Runs bounded Huntington cleanup passes until no more protected pixels qualify. Enable matching-word restoration for intact scanned donors, or use Word repair tools to confirm wording. Hidden letters cannot be recovered by repeated erasing.';
 if(method==='unofficial_copy')$('modeHelp').textContent='Uses the matching gray-watermark template. Dark original strokes are retained; generated word repairs and color inversion are disabled.';
 if(method==='repeated_diagonal')$('modeHelp').textContent='Protects the document-content rectangle. Only matched lettering outside it is cleaned. Review the partial result before selecting a page.';
 if(method==='repeated_diagonal_ai')$('modeHelp').textContent='Local LaMa reconstructs ink inside detected diagonal bands, including original strokes at intersections. Outside-mask pixels are preserved. Select the pages you accept and click Mark page reviewed on each before creating the PDF.';
 const repairs={
  donor:'Best when the same word is clear elsewhere. Example: “mortgage” is damaged on page 4 but clear on page 2. Copy that clean occurrence, matching spelling, capital letters and font.',
  fit:'Use when some original letters survive and you have confirmed the exact word. Example: part of “mortgage” is missing, but its remaining strokes are visible. The app fits replacement letters to those strokes; check the preview.',
  text:'Use when you have confirmed the exact wording from a reliable copy and cannot find a clean occurrence. Example: type the verified word “mortgage”, choose a matching font, and preview. The app uses what you type; it does not verify it.',
  lama:'Use to preview AI repair of a damaged letter shape. Example: a watermark breaks a letter in “mortgage”. Compare with a reliable copy before applying. AI may invent a stroke; it does not guarantee the word you entered.'
 };
 $('repairChoiceHelp').textContent=repairs[$('repairMethod').value]||'';
}
for(const id of ['method','mode','repairMethod'])$(id).addEventListener('change',updateOptionExamples);
updateOptionExamples();

$('forcePolarity').onchange=()=>{$('forcePolarity').checked?polarityPages.add(page):polarityPages.delete(page);invalidate();};
function clearQuality(){qualityReport=null;$('qualityResult').textContent='';$('saveQuality').hidden=true;}
$('compareReference').onclick=()=>$('referenceFile').click();
$('referenceFile').onchange=async()=>{
 const file=$('referenceFile').files[0];if(!file||!job||busy)return;
 if(file.size>200*1024*1024){notice('Maximum file size is 200 MB.',true);return;}
 clearQuality();setBusy(true);
 try{
  qualityReport=await api(`/api/compare/${job.id}/${page}`,file,true);
  const r=qualityReport, val=n=>n===null?'not applicable':n+'%';
  $('qualityResult').textContent=`Page ${r.page} · Pixel error in areas needing correction: ${r.correction_error_before??'N/A'} before → ${r.correction_error_after??'N/A'} after (0–255; lower is better). Matching original pixels preserved: ${val(r.preserved_reference_matching_pixels_percent)}. Reference dark pixels retained: ${val(r.dark_pixel_recall_percent)}. Output dark pixels matching reference: ${val(r.dark_pixel_precision_percent)}. Watermark pixels corrected: ${val(r.corrected_watermark_pixels_percent)}. Faint reference ink matched: ${val(r.faint_ink_pixel_match_percent)}. These are pixel measurements, not word accuracy. Alignment and reference wording must be checked by you.`;
  $('saveQuality').hidden=false;
 }catch(error){notice(error.message,true);}finally{setBusy(false);$('referenceFile').value='';}
};
$('saveQuality').onclick=()=>{if(!qualityReport)return;const url=URL.createObjectURL(new Blob([JSON.stringify(qualityReport,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='clearpage-quality-page-'+qualityReport.page+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};

$('fixNegative').onchange=()=>{syncOverlayControls();invalidate();};
