import { useId, useState } from 'react';
import { useDeleteVideo, useUpdateVideo } from '../api/videos.js';
import Dialog from './Dialog.jsx';
import VisibilityChoice from './VisibilityChoice.jsx';

function ErrorNotice({ error }) {
  if (!error) return null;
  return (
    <div className="notice notice-error" role="alert">
      <span>{error.message}</span>
    </div>
  );
}

// Only the fields that changed are sent, so an edit never overwrites more than it must.
function changedFields(video, form) {
  const changes = {};
  if (form.title.trim() !== video.title) changes.title = form.title;
  if (form.description.trim() !== video.description) changes.description = form.description;
  if (form.visibility !== video.visibility) changes.visibility = form.visibility;
  return changes;
}

function EditForm({ video, onDone }) {
  const update = useUpdateVideo();
  const id = useId();
  const [form, setForm] = useState({
    title: video.title,
    description: video.description,
    visibility: video.visibility,
  });
  const set = (field) => (value) => setForm((current) => ({ ...current, [field]: value }));
  const changes = changedFields(video, form);
  const unchanged = Object.keys(changes).length === 0;

  const submit = (event) => {
    event.preventDefault();
    if (unchanged) {
      onDone();
      return;
    }
    update.mutate({ publicId: video.public_id, changes }, { onSuccess: onDone });
  };

  return (
    <form className="form" onSubmit={submit}>
      <ErrorNotice error={update.error} />
      <div className="field">
        <label htmlFor={`${id}-title`}>Title</label>
        <input
          id={`${id}-title`}
          value={form.title}
          onChange={(event) => set('title')(event.target.value)}
          maxLength={200}
          required
          disabled={update.isPending}
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-description`}>
          Description <span className="optional">optional</span>
        </label>
        <textarea
          id={`${id}-description`}
          value={form.description}
          onChange={(event) => set('description')(event.target.value)}
          maxLength={5000}
          rows={4}
          disabled={update.isPending}
        />
      </div>
      <VisibilityChoice
        name={`${id}-visibility`}
        value={form.visibility}
        onChange={set('visibility')}
        disabled={update.isPending}
      />
      <div className="dialog-actions">
        <button type="button" className="button" onClick={onDone} disabled={update.isPending}>
          Cancel
        </button>
        <button type="submit" className="button button-primary" disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </form>
  );
}

export function EditVideoDialog({ video, open, onClose }) {
  return (
    <Dialog open={open} onClose={onClose} title="Edit video">
      {/* Keyed by video so reopening starts from the saved values. */}
      <EditForm video={video} onDone={onClose} key={video.updated_at} />
    </Dialog>
  );
}

export function DeleteVideoDialog({ video, open, onClose, onDeleted }) {
  const remove = useDeleteVideo();
  return (
    <Dialog open={open} onClose={onClose} title="Delete this video?">
      <p className="dialog-text">
        <strong>{video.title}</strong> and every file made from it will be deleted for good,
        including its view count. This cannot be undone.
      </p>
      <ErrorNotice error={remove.error} />
      <div className="dialog-actions">
        <button type="button" className="button" onClick={onClose} disabled={remove.isPending}>
          Cancel
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={remove.isPending}
          onClick={() =>
            remove.mutate(video.public_id, {
              onSuccess: () => {
                onClose();
                onDeleted?.();
              },
            })
          }
        >
          {remove.isPending ? 'Deleting…' : 'Delete video'}
        </button>
      </div>
    </Dialog>
  );
}

// Edit and Delete buttons plus their dialogs, for the owner's views.
export function OwnerActions({ video, onDeleted, compact = false }) {
  const [open, setOpen] = useState(null);
  const removed = Boolean(video.removed_at);
  return (
    <div className={compact ? 'owner-actions owner-actions-compact' : 'owner-actions'}>
      {removed ? null : (
        <button type="button" className="button" onClick={() => setOpen('edit')}>
          Edit
        </button>
      )}
      <button
        type="button"
        className="button button-quiet-danger"
        onClick={() => setOpen('delete')}
      >
        Delete
      </button>
      {removed ? null : (
        <EditVideoDialog video={video} open={open === 'edit'} onClose={() => setOpen(null)} />
      )}
      <DeleteVideoDialog
        video={video}
        open={open === 'delete'}
        onClose={() => setOpen(null)}
        onDeleted={onDeleted}
      />
    </div>
  );
}
