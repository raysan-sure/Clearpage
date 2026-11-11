import React, { useEffect, useRef, useState } from 'react';
import { Download, LoaderCircle, X } from 'lucide-react';

const megabytes = bytes => `${(bytes / 1000000).toFixed(2)} MB`;

export default function CompressionDialog({ job, request, onBusyChange, onJobChange, onClose }) {
  const dialog = useRef(null);
  const [source, setSource] = useState('original');
  const [result, setResult] = useState(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const working = starting || result?.status === 'processing';
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal();
    let active = true;
    request(`/api/compress/${job.id}`).then(current => {
      if (active && current.status === 'processing') { setSource(current.source); setResult(current); }
    }).catch(failure => { if (active) setError(failure.message); });
    return () => { active = false; previous?.focus(); };
  }, [job.id]);
  useEffect(() => { onBusyChange(working); }, [working, onBusyChange]);
  useEffect(() => {
    if (result?.status !== 'processing') return;
    let active = true;
    let timer;
    async function poll() {
      try {
        const current = await request(`/api/compress/${job.id}`);
        if (!active) return;
        if (current.status !== 'processing') {
          const updatedJob = await request(`/api/job/${job.id}`);
          if (!active) return;
          onJobChange(updatedJob); setResult(current); setError('');
          return;
        }
        setResult(current); setError('');
      } catch (failure) { if (active) setError(failure.message); }
      if (active) timer = setTimeout(poll, 600);
    }
    timer = setTimeout(poll, 600);
    return () => { active = false; clearTimeout(timer); };
  }, [result?.status, job.id]);
  async function compress() {
    setStarting(true); setError(''); setResult(null);
    try { setResult(await request(`/api/compress/${job.id}`, { source })); }
    catch (failure) { setError(failure.message); }
    finally { setStarting(false); }
  }
  return <dialog ref={dialog} className="compression-dialog" aria-labelledby="compression-title" onCancel={event => {
    if (working) event.preventDefault(); else onClose();
  }}>
    <div className="compression-content">
      <button type="button" className="icon-button close-help" aria-label="Close compression" disabled={working} onClick={onClose}><X size={20} /></button>
      <span className="eyebrow">KEEP THE DETAIL</span>
      <h2 id="compression-title">Compress PDF</h2>
      <p>Target 25 MB or below, using lossless compression. Image resolution, text and page quality are preserved.</p>
      <p>Some PDFs are already optimized. If 25 MB cannot be reached losslessly, you can download the smallest result achieved.</p>
      <label htmlFor="compression-source">PDF to compress</label>
      <select id="compression-source" disabled={working} value={source} onChange={event => { setSource(event.target.value); setResult(null); setError(''); }}>
        <option value="original">Original uploaded PDF</option>
        <option value="cleaned" disabled={job.state !== 'done'}>Completed cleanup PDF{job.state !== 'done' ? ' (download your reviewed PDF first)' : ''}</option>
      </select>
      <p className="compression-local">Processed on your computer. Each MB is 1,000,000 bytes.</p>
      {(error || result?.status === 'error') && <p role="alert" className="compression-error">{error || result.message}</p>}
      {result?.status === 'cancelled' && <p role="status">{result.message}</p>}
      {working && <div role="status"><p><LoaderCircle size={16} className="animate-spin" />Compressing your PDF… {result?.progress || 0}%</p><progress aria-label="PDF compression progress" value={result?.progress || 0} max={100} /></div>}
      {result?.status === 'ready' && <div className="compression-result" role="status">
        <strong>{result.target_met ? '25 MB target met' : '25 MB could not be reached losslessly'}</strong>
        <dl><dt>Before</dt><dd>{megabytes(result.original_bytes)}</dd><dt>After</dt><dd>{megabytes(result.output_bytes)}</dd><dt>Saved</dt><dd>{megabytes(result.saved_bytes)} ({(result.saved_bytes / result.original_bytes * 100).toFixed(1)}%)</dd></dl>
        <p>{result.reduced ? 'Lossless optimization complete.' : 'This PDF is already optimized. The download keeps the original PDF bytes.'}</p>
        {!result.target_met && <p>Further reduction would require changing document quality or content.</p>}
        <a className="button button-primary" href={`/api/compressed-download/${job.id}`} download><Download size={16} />Download PDF</a>
      </div>}
      <div className="compression-actions"><button type="button" className="button button-primary" disabled={working} onClick={compress}>{result?.status === 'ready' ? 'Compress again' : 'Compress losslessly'}</button>{working && <button type="button" className="button button-secondary" onClick={async () => {
        try { await request(`/api/cancel/${job.id}`, {}); } catch (failure) { setError(failure.message); }
      }}>Stop compression</button>}</div>
    </div>
  </dialog>;
}
