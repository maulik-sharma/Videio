// Small per-browser preferences. Storage can be blocked (private mode), so
// every access is guarded and the default wins when it fails.
export function readPref(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function writePref(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Not saved: the choice lasts for this page only.
  }
}
