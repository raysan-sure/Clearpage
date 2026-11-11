import React from 'react';

export default function CleanupSuggestions({ state, presets, method, busy, onChoose }) {
  const items = (state.data?.items || []).filter(item => presets.some(p => p.id === item.method));
  return <section className="cleanup-suggestions" aria-labelledby="suggestion-title" aria-busy={state.loading}>
    <h3 id="suggestion-title">Suggested for this file</h3>
    {state.loading ? <p role="status">Checking a few pages for matching watermark types…</p> : <>
      {!items.length && <p>{state.error ? 'Suggestions are unavailable. You can still choose a type below.' : 'No clear match found. Compare the examples under All cleanup types.'}</p>}
      {items.map(item => <div className="suggestion-card" key={item.method}>
        <img className="suggestion-example" src={`/static/type-${presets.find(p => p.id === item.method).image}.${['polarity','red-copy','flat-gray','repeated-copy','nassau','screened-unofficial','kane-outline','licensed-diagonal','hocking-outline'].includes(presets.find(p => p.id === item.method).image) ? 'svg' : 'webp'}`} alt="Illustrative watermark example" />
        <strong>{presets.find(p => p.id === item.method).title}</strong>
        <p>{item.reason}</p>
        <small>Found on {item.pages.length === 1 ? 'page' : 'pages'} {item.pages.join(', ')}</small>
        <button type="button" className="button button-secondary" disabled={busy || method === item.method} onClick={() => onChoose(item.method)}>
          {method === item.method ? 'Selected' : 'Use this type'}
        </button>
      </div>)}
      {!!state.data?.sampled_pages?.length && <p className="suggestion-note">Checked pages {state.data.sampled_pages.join(', ')} of {state.data.total_pages}. Other pages may differ. Preview before cleaning.{!!state.data.skipped_pages?.length && ' Some pages could not be checked.'}</p>}
    </>}
  </section>;
}
