import { useRef, useState } from 'react';
import { Link, Navigate } from 'react-router';
import { useMe } from '../auth/session.js';
import { useCompleteVideoUpload, useCreateVideoUpload } from '../api/videos.js';
import { partSizeFor, uploadParts } from '../api/uploads.js';

const MAX_SIZE = 500 * 1000 * 1000;
const ACCEPTED_TYPES = ['video/mp4', 'video/quicktime', 'video/webm', 'video/x-matroska'];

function errorMessage(error) {
  return error?.message ?? 'The upload could not be completed. Try again.';
}

export default function Upload() {
  const { data: me, isPending: mePending } = useMe();
  const createUpload = useCreateVideoUpload();
  const completeUpload = useCompleteVideoUpload();
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({ title: '', description: '', visibility: 'public' });
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('idle');
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  if (mePending) return <main className="page">Checking session…</main>;
  if (!me) return <Navigate to="/login?next=/upload" replace />;

  const update = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  const chooseFile = (event) => {
    const selected = event.target.files?.[0] ?? null;
    setError(null);
    setResult(null);
    setProgress(0);
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
    } catch (uploadError) {
      setStage('error');
      setError(errorMessage(uploadError));
    }
  };

  return (
    <main className="page page-narrow upload-page">
      <div className="page-heading">
        <div>
          <h1>Upload a video</h1>
          <p className="muted">The file goes straight from your browser to object storage.</p>
        </div>
        <Link to="/my-videos">My videos</Link>
      </div>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
      {result ? (
        <div className="success-box" role="status">
          <strong>Upload complete.</strong>
          <span>{result.title} is waiting for processing.</span>
          <Link to="/my-videos">View my videos</Link>
        </div>
      ) : null}
      <form className="form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="video-file">Video file</label>
          <input
            ref={inputRef}
            id="video-file"
            type="file"
            accept={ACCEPTED_TYPES.join(',')}
            onChange={chooseFile}
            disabled={stage !== 'idle' && stage !== 'error'}
          />
          <p className="field-hint">MP4, QuickTime, WebM, or Matroska; up to 500 MB.</p>
          {file ? (
            <p className="file-summary">
              {file.name} · {formatBytes(file.size)}
            </p>
          ) : null}
        </div>
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
          <label htmlFor="video-description">Description</label>
          <textarea
            id="video-description"
            value={form.description}
            onChange={update('description')}
            maxLength={5000}
            rows={4}
          />
        </div>
        <div className="field">
          <label htmlFor="video-visibility">Visibility</label>
          <select id="video-visibility" value={form.visibility} onChange={update('visibility')}>
            <option value="public">Public</option>
            <option value="unlisted">Unlisted</option>
            <option value="private">Private</option>
          </select>
        </div>
        {stage !== 'idle' && stage !== 'error' ? (
          <div className="upload-progress" aria-live="polite">
            <div className="progress-label">
              <span>{stageLabel(stage)}</span>
              <span>{progress}%</span>
            </div>
            <progress max="100" value={progress} />
          </div>
        ) : null}
        <button
          type="submit"
          className="button button-primary"
          disabled={stage !== 'idle' && stage !== 'error'}
        >
          {stage === 'starting'
            ? 'Preparing upload…'
            : stage === 'uploading'
              ? 'Uploading…'
              : stage === 'finishing'
                ? 'Finishing upload…'
                : 'Start upload'}
        </button>
      </form>
    </main>
  );
}

function stageLabel(stage) {
  if (stage === 'starting') return 'Preparing direct upload';
  if (stage === 'uploading') return 'Uploading to storage';
  if (stage === 'finishing') return 'Confirming uploaded parts';
  return 'Upload';
}

function formatBytes(bytes) {
  if (bytes < 1000 * 1000) return `${Math.round(bytes / 1000)} KB`;
  return `${(bytes / (1000 * 1000)).toFixed(1)} MB`;
}
