import React from 'react';
import CleanupTypeCard from './CleanupTypeCard';

export default function CleanupSuggestions({ state, presets, method, busy, onChoose, onView }) {
  const items = (state.data?.items || []).filter((item, index, all) => presets.some(p => p.id === item.method) && all.findIndex(match => match.method === item.method) === index);
  const suggestedMethods = new Set(items.map(item => item.method));
  return <>
    {state.loading && <p className="suggestion-status" role="status">Checking a few pages for matching watermark types…</p>}
    {!state.loading && !items.length && <p className="suggestion-status" role="status">{state.error ? 'Suggestions are unavailable. Choose a cleanup type below.' : 'No clear match found. Compare the cleanup examples below.'}</p>}
    <div className="pdf-type-grid">
      {items.map(suggestion => <CleanupTypeCard key={suggestion.method} item={presets.find(item => item.id === suggestion.method)} suggestion={suggestion} selected={method === suggestion.method} busy={busy} onView={onView} onChoose={onChoose} />)}
      {presets.filter(item => !suggestedMethods.has(item.id)).map(item => <CleanupTypeCard key={item.id} item={item} selected={method === item.id} busy={busy} onView={onView} onChoose={onChoose} />)}
    </div>
    {!!state.data?.sampled_pages?.length && <p className="suggestion-note">Checked pages {state.data.sampled_pages.join(', ')} of {state.data.total_pages}. Other pages may differ. Preview before cleaning.{!!state.data.skipped_pages?.length && ' Some pages could not be checked.'}</p>}
  </>;
}
