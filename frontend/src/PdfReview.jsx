import React, { useEffect, useRef, useState } from 'react';
import useCtrlWheelZoom from './useCtrlWheelZoom';
import { ArrowDownToLine, ArrowLeft, Check, CheckCheck, ChevronLeft, ChevronRight, FileText, LoaderCircle, Maximize2, ZoomIn, ZoomOut } from 'lucide-react';

export default function PdfReview({ job, results, selected, page, revision, busy, error, message, downloaded, onPage, onToggle, onSelectAll, onDownload, onBack, reviewedAI, onReviewAI }) {
  const [version, setVersion] = useState('after');
  const [zoom, setZoom] = useState(100);
  const [fitMode, setFitMode] = useState('page');
  const [imageError, setImageError] = useState(false);
  const [retry, setRetry] = useState(0);
  const activeRef = useRef(null);
  const titleRef = useRef(null);
  const canvasRef = useRef(null);
  useCtrlWheelZoom(canvasRef, direction => {
    const canvas = canvasRef.current;
    const image = canvas?.querySelector('img');
    if (!image) return;
    // Start from the fitted page's visible size instead of jumping to full width.
    const style = getComputedStyle(canvas);
    const availableWidth = canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    const currentZoom = fitMode === 'page' && availableWidth > 0 ? image.getBoundingClientRect().width / availableWidth * 100 : zoom;
    setFitMode('width');
    setZoom(Math.max(25, Math.min(300, Math.round(currentZoom + direction * 10))));
  });
  const result = results.find(item => item.page === page);
  const hasCleanedPages = results.some(item => item.changed);
  const needsSelection = hasCleanedPages && !results.some(item => item.changed && selected.has(item.page));
  useEffect(() => { titleRef.current?.focus(); window.scrollTo(0, 0); }, []);
  useEffect(() => {
    setImageError(false);
    activeRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    canvasRef.current?.scrollTo(0, 0);
  }, [page, version, revision, retry]);
  return <section className="pdf-review" aria-label="Review and export PDF">
    <header className="review-topbar">
      <div className="review-file"><button className="icon-button" aria-label="Back to cleanup" disabled={busy} onClick={onBack}><ArrowLeft size={19} /></button><FileText size={21} /><div><h1 ref={titleRef} tabIndex={-1}>{job.name}</h1><p>{selected.size} of {job.count} cleaned pages selected</p></div></div>
      <div className="review-top-actions"><button className="button button-secondary" disabled={busy} onClick={onSelectAll}><CheckCheck size={16} />{selected.size === job.count ? 'Clear selection' : 'Select all pages'}</button><button className="button button-primary" disabled={busy || !results.length || needsSelection || results.some(item => item.reconstruction_unverified && selected.has(item.page) && !reviewedAI?.has(item.page))} onClick={onDownload}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <ArrowDownToLine size={16} />}{busy ? 'Preparing PDF…' : downloaded ? 'Download again' : 'Download PDF'}</button></div>
    </header>
    {needsSelection && <p className="review-guidance" role="status">Select at least one cleaned page to download your changes. Use Select all pages to include every cleaned page.</p>}
    {result?.reconstruction_unverified && <label className="review-guidance"><input type="checkbox" checked={reviewedAI?.has(page) || false} disabled={busy} onChange={event => onReviewAI(page, event.target.checked)} /> I compared this AI draft with the original, including text, numbers, signatures and barcodes.</label>}
    {(error || busy || downloaded) && <div className={`review-notice ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}>{error || message}</div>}
    <div className="review-reader">
      <aside className="review-page-rail" aria-label="PDF pages"><div className="review-rail-title">Pages <span>{job.count}</span></div><div className="review-page-list">{Array.from({ length: job.count }, (_, index) => {
        const item = results.find(entry => entry.page === index);
        return <div ref={page === index ? activeRef : null} key={index} className={`review-page-item ${page === index ? 'active' : ''}`}>
          <button className="review-page-open" aria-label={`Show page ${index + 1}`} aria-current={page === index ? 'page' : undefined} disabled={busy} onClick={() => onPage(index)}><span className="review-page-thumb"><img loading="lazy" src={`/api/page/${job.id}/${index}/after?v=${revision}`} alt={`Page ${index + 1} thumbnail`} onError={event => { event.currentTarget.style.visibility = 'hidden'; }} /><span>{index + 1}</span></span><strong>Page {index + 1}</strong></button>
          <label className="review-page-choice" title={`Use cleaned page ${index + 1}`}><input type="checkbox" checked={selected.has(index)} disabled={busy} aria-label={`Use cleaned page ${index + 1}`} onChange={() => onToggle(index)} /></label><p className="review-page-status">{item?.uncertain ? 'Review carefully' : item?.changed ? (selected.has(index) ? 'Cleaned page selected' : 'Original page kept') : 'Unchanged'}</p>
        </div>;
      })}</div></aside>
      <section className="review-main-page" aria-label={`Page ${page + 1} preview`}>
        <div className="review-page-toolbar"><div className="review-version" role="group" aria-label="Page version"><button className={version === 'before' ? 'active' : ''} aria-pressed={version === 'before'} onClick={() => setVersion('before')}>Original</button><button className={version === 'after' ? 'active' : ''} aria-pressed={version === 'after'} onClick={() => setVersion('after')}>Cleaned</button></div><div className="review-page-controls"><button className="icon-button" aria-label="Previous page" disabled={busy || page === 0} onClick={() => onPage(page - 1)}><ChevronLeft size={18} /></button><label>Page <select aria-label="Review page" value={page} disabled={busy} onChange={event => onPage(Number(event.target.value))}>{Array.from({ length: job.count }, (_, index) => <option key={index} value={index}>{index + 1}</option>)}</select> of {job.count}</label><button className="icon-button" aria-label="Next page" disabled={busy || page === job.count - 1} onClick={() => onPage(page + 1)}><ChevronRight size={18} /></button></div><div className="review-selection-group"><label className={`review-current-choice ${selected.has(page) ? 'is-enabled' : ''}`}><input type="checkbox" checked={selected.has(page)} disabled={busy} onChange={() => onToggle(page)} /><span className="review-selection-switch" aria-hidden="true" /><span className="review-selection-label">Use cleaned page</span></label>{result?.uncertain && <span className="review-caution-badge">Review carefully</span>}</div><div className="zoom-controls"><button className="icon-button" aria-label="Zoom out" disabled={fitMode !== 'page' && zoom <= 25} onClick={() => { setFitMode('width'); setZoom(value => Math.max(25, value - 25)); }}><ZoomOut size={16} /></button><span>{fitMode === 'page' ? 'Fit page' : `${zoom}%`}</span><button className="icon-button" aria-label="Zoom in" disabled={fitMode !== 'page' && zoom >= 300} onClick={() => { setFitMode('width'); setZoom(value => Math.min(300, value + 25)); }}><ZoomIn size={16} /></button><button className={`review-fit-button ${fitMode === 'page' ? 'active' : ''}`} aria-pressed={fitMode === 'page'} onClick={() => { setFitMode('page'); setZoom(100); }}><Maximize2 size={14} />Fit page</button><button className={`review-fit-button ${fitMode === 'width' && zoom === 100 ? 'active' : ''}`} aria-pressed={fitMode === 'width' && zoom === 100} onClick={() => { setFitMode('width'); setZoom(100); }}>Fit width</button></div></div>
        <div ref={canvasRef} title="Hold Ctrl and scroll to zoom" className={`review-document-canvas ${fitMode === 'page' ? 'fit-page' : ''}`}>{imageError ? <div className="review-image-error" role="alert">Could not load this page.<button className="button button-secondary" onClick={() => setRetry(value => value + 1)}>Retry</button></div> : <img key={`${page}-${version}-${revision}-${retry}`} style={fitMode === 'page' ? { width: 'auto', height: 'auto', maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' } : { width: `${zoom}%`, maxWidth: 'none' }} src={`/api/page/${job.id}/${page}/${version}?v=${revision}&retry=${retry}`} alt={`${version === 'after' ? 'Cleaned' : 'Original'} document page ${page + 1}`} onError={() => setImageError(true)} />}</div>

      </section>
    </div>
  </section>;
}
