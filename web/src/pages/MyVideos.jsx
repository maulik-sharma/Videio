import { Link, Navigate } from 'react-router';
import { useMe } from '../auth/session.js';
import { useMyVideos } from '../api/videos.js';

const STATUS_LABELS = {
  pending_upload: 'Pending upload',
  uploaded: 'Uploaded',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
};

export default function MyVideos() {
  const { data: me, isPending: mePending } = useMe();
  const videos = useMyVideos(Boolean(me));

  if (mePending) return <main className="page">Checking session…</main>;
  if (!me) return <Navigate to="/login?next=/my-videos" replace />;

  const items = videos.data?.pages.flatMap((page) => page.items) ?? [];
  const firstLoad = videos.isPending && items.length === 0;
  const failed = videos.error?.message ?? 'Could not load your videos.';

  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <h1>My videos</h1>
          <p className="muted">Uploads and their current processing state.</p>
        </div>
        <Link className="button button-primary" to="/upload">
          Upload video
        </Link>
      </div>
      {firstLoad ? <p className="muted">Loading videos…</p> : null}
      {videos.isError ? (
        <p className="form-error" role="alert">
          {failed}
        </p>
      ) : null}
      {!firstLoad && !videos.isError && items.length === 0 ? (
        <div className="empty-state">
          <strong>No videos yet.</strong>
          <span>Upload your first video to see it here.</span>
          <Link to="/upload">Start an upload</Link>
        </div>
      ) : null}
      {items.length > 0 ? (
        <div className="video-list">
          {items.map((video) => (
            <article className="video-row" key={video.id}>
              <div className="video-placeholder" aria-hidden="true">
                {video.status === 'ready' ? 'Ready' : '—'}
              </div>
              <div className="video-row-main">
                <h2>{video.title}</h2>
                <p className="muted">{video.description || 'No description'}</p>
                <div className="video-meta">
                  <span className={`status status-${video.status}`}>
                    {STATUS_LABELS[video.status] ?? video.status}
                  </span>
                  <span>{video.visibility}</span>
                  <span>{formatDate(video.created_at)}</span>
                </div>
              </div>
              <div className="video-size">{formatBytes(video.size_bytes)}</div>
            </article>
          ))}
        </div>
      ) : null}
      {videos.hasNextPage ? (
        <button
          type="button"
          className="button load-more"
          onClick={() => videos.fetchNextPage()}
          disabled={videos.isFetchingNextPage}
        >
          {videos.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </button>
      ) : null}
    </main>
  );
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value));
}

function formatBytes(bytes) {
  if (bytes < 1000 * 1000) return `${Math.round(bytes / 1000)} KB`;
  return `${(bytes / (1000 * 1000)).toFixed(1)} MB`;
}
