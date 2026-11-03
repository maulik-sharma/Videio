const LABELS = {
  pending_upload: 'Waiting for upload',
  uploaded: 'Queued',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
};

export default function StatusBadge({ status, reason }) {
  return (
    <span className={`status status-${status}`}>
      <span className="status-dot" aria-hidden="true" />
      {LABELS[status] ?? status}
      {status === 'failed' && reason ? `: ${reason}` : null}
    </span>
  );
}
