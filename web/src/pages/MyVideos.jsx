import { Link, Navigate } from 'react-router';
import { useMe } from '../auth/session.js';
import { useMyVideos } from '../api/videos.js';
import StatusBadge from '../components/StatusBadge.jsx';
import Thumbnail from '../components/Thumbnail.jsx';
import { formatBytes, formatDate, formatRelative, formatViews } from '../utils/format.js';

const VISIBILITY = { public: 'Public', unlisted: 'Unlisted', private: 'Private' };

function VideoRow({ video }) {
  const ready = video.status === 'ready';
  const thumb = (
    <Thumbnail
      src={video.thumbnail_url}
      duration={video.duration_s}
      renditions={video.renditions}
    />
  );
  return (
    <li className="row">
      {ready ? (
        <Link to={`/v/${video.public_id}`} className="row-thumb" tabIndex={-1} aria-hidden="true">
          {thumb}
        </Link>
      ) : (
        <div className="row-thumb">{thumb}</div>
      )}
      <div className="row-main">
        <h2 className="row-title">
          {ready ? <Link to={`/v/${video.public_id}`}>{video.title}</Link> : video.title}
        </h2>
        {video.description ? <p className="row-description">{video.description}</p> : null}
        <StatusBadge status={video.status} reason={video.failure_reason} />
      </div>
      <dl className="row-facts">
        <div className="fact-1">
          <dt>Visibility</dt>
          <dd>{VISIBILITY[video.visibility] ?? video.visibility}</dd>
        </div>
        {ready ? (
          <div className="fact-2">
            <dt>Views</dt>
            <dd>{formatViews(video.view_count)}</dd>
          </div>
        ) : null}
        <div className="fact-3">
          <dt>Uploaded</dt>
          <dd title={formatDate(video.created_at)}>{formatRelative(video.created_at)}</dd>
        </div>
        <div className="fact-4">
          <dt>Size</dt>
          <dd>{formatBytes(video.size_bytes)}</dd>
        </div>
      </dl>
    </li>
  );
}

export default function MyVideos() {
  const { data: me, isPending: mePending } = useMe();
  const videos = useMyVideos(Boolean(me));

  if (mePending) return <main className="page" aria-busy="true" />;
  if (!me) return <Navigate to="/login?next=/my-videos" replace />;

  const items = videos.data?.pages.flatMap((page) => page.items) ?? [];
  const firstLoad = videos.isPending && items.length === 0;

  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <h1>My videos</h1>
          <p className="page-subtitle">
            Everything you have uploaded. This page updates while videos are processing.
          </p>
        </div>
      </div>

      {firstLoad ? (
        <ul className="rows" aria-hidden="true">
          {[0, 1, 2].map((key) => (
            <li className="row" key={key}>
              <div className="row-thumb skeleton skeleton-thumb" />
              <div className="row-main">
                <div className="skeleton skeleton-line" />
                <div className="skeleton skeleton-line skeleton-short" />
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {videos.isError ? (
        <div className="notice notice-error" role="alert">
          <strong>Your videos could not be loaded.</strong>
          <span>{videos.error?.message ?? 'Reload the page to try again.'}</span>
        </div>
      ) : null}
      {!firstLoad && !videos.isError && items.length === 0 ? (
        <div className="empty">
          <strong>You have not uploaded anything yet</strong>
          <span>Upload a video up to 10 minutes long and 500 MB.</span>
          <Link className="button button-primary" to="/upload">
            Upload a video
          </Link>
        </div>
      ) : null}

      {items.length > 0 ? (
        <ul className="rows">
          {items.map((video) => (
            <VideoRow video={video} key={video.id} />
          ))}
        </ul>
      ) : null}

      {videos.hasNextPage ? (
        <div className="load-more">
          <button
            type="button"
            className="button"
            onClick={() => videos.fetchNextPage()}
            disabled={videos.isFetchingNextPage}
          >
            {videos.isFetchingNextPage ? 'Loading…' : 'Show more'}
          </button>
        </div>
      ) : null}
    </main>
  );
}
