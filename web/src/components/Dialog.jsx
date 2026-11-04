import { useEffect, useId, useRef } from 'react';

// A modal on the native <dialog>: the browser traps focus, closes on Escape
// and restores focus to whatever opened it.
export default function Dialog({ open, onClose, title, children }) {
  const ref = useRef(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        // A click on the backdrop lands on the <dialog> element itself.
        if (event.target === ref.current) onClose();
      }}
    >
      {open ? (
        <div className="dialog-body">
          <h2 id={titleId} className="dialog-title">
            {title}
          </h2>
          {children}
        </div>
      ) : null}
    </dialog>
  );
}
