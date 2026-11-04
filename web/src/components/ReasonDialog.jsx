import { useId, useState } from 'react';
import Dialog from './Dialog.jsx';

// Admin actions need a reason: it goes to the audit log, and a takedown
// reason is shown to the video's owner.
export default function ReasonDialog({
  open,
  onClose,
  title,
  description,
  confirmLabel,
  danger = false,
  mutation,
  onConfirm,
}) {
  const id = useId();
  const [reason, setReason] = useState('');
  const close = () => {
    setReason('');
    mutation.reset();
    onClose();
  };
  const submit = (event) => {
    event.preventDefault();
    onConfirm(reason.trim(), close);
  };
  return (
    <Dialog open={open} onClose={close} title={title}>
      <form className="form" onSubmit={submit}>
        {description ? <p className="dialog-text">{description}</p> : null}
        {mutation.error ? (
          <div className="notice notice-error" role="alert">
            <span>{mutation.error.message}</span>
          </div>
        ) : null}
        <div className="field">
          <label htmlFor={`${id}-reason`}>Reason</label>
          <textarea
            id={`${id}-reason`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            minLength={3}
            maxLength={500}
            rows={3}
            required
            disabled={mutation.isPending}
          />
          <span className="field-hint">Recorded in the audit log.</span>
        </div>
        <div className="dialog-actions">
          <button type="button" className="button" onClick={close} disabled={mutation.isPending}>
            Cancel
          </button>
          <button
            type="submit"
            className={danger ? 'button button-danger' : 'button button-primary'}
            disabled={mutation.isPending || reason.trim().length < 3}
          >
            {mutation.isPending ? 'Saving…' : confirmLabel}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
