import { Link } from 'react-router';
import { useFeed } from '../api/videos.js';
import { useMe } from '../auth/session.js';
import CardSkeletons from '../components/Skeleton.jsx';
import VideoCard from '../components/VideoCard.jsx';

export default function Home() {
  const feed = useFeed();
  const { data: me } = useMe();
  const items = feed.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <h1>Latest videos</h1>
          <p className="page-subtitle">Public videos, newest first.</p>
        </div>
      </div>

      {feed.isPending ? <CardSkeletons /> : null}
      {feed.isError ? (
        <div className="notice notice-error" role="alert">
          <strong>The feed could not be loaded.</strong>
          <span>Check your connection and reload the page.</span>
        </div>
      ) : null}
      {!feed.isPending && !feed.isError && items.length === 0 ? (
        <div className="empty">
          <strong>No public videos yet</strong>
          <span>Videos appear here once they are public and finished processing.</span>
          <Link className="button button-primary" to={me ? '/upload' : '/register'}>
            {me ? 'Upload a video' : 'Create an account to upload'}
          </Link>
        </div>
      ) : null}

      {items.length > 0 ? (
        <div className="video-grid">
          {items.map((video) => (
            <VideoCard video={video} key={video.public_id} />
          ))}
        </div>
      ) : null}

      {feed.hasNextPage ? (
        <div className="load-more">
          <button
            type="button"
            className="button"
            onClick={() => feed.fetchNextPage()}
            disabled={feed.isFetchingNextPage}
          >
            {feed.isFetchingNextPage ? 'Loading…' : 'Show more videos'}
          </button>
        </div>
      ) : null}
    </main>
  );
}
