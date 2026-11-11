import React, { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, CircleHelp, FileText, LoaderCircle, LockKeyhole, Maximize2, Plus, ScanLine, ShieldCheck, Upload, WandSparkles, X, ZoomIn, ZoomOut } from 'lucide-react';
import './styles.css';
import HuntingtonReview from './HuntingtonReview';
import CleanupProgress from './CleanupProgress';
import PdfReview from './PdfReview';
import useCtrlWheelZoom from './useCtrlWheelZoom';
import BillingDialog from './BillingDialog';
import AdminDialog from './AdminDialog';
import GeminiTrial from './GeminiTrial';
import CleanupSuggestions from './CleanupSuggestions';
import CompressionDialog from './CompressionDialog';

const presets = [
  { id: 'lake_outline', title: 'Lake County · hollow UNOFFICIAL COPY', description: 'Choose this when thin outlines spell UNOFFICIAL COPY from the upper left to the lower right. Preview where a letter crosses handwriting.', image: 'lake-outline', tag: 'County scans' },
  { id: 'henry_red', title: 'Henry County · red diagonal stamp', description: 'Use for Henry County scans with a red “NOT AN OFFICIAL COPY” stamp running from bottom left to top right. Include at least two different stamped pages in the same PDF.', image: 'henry-red', tag: 'County scans' },
  { id: 'union_parish', title: 'Union Parish · purple UNOFFICIAL', description: 'Choose this when purple UNOFFICIAL lettering appears at the top, middle, and bottom of the page. It also cleans the small repeated gray codes.', image: 'union-parish', tag: 'Parish scans' },
  { id: 'hocking_outline', title: 'Hocking County outline', description: 'Choose this when a diagonal line reads “HOCKING COUNTY RECORDER THIS IS AN UNOFFICIAL COPY”. Preview where it crosses words or signatures.', image: 'hocking-outline', tag: 'County scans' },
  { id: 'licensed_diagonal', title: 'Diagonal licensing text', description: 'One diagonal line beginning “LICENSED TO FIRST AMERICAN DATA TREE LLC”. For matching search-package scans. Review words and signatures where the line crosses them.', image: 'licensed-diagonal', tag: 'Search packages' },
  { id: 'kane_outline', title: 'Kane County · dotted outline', description: 'One large diagonal “Unofficial” word drawn with a dotted outline, as in Kane County scans. Review tiny crossings with text and signatures.', image: 'kane-outline', tag: 'County scans' },
  { id: 'screened_unofficial', title: 'Dotted Un-Official', description: 'One diagonal dotted “Un-Official” mark across a scan. Removes the pattern and repairs damaged strokes. Review text and signatures.', image: 'screened-unofficial', tag: 'Scanned documents' },
  { id: 'repeated_copy', title: 'Small repeated copy', description: 'Small gray copy words repeated across the scan. Review dark areas after cleanup.', image: 'repeated-copy', tag: 'Scans' },
  { id: 'flat_gray_copy', title: 'St. Tammany / Gallia · gray watermark', description: 'Use for the diagonal St. Tammany “UNOFFICIAL” stamp or repeated Gallia County “Unofficial Copy” marks. Keeps original black pixels.', image: 'flat-gray', tag: 'Black ink scans' },
  { id: 'cumberland_red', title: 'Cumberland red watermark', description: 'Refines faint traces from the pale red diagonal Cumberland stamp while protecting dark ink.', image: 'red-copy', tag: 'County scans' },
  { id: 'red_copy', title: 'Red “Not an Official Copy”', description: 'Pale red diagonal lettering on black-and-white scans. No AI needed.', image: 'red-copy', tag: 'Red marks' },
  { id: 'gemini_draft', title: 'Gemini AI draft · cloud', description: 'Try one difficult page with your Gemini key. Requires review.', image: 'repeated-diagonal', tag: 'Optional cloud' },
  { id: 'dotted_document', title: 'Dotted document / search package', description: 'Diagonal dotted lettering on scanned documents, including supported grayscale search packages.', image: 'dotted-document', tag: 'Documents' },
  { id: 'dotted_map', title: 'Maps & surveys', description: 'Dotted lettering across plats and survey maps.', image: 'dotted-map', tag: 'Maps' },
  { id: 'unofficial_copy', title: 'Unofficial copy', description: 'Gray “Unofficial Copy” marks. Adjusts to each supported page’s size and angle.', image: 'unofficial-copy', tag: 'Scans' },
  { id: 'nassau_unofficial', title: 'Nassau · Unofficial Copy', description: 'Pale diagonal lettering with a repeated square border. Review faint text, signatures and intersections after cleanup.', image: 'nassau', tag: 'County scans' },
  { id: 'preble_unofficial', title: 'Preble County · Unofficial Copy', description: 'Choose this for gray UNOFFICIAL COPY lettering that rises from left to right on a Preble County scan. It may look dotted when you zoom in.', image: 'preble', tag: 'County scans' },
  { id: 'overlay', title: 'Text & website marks', description: 'Choose this for a separate PDF stamp, such as large gray UNOFFICIAL letters over a scanned recorder page. Also supports Un-Official on maps.', image: 'overlay', tag: 'PDF overlays' },
  { id: 'preset', title: 'Huntington County', description: 'The county watermark across scanned text.', image: 'huntington', tag: 'County scans' },
  { id: 'huntington_background', title: 'Huntington - step by step', description: 'Refine cleanup, then repair confirmed words.', image: 'huntington', tag: 'Text review' },
  { id: 'repeated_document', title: 'Repeated diagonal', description: 'Clean margins and text gaps. Overlapping ink stays protected.', image: 'repeated-diagonal', tag: 'Scans' },
  { id: 'repeated_diagonal', title: 'Repeated diagonal · margins only', description: 'Clean supported repeated lettering outside the document content area. Lettering within that area stays visible.', image: 'repeated-diagonal', tag: 'Scans' },
  { id: 'repeated_diagonal_ai', title: 'Repeated diagonal · local AI', description: 'Full cleanup, with supported original strokes restored afterward. Review the AI draft.', image: 'repeated-diagonal', tag: 'AI draft' },
  { id: 'polarity', title: 'Dark page to white', description: 'Turn negative scans into readable white pages.', image: 'polarity', tag: 'Restoration' },
];
const steps = ['Upload file', 'Preview & clean', 'Review & export'];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function request(path, body, filename) {
  const options = body === undefined ? {} : {
    method: 'POST',
    headers: { 'X-ClearPage': 'local', 'Content-Type': filename ? (body.type || 'application/octet-stream') : 'application/json', ...(filename ? { 'X-Filename': encodeURIComponent(filename) } : {}) },
    body: filename ? body : JSON.stringify(body),
  };
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error('The connection to ClearPage was interrupted. Make sure the app is running, then try again.');
  }
  const data = await response.json().catch(() => {
    throw new Error('The local PDF service did not respond correctly. Restart the app and try again.');
  });
  if (!response.ok) { const failure = new Error(data.error || 'Something went wrong. Please try again.'); failure.code = data.code; throw failure; }
  return data;
}

function Button({ children, variant = 'primary', className = '', ...props }) {
  return <button className={`button button-${variant} ${className}`} {...props}>{children}</button>;
}

function Brand() {
  return <span className="brand"><span className="brand-symbol" aria-hidden="true">▤</span><span><span className="brand-accent">clear</span>page<span className="brand-accent">.</span></span></span>;
}

function HelpDialog({ onClose }) {
  const closeRef = useRef(null);
  useEffect(() => { const previous = document.activeElement; closeRef.current?.focus(); return () => previous?.focus(); }, []);
  return <div className="modal-backdrop" onClick={onClose}><section className="help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-title" onClick={event => event.stopPropagation()} onKeyDown={event => {
    if (event.key === 'Tab') { event.preventDefault(); closeRef.current?.focus(); }
  }}><button ref={closeRef} className="icon-button close-help" aria-label="Close guide" onClick={onClose}><X size={20} /></button>
    <span className="eyebrow">A LITTLE GUIDANCE</span><h2 id="help-title">From cluttered to clear.</h2>
    {[
      ['Add your document', 'Choose one PDF, JPG, PNG or TIFF up to 200 MB. Standard presets run on your computer. The optional Gemini draft sends one page to Google with your confirmation. Scans are exported as a PDF.'],
      ['Choose a matching preset', 'Select the example closest to your watermark. Preview the first page before cleaning the full document.'],
      ['Review before you export', 'Compare original and cleaned pages. Selected pages use the cleaned result; unchecked pages keep the original. Every page stays in your PDF.'],
    ].map(([title, body], index) => <div className="help-step" key={title}><span>{index + 1}</span><div><h3>{title}</h3><p>{body}</p></div></div>)}
    <div className="note"><ShieldCheck size={20} /><p>Flattened marks can overlap original text. Check the results carefully, especially names and numbers.</p></div>
  </section></div>;
}

function PdfTypeDialog({ item, selected, onSelect, onClose }) {
  const dialogRef = useRef(null);
  useEffect(() => { const previous = document.activeElement; dialogRef.current?.showModal(); return () => previous?.focus(); }, []);
  return <dialog ref={dialogRef} className="pdf-type-dialog" aria-labelledby="pdf-type-title" onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose(); }}><div className="pdf-type-dialog-content">
    <button autoFocus className="icon-button close-help" aria-label="Close PDF type" onClick={onClose}><X size={20} /></button>
    <div className="pdf-type-example"><img src={`/static/type-${item.image}.${['polarity','red-copy','flat-gray','repeated-copy','nassau','screened-unofficial','kane-outline','licensed-diagonal','hocking-outline'].includes(item.image) ? 'svg' : 'webp'}`} alt={`${item.title} example`} /></div>
    <div className="pdf-type-details"><span className="eyebrow">{item.tag}</span><h2 id="pdf-type-title">{item.title}</h2><p>{item.description}</p><Button onClick={() => { onSelect(item.id); onClose(); }}>{selected ? <Check size={17} /> : <ArrowRight size={17} />}{selected ? 'Keep this PDF type' : 'Use this PDF type'}</Button></div>
  </div></dialog>;
}

export default function App() {
  const [view, setView] = useState('upload');
  const [billing, setBilling] = useState(null);
  const [showBilling, setShowBilling] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [showCompression, setShowCompression] = useState(false);
  const [updateInfo, setUpdateInfo] = useState(null);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [dismissedUpdate, setDismissedUpdate] = useState('');
  const updateRequest = useRef(false);
  async function checkUpdates(manual = true) {
    if (updateRequest.current) return;
    updateRequest.current = true;
    if (manual) setCheckingUpdate(true);
    try {
      const result = await request('/api/update-status');
      if (manual || result.available) setUpdateInfo(result);
      if (manual) setDismissedUpdate('');
    } catch {
      if (manual) setUpdateInfo({ error: true, message: 'Could not check for updates. Try again when connected.' });
    } finally { updateRequest.current = false; setCheckingUpdate(false); }
  }
  useEffect(() => {
    const timer = setTimeout(() => checkUpdates(false), 4000);
    return () => clearTimeout(timer);
  }, []);
  const [job, setJob] = useState(null);
  const [method, setMethod] = useState('dotted_document');
  const [suggestions, setSuggestions] = useState({ loading: false, data: null, error: false });
  const [showAllTypes, setShowAllTypes] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [help, setHelp] = useState(false);
  const [pdfType, setPdfType] = useState(null);
  const [page, setPage] = useState(0);
  const [revision, setRevision] = useState(0);
  const [previewed, setPreviewed] = useState(new Set());
  const [results, setResults] = useState([]);
  const [huntingtonPreview, setHuntingtonPreview] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [reviewedAI, setReviewedAI] = useState(new Set());
  const [progress, setProgress] = useState(null);
  const [cleanupProgress, setCleanupProgress] = useState(null);
  const [zoom, setZoom] = useState(100);
  const [downloaded, setDownloaded] = useState(false);
  const [previewError, setPreviewError] = useState(false);
  const [localAI, setLocalAI] = useState({ ready: false, message: 'Checking local AI availability…' });
  const fileRef = useRef(null);
  const uploadLock = useRef(false);
  const titleRef = useRef(null);
  const canvasRef = useRef(null);
  useCtrlWheelZoom(canvasRef, direction => setZoom(value => Math.max(50, Math.min(200, value + direction * 25))), view === 'workspace' && !!job);
  const stage = job && view === 'review' ? 2 : job && view === 'workspace' ? 1 : 0;
  async function refreshBilling() {
    const next = await request('/api/billing'); setBilling(next); return next;
  }
  useEffect(() => { refreshBilling().catch(() => {}); }, []);
  useEffect(() => {
    if (!billing?.admin) return;
    const timer=setInterval(()=>refreshBilling().catch(()=>{}),60000);
    return ()=>clearInterval(timer);
  }, [billing?.admin]);

  useEffect(() => {
    const handleKey = event => { if (event.key === 'Escape') { if (pdfType || showCompression) return; else if (help) setHelp(false); else if (!busy && view !== 'upload') setView(view === 'review' ? 'workspace' : 'upload'); } };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [help, pdfType, showCompression, busy, view]);
  useEffect(() => { titleRef.current?.focus({ preventScroll: true }); }, [view]);
  useEffect(() => {
    if (method !== 'repeated_diagonal_ai') return;
    let active = true;
    setLocalAI({ ready: false, message: 'Checking local AI availability…' });
    request('/api/local-ai').then(data => { if (active) setLocalAI(data); }).catch(failure => {
      if (active) setLocalAI({ ready: false, message: failure.message });
    });
    return () => { active = false; };
  }, [method]);
  useEffect(() => {
    // A refresh or hot reload must not hide a cleanup already running locally.
    let active = true;
    async function reconnect() {
      let current;
      try { current = await request('/api/session'); } catch { return; }
      if (active && !uploadLock.current && current?.state === 'exporting' && current.compression?.status === 'processing') {
        setJob(current); setView('workspace'); setShowCompression(true); return;
      }
      if (!active || uploadLock.current || current?.state !== 'processing') return;
      setJob(current); setMethod(current.options?.method || 'dotted_document');
      setView('workspace'); begin(); updateCleanupProgress(current);
      try {
        const finished = await waitForJob('review', updateCleanupProgress, current.id, () => active);
        if (finished) finishCleanup(finished);
      } catch (failure) {
        if (active) {
          setError(failure.message);
          setCleanupProgress(previous => ({ ...previous, status: 'error', message: failure.message }));
        }
      } finally { if (active) setBusy(false); }
    }
    reconnect();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!job?.id) return;
    let active = true;
    setSuggestions({ loading: true, data: null, error: false });
    setShowAllTypes(false);
    request(`/api/suggestions/${job.id}`).then(data => {
      if (!active) return;
      setSuggestions({ loading: false, data, error: false });
      if (!data.items?.length) setShowAllTypes(true);
    }).catch(() => {
      if (active) { setSuggestions({ loading: false, data: null, error: true }); setShowAllTypes(true); }
    });
    return () => { active = false; };
  }, [job?.id]);

  function begin() { setBusy(true); setError(''); setProgress(null); setDownloaded(false); }
  function choosePreset(id) {
    if (busy || method === id) return;
    setReviewedAI(new Set()); setMethod(id); setHuntingtonPreview(null); setResults([]); setPreviewed(new Set()); setSelected(new Set()); setDownloaded(false); setError(''); setMessage(id === 'gemini_draft' ? 'Enter your Gemini key below to try the selected page. The cloud trial is optional.' : 'Preset updated. Preview the first page to check the result.');
  }
  function settings() { return { method, mode: 'careful', strokes: [], page: 0, use_reviewed: false, auto_restore: false, scan_inversion: false, fix_negative: false, polarity_pages: [] }; }
  async function upload(file) {
    if (!file || busy || uploadLock.current) return;
    setError('');
    if (!/\.(pdf|jpe?g|png|tiff?)$/i.test(file.name)) { setError('Please choose a PDF, JPG, PNG or TIFF file.'); return; }
    if (file.size > 200 * 1024 * 1024) { setError('Please choose a file under 200 MB.'); return; }
    uploadLock.current = true; begin(); setMessage('Opening your document…');
    try {
      const previous = job;
      const next = await request('/api/upload', file, file.name);
      await refreshBilling().catch(() => {});
      setJob({ ...next, name: file.name, size: file.size }); setHuntingtonPreview(null); setPage(0); setRevision(value => value + 1); setPreviewed(new Set()); setResults([]); setSelected(new Set()); setZoom(100); setPreviewError(false);
      setView('workspace'); setMessage('Choose the preset that matches your document, then preview the first page.');
      if (previous) await request(`/api/clear/${previous.id}`, {}).catch(() => {});
    } catch (failure) { setError(failure.message); setMessage(''); if (failure.code === 'license_required') { await refreshBilling().catch(() => {}); setShowBilling(true); } }
    finally { setBusy(false); uploadLock.current = false; if (fileRef.current) fileRef.current.value = ''; }
  }
  async function waitForJob(expected, onProgress, identifier = job.id, isCurrent = () => true) {
    for (;;) {
      if (!isCurrent()) return null;
      const state = await request(`/api/job/${identifier}`);
      if (!isCurrent()) return null;
      onProgress?.(state);
      setProgress(state.count ? Math.min(100, Math.round((state.progress || 0) / state.count * 100)) : null);
      if (['processing', 'exporting', 'learning', 'restoring'].includes(state.state)) { setMessage(state.message || 'Working on your document…'); await pause(600); continue; }
      if (state.state !== expected) throw new Error(state.message || 'The operation could not be completed. Please try again.');
      setJob(previous => ({ ...previous, ...state })); return state;
    }
  }
  function updateCleanupProgress(current) {
    const total = current.count;
    const completed = Math.min(total, Math.max(0, current.results?.length ?? current.progress ?? 0));
    setCleanupProgress({ completed, total, status: 'running', message: current.message || '' });
  }
  function finishCleanup(state) {
    const next = state.results || [];
    const cleaned = next.filter(item => item.changed);
    const selectedPages = cleaned.filter(item => !item.uncertain || ((item.native_search_package || item.native_tammany || item.native_kane) && item.matched));
    setReviewedAI(new Set()); setResults(next); setPreviewed(new Set(next.map(item => item.page))); setSelected(new Set(selectedPages.map(item => item.page))); setPage(0); setRevision(value => value + 1); setPreviewError(false);
    if (cleaned.length === 0) {
      setMessage('No supported watermarks found. Try another preset or export the original pages.');
    } else if (selectedPages.length === cleaned.length) {
      setMessage('All cleaned pages are selected. Review them before downloading.');
    } else {
      setMessage('Cleanup complete. Review your pages and choose which cleaned results to keep. Some pages start unchecked.');
    }
    setCleanupProgress({ completed: state.count, total: state.count, status: 'complete', message: '' });
  }
  async function preview() {
    if (busy || !job) return;
    begin(); setMessage('Creating a preview of the first page…');
    try {
      const result = await request(`/api/preview/${job.id}`, settings());
      setHuntingtonPreview(result);
      setResults([]); setSelected(new Set()); setPage(0); setRevision(value => value + 1); setPreviewed(new Set([0])); setPreviewError(false);
      setMessage(result.changed ? 'Your preview is ready. Compare both pages, then clean the full PDF.' : 'No supported watermark was found on page 1. Try another preset, or check the full PDF.');
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  async function cleanAll() {
    if (busy || !job) return;
    begin(); setMessage('Cleaning your document…'); setHuntingtonPreview(null); setResults([]); setSelected(new Set());
    setCleanupProgress({ completed: 0, total: job.count, status: 'running', message: '' });
    try {
      await request(`/api/process/${job.id}`, settings());
      const state = await waitForJob('review', updateCleanupProgress);
      finishCleanup(state);
    } catch (failure) {
      setError(failure.message);
      setCleanupProgress(previous => ({ ...previous, status: 'error', message: failure.message }));
    }
    finally { setBusy(false); }
  }
  async function geminiTrial() {
    if (busy || !job) return;
    const trialPage = page;
    begin(); setMessage('Preparing the Gemini page trial…');
    try {
      await request(`/api/gemini/trial/${job.id}`, { page: trialPage, consent: true });
      setHuntingtonPreview(null); setResults([]); setSelected(new Set()); setReviewedAI(new Set()); setPreviewed(new Set());
      const state = await waitForJob('review');
      finishCleanup(state); setCleanupProgress(null); setPage(trialPage); setMessage(state.message);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  async function download() {
    if (busy || !job || !results.length) return;
    begin(); setMessage('Preparing your PDF…');
    try {
      await request(`/api/view/${job.id}`, { page, keep: [...selected], reviewed: [...reviewedAI] });
      await request(`/api/export/${job.id}`, { keep: [...selected] }); await waitForJob('done');
      const link = document.createElement('a'); link.href = `/api/download/${job.id}`; link.download = ''; document.body.append(link); link.click(); link.remove();
      setDownloaded(true); setMessage('Your PDF is ready. The download has started.');
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  function togglePage(index) { setSelected(previous => { const next = new Set(previous); next.has(index) ? next.delete(index) : next.add(index); return next; }); }
  function navigate(next) { if (!busy) { setView(next); setError(''); } }
  const comparing = previewed.has(page);
  const pageDetails = results.find(item => item.page === page) || (comparing && page === 0 ? huntingtonPreview : null);

  return <div className={`app-shell ${view === 'review' ? 'review-mode' : view === 'workspace' ? 'workspace-mode' : ''}`}>
    <div className="main-shell">
      <header className="navbar">
        <a className="brand-link" href="/" onClick={event => { event.preventDefault(); navigate('upload'); }} aria-label="ClearPage home"><Brand /></a>
        {view !== 'review' && <div className="stepper" aria-label="Cleanup progress">{steps.map((label, index) => <React.Fragment key={label}>{index > 0 && <span className={`step-line ${stage >= index ? 'complete' : ''}`} />}<span className={`step ${stage === index ? 'current' : stage > index ? 'done' : ''}`} aria-current={stage === index ? 'step' : undefined}><b>{stage > index ? <Check size={12} /> : `0${index + 1}`}</b><span>{label}</span></span></React.Fragment>)}</div>}
        {job && view !== 'upload' && <Button variant="secondary" disabled={busy} onClick={() => setShowCompression(true)}>Compress PDF</Button>}
        <button type="button" className="button button-secondary update-trigger" disabled={checkingUpdate} onClick={() => checkUpdates(true)}>{checkingUpdate ? 'Checking updates…' : 'Check for updates'}</button>
      </header>
      <main className={view === 'review' ? 'main-content review-content' : view === 'workspace' ? 'main-content workspace-content' : 'main-content'}>
          {updateInfo && (!updateInfo.available || dismissedUpdate !== updateInfo.version) && <aside className={`update-notice ${updateInfo.available ? 'update-ready' : ''}`} role="status">
            <div><strong>{updateInfo.message}</strong>{updateInfo.available && <p>Download the new ZIP, close ClearPage and extract it into a new folder. Keep your existing app profile so your online account access is preserved.</p>}</div>
            {updateInfo.available && <div className="update-links"><a className="button button-primary" href={updateInfo.download_url} target="_blank" rel="noopener noreferrer">Download {updateInfo.version} ↗</a><a href={updateInfo.url} target="_blank" rel="noopener noreferrer">Release notes</a></div>}
            <button className="icon-button" aria-label="Dismiss update notice" onClick={() => { if (updateInfo.available) setDismissedUpdate(updateInfo.version); else setUpdateInfo(null); }}><X size={17} /></button>
          </aside>}
        <input ref={fileRef} type="file" accept="application/pdf,.pdf" aria-label="Choose a PDF" className="sr-only" disabled={busy} onChange={event => upload(event.target.files?.[0])} />

        {view === 'upload' && <>
          {billing?.admin ? <div className="billing-upload-note admin-active" role="status"><strong>Admin access is active.</strong> Documents do not use paid credits during this verified session. <button className="text-button" disabled={busy} onClick={()=>setShowAdmin(true)}>Manage admin session</button></div> : billing?.enabled && <div className="billing-upload-note"><strong>₹999 for two documents.</strong> One credit per distinct PDF. Reopen and download the same file again at no extra charge. <button className="text-button" disabled={busy} onClick={() => setShowBilling(true)}>Buy / activate</button> {billing.payment_provider === 'razorpay' && <button className="text-button" disabled={busy} onClick={() => setShowAdmin(true)}>Admin login</button>}</div>}
          <p className="upload-tool-note">Upload a PDF for watermark cleanup or lossless compression with a 25 MB target.</p>
          <div className="upload-layout"><section className="upload-card"><div className="card-topline"><span><Upload size={16} />ADD YOUR DOCUMENT</span><span className="file-type">.PDF</span></div>
            <button disabled={busy} className={`dropzone ${dragging ? 'dragging' : ''}`} onClick={() => fileRef.current?.click()} onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files.length > 1) setError('Please add one PDF at a time.'); else upload(event.dataTransfer.files[0]); }}>
              <span className="upload-art"><span className="upload-art-back" /><span className="upload-art-front"><FileText size={37} strokeWidth={1.1} /></span><span className="upload-art-plus"><Plus size={16} /></span></span>
              <h2>{busy ? 'Opening your document…' : dragging ? 'Drop it here. We’ve got it.' : 'Good things start with a drop.'}</h2><p>Drag your PDF or scan here, or browse to upload</p><span className="button button-primary">{busy ? <LoaderCircle size={16} className="animate-spin" /> : <Plus size={17} />}Choose a file<ArrowRight size={16} /></span><small>PDF · JPG · PNG · TIFF <span>·</span> Up to 200 MB</small>
            </button><div className="upload-card-footer"><LockKeyhole size={14} /><span>Your files stay yours. Your original stays untouched.</span></div>
          </section>
          </div>
          {(error || busy) && <div role={error ? 'alert' : 'status'} className={`status-banner ${error ? 'error' : ''}`}>{busy && <LoaderCircle size={17} className="animate-spin" />}{error || message}</div>}
          {job && <div className="resume-bar"><FileText size={18} /><span>Your document <strong>{job.name}</strong> is still open.</span><button className="text-button" onClick={() => navigate(results.length ? 'review' : 'workspace')}>Continue editing<ArrowRight size={16} /></button></div>}

        </>}


        {view === 'workspace' && job && <>
          <div className="editor-heading"><div className="editor-file"><button className="icon-button" aria-label="Back to upload" disabled={busy} onClick={() => navigate('upload')}><ArrowLeft size={18} /></button><FileText size={22} /><div><h1 ref={titleRef} tabIndex={-1}>{job.name}</h1><p>{job.count} {job.count === 1 ? 'page' : 'pages'}<span>·</span>{(job.size / 1024 / 1024).toFixed(1)} MB<span>·</span><LockKeyhole size={12} />Local document</p></div></div><Button variant="secondary" disabled={busy} onClick={() => fileRef.current?.click()}><Plus size={16} />New PDF</Button></div>
          <div className={`editor-layout ${!comparing ? 'initial-preview' : ''}`}><section className="document-viewer" aria-label="Document preview"><div className="viewer-toolbar"><span><span className="live-dot" />{comparing ? 'Compare your result' : 'Original document'}</span><div className="zoom-controls"><button className="icon-button" aria-label="Zoom out" disabled={zoom <= 50} onClick={() => setZoom(value => value - 25)}><ZoomOut size={16} /></button><span>{zoom}%</span><button className="icon-button" aria-label="Zoom in" disabled={zoom >= 200} onClick={() => setZoom(value => value + 25)}><ZoomIn size={16} /></button><button className="icon-button" aria-label="Reset zoom" onClick={() => setZoom(100)}><Maximize2 size={15} /></button></div></div>
            <div ref={canvasRef} title="Hold Ctrl and scroll to zoom" className={`page-canvas ${comparing ? 'comparing' : ''} zoom-${zoom}`}>
              <figure className="comparison-before"><figcaption><strong>{comparing ? 'Before' : 'Original'}</strong><span>Original PDF</span></figcaption><div className="page-image"><img src={`/api/page/${job.id}/${page}/before?v=${revision}`} alt={`Original document page ${page + 1}`} onError={() => setPreviewError(true)} /></div></figure>
              {comparing && <figure className="comparison-after"><figcaption><strong>After</strong><span><Check size={13} />Cleaned preview</span></figcaption><div className="page-image"><img src={`/api/page/${job.id}/${page}/after?v=${revision}`} alt={`Cleaned document page ${page + 1}`} onError={() => setPreviewError(true)} /></div></figure>}
            </div>
            {previewError && <div role="alert" className="status-banner error">Could not load the page preview.<button className="text-button" onClick={() => { setPreviewError(false); setRevision(value => value + 1); }}>Retry</button></div>}
            <div className="page-navigation"><button className="icon-button" aria-label="Previous page" disabled={busy || page === 0} onClick={() => { setPage(value => value - 1); setPreviewError(false); }}><ChevronLeft size={17} /></button><label>Page <select aria-label="Preview page" value={page} disabled={busy} onChange={event => { setPage(Number(event.target.value)); setPreviewError(false); }}>{Array.from({ length: job.count }, (_, index) => <option key={index} value={index}>{index + 1}</option>)}</select> of {job.count}</label><button className="icon-button" aria-label="Next page" disabled={busy || page === job.count - 1} onClick={() => { setPage(value => value + 1); setPreviewError(false); }}><ChevronRight size={17} /></button></div>
          </section>
          <aside className="cleanup-panel" aria-label="Cleanup settings"><div className="panel-title"><span className="feature-icon"><WandSparkles size={20} /></span><div><h2 id="pdf-type-heading">Choose your PDF type</h2><p>{presets.length} cleanup types available.</p></div></div>
            <div className="cleanup-type-picker"><label htmlFor="cleanup-type">All cleanup types ({presets.length})</label><select id="cleanup-type" value={method} disabled={busy} onChange={event => choosePreset(event.target.value)}>{presets.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select><button type="button" className="text-button" disabled={busy} onClick={() => setPdfType(presets.find(item => item.id === method))}>View selected example<ArrowRight size={14} /></button></div>
            <div className="cleanup-options">
            <CleanupSuggestions state={suggestions} presets={presets} method={method} busy={busy} onChoose={choosePreset} />
            <fieldset disabled={busy} className="preset-fieldset" aria-labelledby="cleanup-examples-heading"><legend id="cleanup-examples-heading">All cleanup examples</legend><div className="pdf-type-grid">{presets.map(item => <button type="button" key={item.id} className={'pdf-type-card ' + (method === item.id ? 'selected' : '')} aria-label={'View ' + item.title + ' PDF type'} aria-haspopup="dialog" onClick={() => setPdfType(item)}><span className="pdf-type-thumbnail"><img src={'/static/type-' + item.image + '.' + (['polarity','red-copy','flat-gray','repeated-copy','nassau','screened-unofficial','kane-outline','licensed-diagonal','hocking-outline'].includes(item.image) ? 'svg' : 'webp')} alt="" />{method === item.id && <span className="pdf-type-check"><Check size={13} /></span>}</span><span className="pdf-type-card-title">{item.title}<ArrowRight size={14} /></span><span className="pdf-type-tag">{item.tag}</span><span className="sr-only">{method === item.id ? 'Selected' : ''}</span></button>)}</div></fieldset>
            {method === 'repeated_diagonal_ai' && <div className="note" role="status"><p>{localAI.message} Uses the earlier full cleanup coverage, then copies supported original strokes back into the result. Covered words, signatures and barcodes can still change; inspect every AI draft before export.</p></div>}
            </div>
            {method === 'gemini_draft' ? <GeminiTrial request={request} busy={busy} page={page} onTrial={geminiTrial} /> : <div className="panel-actions"><Button className="preview-action" onClick={preview} disabled={busy || (method === 'repeated_diagonal_ai' && !localAI.ready)}><ScanLine size={16} />Preview first page</Button>{results.length ? <Button className="review-action" onClick={() => navigate('review')} disabled={busy}><Check size={16} />Review pages<ArrowRight size={16} /></Button> : <Button className="clean-action" onClick={cleanAll} disabled={busy || (method === 'repeated_diagonal_ai' && !localAI.ready)}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <WandSparkles size={16} />}Clean all {job.count} {job.count === 1 ? 'page' : 'pages'}<ArrowRight size={16} /></Button>}<span className="panel-footnote"><ShieldCheck size={13} />Your original is always preserved.</span></div>}
          </aside></div>
          <div aria-live="polite" role={error ? 'alert' : 'status'} className={`status-banner ${error ? 'error' : ''}`}>{busy ? <LoaderCircle size={17} className="animate-spin" /> : error ? <CircleHelp size={17} /> : <Check size={17} />}<span>{error || message}</span>{busy && progress !== null && <span className="progress-value">{progress}%</span>}</div>
          {pageDetails?.pattern_note && <div className="note" role="status"><ShieldCheck size={20} /><p>{pageDetails.pattern_note}</p></div>}
          {pageDetails?.reconstruction_unverified && !!results.length && <div className="note"><label><input type="checkbox" checked={reviewedAI.has(page)} disabled={busy} onChange={event => { const checked = event.target.checked; setReviewedAI(previous => { const next = new Set(previous); checked ? next.add(page) : next.delete(page); return next; }); }} /> Mark page reviewed: I compared the text, numbers, signatures and barcodes with the original. This does not certify accuracy.</label></div>}
          {method === 'repeated_diagonal_ai' && busy && !cleanupProgress && <Button variant="secondary" onClick={() => request(`/api/cancel/${job.id}`, {}).catch(failure => setError(failure.message))}>Stop local AI</Button>}
          {method === 'gemini_draft' && busy && <Button variant="secondary" onClick={() => request(`/api/cancel/${job.id}`, {}).catch(failure => setError(failure.message))}>Stop waiting (submitted requests may still be billed)</Button>}
          {busy && <progress className="work-progress" aria-label="Document processing progress" {...(progress !== null ? { value: progress, max: 100 } : {})} />}
          {method==='huntington_background'&&previewed.size>0&&<HuntingtonReview key={job.id} job={job} results={results} page={page} revision={revision} preview={huntingtonPreview} busy={busy} request={request} waitForJob={waitForJob} begin={begin} end={()=>setBusy(false)} onPage={setPage} update={state=>{setJob(previous=>({...previous,...state}));setResults(state.results||[]);setRevision(value=>value+1);setSelected(new Set());setDownloaded(false);setMessage(state.message||'Review the updated text.');}}/>}
        </>}
        {view === 'review' && job && <PdfReview reviewedAI={reviewedAI} onReviewAI={(index, checked) => setReviewedAI(previous => { const next = new Set(previous); checked ? next.add(index) : next.delete(index); return next; })} job={job} results={results} selected={selected} page={page} revision={revision} busy={busy} error={error} message={message} downloaded={downloaded} onPage={setPage} onToggle={togglePage} onSelectAll={() => setSelected(selected.size === job.count ? new Set() : new Set(Array.from({ length: job.count }, (_, index) => index)))} onDownload={download} onBack={() => navigate('workspace')} />}
      </main>
    </div>
    {pdfType && <PdfTypeDialog item={pdfType} selected={method === pdfType.id} onSelect={choosePreset} onClose={() => setPdfType(null)} />}
    {help && <HelpDialog onClose={() => setHelp(false)} />}
    {showBilling && <BillingDialog status={billing} request={request} onChange={setBilling} onClose={() => setShowBilling(false)} />}
    {showAdmin && <AdminDialog status={billing} request={request} onChange={setBilling} onClose={()=>setShowAdmin(false)} />}
    {showCompression && job && <CompressionDialog job={job} request={request} onBusyChange={setBusy} onJobChange={updated => setJob(previous => ({ ...previous, ...updated }))} onClose={() => setShowCompression(false)} />}
    {cleanupProgress && <CleanupProgress filename={job.name} {...cleanupProgress} onStop={async () => { try { await request(`/api/cancel/${job.id}`, {}); } catch (failure) { setError(failure.message); throw failure; } }} onClose={() => {
      const complete = cleanupProgress.status === 'complete';
      setCleanupProgress(null);
      if (complete) { setPage(0); setView('review'); }
    }} />}
  </div>;
}

