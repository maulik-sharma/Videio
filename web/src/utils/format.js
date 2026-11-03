// Display formatting shared by every page. English copy, so English formats.
const LOCALE = 'en';

const compact = new Intl.NumberFormat(LOCALE, { notation: 'compact', maximumFractionDigits: 1 });
const relative = new Intl.RelativeTimeFormat(LOCALE, { numeric: 'auto' });
const fullDate = new Intl.DateTimeFormat(LOCALE, { dateStyle: 'medium' });

// 75 -> "1:15", 3725 -> "1:02:05". Null when the length is not known yet.
export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = String(total % 60).padStart(2, '0');
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${secs}` : `${minutes}:${secs}`;
}

// 1 -> "1 view", 1234 -> "1.2K views".
export function formatViews(count) {
  return `${compact.format(count)} ${count === 1 ? 'view' : 'views'}`;
}

const UNITS = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
];

// "just now", "5 minutes ago", "yesterday", "3 weeks ago".
export function formatRelative(value, now = Date.now()) {
  const seconds = (new Date(value).getTime() - now) / 1000;
  if (Math.abs(seconds) < 60) return 'just now';
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return 'just now';
}

export function formatDate(value) {
  return fullDate.format(new Date(value));
}

export function formatBytes(bytes) {
  if (bytes < 1000 * 1000) return `${Math.max(1, Math.round(bytes / 1000))} KB`;
  if (bytes < 1000 * 1000 * 1000) return `${(bytes / (1000 * 1000)).toFixed(1)} MB`;
  return `${(bytes / (1000 * 1000 * 1000)).toFixed(2)} GB`;
}
