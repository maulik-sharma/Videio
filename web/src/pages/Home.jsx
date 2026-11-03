import { Link } from 'react-router';
import { useFeed } from '../api/videos.js';

export default function Home() {
  const feed = useFeed();
  const items = feed.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <h1>Public videos</h1>
          <p className="muted">Ready videos from the public feed.</p>
        </div>
        <Link className="button button-primary" to="/upload">
          Upload a video
        </Link>
      </div>
      {feed.isPending ? <p className="muted">Loading feed…</p> : null}
      {feed.isError ? <p className="form-error">Could not load the public feed.</p> : null}
      {!feed.isPending && !feed.isError && items.length === 0 ? (
        <div className="empty-state">
          <strong>No public videos yet.</strong>
          <span>Upload a video and make it public to see it here.</span>
        </div>
      ) : null}
      {items.length > 0 ? (
        <div className="feed-grid">
          {items.map((video) => (
            <Link className="video-card" to={`/v/${video.public_id}`} key={video.public_id}>
              {video.thumbnail_url ? (
                <img src={video.thumbnail_url} alt="" />
              ) : (
                <div className="video-placeholder">No thumbnail</div>
              )}
              <strong>{video.title}</strong>
              <span className="muted">{video.view_count} views</span>
            </Link>
          ))}
        </div>
      ) : null}
      {feed.hasNextPage ? (
        <button
          type="button"
          className="button load-more"
          onClick={() => feed.fetchNextPage()}
          disabled={feed.isFetchingNextPage}
        >
          {feed.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </button>
      ) : null}
    </main>
  );
}
