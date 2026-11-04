import { useState } from 'react';
import { formatDuration } from '../utils/format.js';

// The worker records the source size; portrait videos are pillarboxed, not cropped.
function isPortrait(renditions) {
  const source = renditions?.source;
  return Boolean(source && source.height > source.width);
}

export default function Thumbnail({ src, duration, renditions }) {
  const length = formatDuration(duration);
  // A thumbnail the viewer may not load (a removed video) falls back to the placeholder.
  const [failed, setFailed] = useState(null);
  const shown = src && failed !== src ? src : null;
  return (
    <div className={isPortrait(renditions) ? 'thumb thumb-portrait' : 'thumb'}>
      {shown ? (
        <img src={shown} alt="" loading="lazy" decoding="async" onError={() => setFailed(src)} />
      ) : (
        <svg className="thumb-empty" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M8 5.5v13a.75.75 0 0 0 1.14.64l10.4-6.5a.75.75 0 0 0 0-1.28L9.14 4.86A.75.75 0 0 0 8 5.5Z" />
        </svg>
      )}
      {length ? <span className="thumb-duration">{length}</span> : null}
    </div>
  );
}
