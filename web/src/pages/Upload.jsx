import { useState } from 'react';
import { Link, Navigate } from 'react-router';
import { useMe } from '../auth/session.js';
import { useCompleteVideoUpload, useCreateVideoUpload } from '../api/videos.js';
import { partSizeFor, uploadParts } from '../api/uploads.js';
import { formatBytes } from '../utils/format.js';

const MAX_SIZE = 500 * 1000 * 1000;
const ACCEPTED_TYPES = ['video/mp4', 'video/quicktime', 'video/webm', 'video/x-matroska'];

const STAGES = {
  starting: 'Preparing upload…',
  uploading: 'Uploading…',
  finishing: 'Finishing upload…',
};

function errorMessage(error) {
  return error?.message ?? 'The upload could not be completed. Try again.';
}

export default function Upload() {
  const { data: me, isPending: mePending } = useMe();
  const createUpload = useCreateVideoUpload();
  const completeUpload = useCompleteVideoUpload();
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({ title: '', description: '', visibility: 'public' });
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('idle');
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [dragging, setDragging] = useState(false);

  if (mePending) return <main className="page" aria-busy="true" />;
  if (!me) return <Navigate to="/login?next=/upload" replace />;

  const busy = stage in STAGES;
  const update = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  const chooseFile = (selected) => {
    setError(null);
    setResult(null);
    setProgress(0);
    setStage('idle');
    if (selected && !ACCEPTED_TYPES.includes(selected.type)) {
      setFile(null);
      setError('Choose an MP4, QuickTime, WebM, or Matroska video.');
      return;
    }
    if (selected && selected.size > MAX_SIZE) {
      setFile(null);
      setError('Videos must be 500 MB or smaller.');
      return;
    }
    setFile(selected);
    if (selected && !form.title)
      setForm((current) => ({ ...current, title: selected.name.replace(/\.[^.]+$/, '') }));
  };

  const drop = (event) => {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) chooseFile(dropped);
  };

  const submit = async (event) => {
    event.preventDefault();
    setError(null);
    setResult(null);
    if (!file) {
      setError('Choose a video file first.');
      return;
    }
    if (!form.title.trim()) {
      setError('Add a title for the video.');
      return;
    }

    try {
      setStage('starting');
      const upload = await createUpload.mutateAsync({ ...form, file });
      const partSizes = partSizeFor(file, upload.part_size_bytes);
      const parts = upload.parts.map((part, index) => ({
        ...part,
        size: partSizes[index].size,
      }));
      setStage('uploading');
      const completedParts = await uploadParts(file, parts, setProgress);
      setStage('finishing');
      const completed = await completeUpload.mutateAsync({
        publicId: upload.public_id,
        parts: completedParts,
      });
      setStage('done');
      setProgress(100);
      setResult(completed);
      setFile(null);
      setForm({ title: '', description: '', visibility: form.visibility });
    } catch (uploadError) {
      setStage('error');
      setError(errorMessage(uploadError));
    }
  };

  return (
    <main className="page page-form">
      <div className="page-heading">
        <div>
          <h1>Upload a video</h1>
          <p className="page-subtitle">
            Up to 10 minutes and 500 MB. Every quality up to the original is made after upload.
          </p>
        </div>
      </div>

      {error ? (
        <div className="notice notice-error" role="alert">
          {error}
        </div>
      ) : null}
      {result ? (
        <div className="notice notice-ok" role="status">
          <strong>Uploaded “{result.title}”.</strong>
          <span>
            It is being processed now. <Link to="/my-videos">Follow its progress in My videos</Link>
            .
          </span>
        </div>
      ) : null}

      <form className="form" onSubmit={submit}>
        <label
          className={`dropzone${dragging ? ' dropzone-active' : ''}${file ? ' dropzone-filled' : ''}`}
          htmlFor="video-file"
          onDragOver={(event) => {
            event.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={drop}
        >
          <input
            id="video-file"
            className="visually-hidden"
            type="file"
            accept={ACCEPTED_TYPES.join(',')}
            onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
            disabled={busy}
          />
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
          </svg>
          {file ? (
            <>
              <strong className="dropzone-file">{file.name}</strong>
              <span>
                {formatBytes(file.size)}
                {busy ? '' : ' · choose a different file'}
              </span>
            </>
          ) : (
            <>
              <strong>Drop a video here, or choose a file</strong>
              <span>MP4, QuickTime, WebM or Matroska</span>
            </>
          )}
        </label>

        <div className="field">
          <label htmlFor="video-title">Title</label>
          <input
            id="video-title"
            value={form.title}
            onChange={update('title')}
            maxLength={200}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="video-description">
            Description <span className="optional">optional</span>
          </label>
          <textarea
            id="video-description"
            value={form.description}
            onChange={update('description')}
            maxLength={5000}
            rows={4}
          />
        </div>
        <fieldset className="field">
          <legend>Visibility</legend>
          <div className="choice-group">
            {[
              ['public', 'Public', 'Shown in the feed.'],
              ['unlisted', 'Unlisted', 'Anyone with the link can watch.'],
              ['private', 'Private', 'Only you can watch.'],
            ].map(([value, label, hint]) => (
              <label className="choice" key={value}>
                <input
                  type="radio"
                  name="visibility"
                  value={value}
                  checked={form.visibility === value}
                  onChange={update('visibility')}
                  disabled={busy}
                />
                <span>
                  <strong>{label}</strong>
                  <span>{hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {busy ? (
          <div className="progress" aria-live="polite">
            <div className="progress-label">
              <span>{STAGES[stage]}</span>
              <span>{progress}%</span>
            </div>
            <div
              className="progress-track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress}
              aria-label="Upload progress"
            >
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
          </div>
        ) : null}

        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={busy}>
            {busy ? STAGES[stage] : 'Upload'}
          </button>
        </div>
      </form>
    </main>
  );
}
