export default function Wordmark() {
  return (
    <span className="wordmark">
      <svg className="wordmark-mark" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <rect width="24" height="24" rx="6" />
        <path d="M9.5 7.2v9.6a.6.6 0 0 0 .9.5l7.6-4.8a.6.6 0 0 0 0-1L10.4 6.7a.6.6 0 0 0-.9.5Z" />
      </svg>
      <span className="wordmark-text">Videio</span>
    </span>
  );
}
