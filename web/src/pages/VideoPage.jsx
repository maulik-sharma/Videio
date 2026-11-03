import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import Hls from 'hls.js';
import { useCountVideoView, useFeed, useVideo } from '../api/videos.js';
import Avatar from '../components/Avatar.jsx';
import VideoCard from '../components/VideoCard.jsx';
import { formatDate, formatViews } from '../utils/format.js';

const AUTO = -1;

// Rungs are named by their short side, so a portrait 1080x1920 video is 1080p.
function qualityLabel(level) {
  if (!level) return '';
  const side = Math.min(level.width || level.height, level.height || level.width);
  return side ? `${side}p` : `${Math.round(level.bitrate / 1000)} kbps`;
}

function HlsPlayer({ source }) {
  const videoRef = useRef(null);
  const hlsRef = useRef(null);
  const [levels, setLevels] = useState([]);
  const [chosen, setChosen] = useState(AUTO);
  const [playing, setPlaying] = useState(AUTO);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !source) return undefined;

    if (Hls.isSupported()) {
      const hls = new Hls({ enableWorker: true });
      hlsRef.current = hls;
      hls.on(Hls.Events.MANIFEST_PARSED, (_, data) => setLevels(data.levels));
      hls.on(Hls.Events.LEVEL_SWITCHED, (_, data) => setPlaying(data.level));
      hls.loadSource(source);
      hls.attachMedia(element);
      return () => {
        hls.destroy();
        hlsRef.current = null;
        setLevels([]);
        setChosen(AUTO);
        setPlaying(AUTO);
      };
    }

    // Safari plays HLS natively and picks the quality itself.
    if (element.canPlayType('application/vnd.apple.mpegurl')) element.src = source;
    return undefined;
  }, [source]);

  const choose = (event) => {
    const level = Number(event.target.value);
    setChosen(level);
    if (hlsRef.current) hlsRef.current.currentLevel = level;
  };

  return (
    <>
      <div className="player">
        <video ref={videoRef} controls playsInline />
      </div>
      {levels.length > 1 ? (
        <div className="player-bar">
          <label className="quality">
            <span>Quality</span>
            <select value={chosen} onChange={choose}>
              <option value={AUTO}>
                {chosen === AUTO && levels[playing]
                  ? `Auto (${qualityLabel(levels[playing])})`
                  : 'Auto'}
              </option>
              {levels
                .map((level, index) => ({ index, level }))
                .reverse()
                .map(({ index, level }) => (
                  <option value={index} key={`${level.width}x${level.height}-${level.bitrate}`}>
                    {qualityLabel(level)}
                  </option>
                ))}
            </select>
          </label>
        </div>
      ) : null}
    </>
  );
}

function PlayerPlaceholder({ video }) {
  if (video.status === 'failed') {
    return (
      <div className="player player-message player-failed" role="alert">
        <strong>This video could not be processed</strong>
        <span>{video.failure_reason || 'Unknown error.'} Try uploading the file again.</span>
      </div>
    );
  }
  return (
    <div className="player player-message" role="status">
      <span className="spinner" aria-hidden="true" />
      <strong>Processing</strong>
      <span>Every quality up to the original will be ready in a minute or two.</span>
    </div>
  );
}

function MoreVideos({ currentId }) {
  const feed = useFeed();
  const items = (feed.data?.pages.flatMap((page) => page.items) ?? [])
    .filter((video) => video.public_id !== currentId)
    .slice(0, 12);
  if (feed.isPending || items.length === 0) return null;
  return (
    <aside className="watch-aside" aria-labelledby="more-videos">
      <h2 id="more-videos">More videos</h2>
      <div className="compact-list">
        {items.map((video) => (
          <VideoCard video={video} compact key={video.public_id} />
        ))}
      </div>
    </aside>
  );
}

const VISIBILITY = { unlisted: 'Unlisted', private: 'Private' };

export default function VideoPage() {
  const { publicId } = useParams();
  const video = useVideo(publicId);
  // The id of the last video counted. The router reuses this component when
  // moving between videos, so a plain boolean would skip every later video.
  const counted = useRef(null);
  const data = video.data;
  const { mutate: countView } = useCountVideoView();

  useEffect(() => {
    if (data?.status === 'ready' && counted.current !== publicId) {
      counted.current = publicId;
      countView(publicId);
    }
  }, [countView, data?.status, publicId]);

  useEffect(() => {
    document.title = data?.title ? `${data.title} - Videio` : 'Videio';
    return () => {
      document.title = 'Videio';
    };
  }, [data?.title]);

  if (video.isPending) {
    return (
      <main className="page watch">
        <div className="watch-main">
          <div className="player skeleton" />
          <div className="skeleton skeleton-title" />
        </div>
      </main>
    );
  }
  if (video.isError || !data) {
    return (
      <main className="page">
        <div className="empty">
          <strong>Video unavailable</strong>
          <span>This video does not exist, or it is private.</span>
          <Link className="button" to="/">
            Browse latest videos
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="page watch">
      <div className="watch-main">
        {data.status === 'ready' && data.playback_url ? (
          <HlsPlayer source={data.playback_url} />
        ) : (
          <PlayerPlaceholder video={data} />
        )}
        <h1 className="watch-title">{data.title}</h1>
        <div className="watch-meta">
          <div className="watch-owner">
            <Avatar name={data.owner_name} size="lg" />
            <strong>{data.owner_name}</strong>
          </div>
          <div className="watch-stats">
            {VISIBILITY[data.visibility] ? (
              <span className="badge">{VISIBILITY[data.visibility]}</span>
            ) : null}
            <span>{formatViews(data.view_count)}</span>
            <span>{formatDate(data.created_at)}</span>
          </div>
        </div>
        {data.description ? <p className="watch-description">{data.description}</p> : null}
      </div>
      <MoreVideos currentId={data.public_id} />
    </main>
  );
}
