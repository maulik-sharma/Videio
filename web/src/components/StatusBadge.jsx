const LABELS = {
  pending_upload: 'Waiting for upload',
  uploaded: 'Queued',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
};

// `short` drops the reason, for tables that show it on its own line.
export default function StatusBadge({ status, reason, removedReason, short = false }) {
  // A takedown outranks the processing status: it is why nobody can watch it.
  if (removedReason !== undefined && removedReason !== null) {
    return (
      <span className="status status-removed">
        <span className="status-dot" aria-hidden="true" />
        {short ? 'Removed' : `Removed by an admin: ${removedReason}`}
      </span>
    );
  }
  return (
    <span className={`status status-${status}`}>
      <span className="status-dot" aria-hidden="true" />
      {LABELS[status] ?? status}
      {status === 'failed' && reason && !short ? `: ${reason}` : null}
    </span>
  );
}
