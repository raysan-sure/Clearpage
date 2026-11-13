import React from 'react';
import { ArrowRight, Check, WandSparkles } from 'lucide-react';

export default function CleanupTypeCard({ item, suggestion, selected, busy, onView }) {
  const extension = ['not-valid', 'polarity', 'red-copy', 'flat-gray', 'repeated-copy', 'nassau', 'screened-unofficial', 'kane-outline', 'licensed-diagonal', 'hocking-outline'].includes(item.image) ? 'svg' : 'webp';
  return <article className={`pdf-type-card ${selected ? 'selected' : ''} ${suggestion ? 'suggested' : ''}`}>
    <button type="button" className="pdf-type-details-button" disabled={busy} aria-label={`View ${item.title} PDF type`} aria-haspopup="dialog" onClick={() => onView(item)}>
      <span className="pdf-type-thumbnail"><img src={`/static/type-${item.image}.${extension}`} alt="" />{suggestion && <span className="suggestion-badge"><WandSparkles size={13} />Suggested</span>}{selected && <span className="pdf-type-check"><Check size={13} /></span>}</span>
      <span className="pdf-type-card-title">{item.title}<ArrowRight size={14} /></span>
      {selected && <span className="sr-only">Selected</span>}
    </button>
  </article>;
}
