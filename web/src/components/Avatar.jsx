// A fixed set of muted hues, so a name always gets the same colour.
const HUES = [215, 250, 285, 330, 10, 35, 150, 185];

function hueFor(name) {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.codePointAt(0)) | 0;
  return HUES[Math.abs(hash) % HUES.length];
}

function initials(name) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  const letters = words.length === 1 ? [...words[0]].slice(0, 1) : [words[0], words.at(-1)];
  return letters
    .map((word) => [...word][0])
    .join('')
    .toUpperCase();
}

export default function Avatar({ name = '', size = 'md' }) {
  return (
    <span
      className={`avatar avatar-${size}`}
      style={{ '--avatar-hue': hueFor(name) }}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  );
}
