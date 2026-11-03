import { Link } from 'react-router';
import { formatRelative, formatViews } from '../utils/format.js';
import Avatar from './Avatar.jsx';
import Thumbnail from './Thumbnail.jsx';

// `compact` is the narrow list used beside the player.
export default function VideoCard({ video, compact = false }) {
  const meta = (
    <span className="card-meta">
      <span>{formatViews(video.view_count)}</span>
      <span>{formatRelative(video.created_at)}</span>
    </span>
  );
  return (
    <Link className={compact ? 'card card-compact' : 'card'} to={`/v/${video.public_id}`}>
      <Thumbnail
        src={video.thumbnail_url}
        duration={video.duration_s}
        renditions={video.renditions}
      />
      <div className="card-body">
        {compact ? null : <Avatar name={video.owner_name} />}
        <div className="card-text">
          <span className="card-title">{video.title}</span>
          <span className="card-owner">{video.owner_name}</span>
          {meta}
        </div>
      </div>
    </Link>
  );
}
