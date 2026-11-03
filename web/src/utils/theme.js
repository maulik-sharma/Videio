// Light/dark preference. With nothing stored the OS setting decides (CSS media
// query); a stored choice is applied as <html data-theme> by index.html before
// first paint, so there is no flash of the wrong theme.
const KEY = 'videio-theme';

export function storedTheme() {
  try {
    const value = localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null;
  }
}

export function systemTheme() {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function currentTheme() {
  return storedTheme() ?? systemTheme();
}

export function saveTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Storage blocked (private mode): the choice lasts for this page only.
  }
}
