import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import Hls from 'hls.js';
import { useCountVideoView, useVideo } from '../api/videos.js';

function HlsPlayer({ source }) {
  const videoRef = useRef(null);
  const [levels, setLevels] = useState([]);
  const hlsRef = useRef(null);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !source) return undefined;

    if (Hls.isSupported()) {
      const hls = new Hls({ enableWorker: true });
      hlsRef.current = hls;
      hls.on(Hls.Events.MANIFEST_PARSED, (_, data) => setLevels(data.levels));
      hls.loadSource(source);
      hls.attachMedia(element);
      return () => {
        hls.destroy();
        hlsRef.current = null;
        setLevels([]);
      };
    }

    if (element.canPlayType('application/vnd.apple.mpegurl')) element.src = source;
    return undefined;
  }, [source]);

  return (
    <div className="player-wrap">
      <video ref={videoRef} controls playsInline />
      {levels.length > 0 ? (
        <label className="quality-control">
          Quality
          <select
            defaultValue="-1"
            onChange={(event) => {
              if (hlsRef.current) hlsRef.current.currentLevel = Number(event.target.value);
            }}
          >
            <option value="-1">Auto</option>
            {levels.map((level, index) => (
              <option value={index} key={`${level.height}-${level.bitrate}`}>
                {level.height}p
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}

export default function VideoPage() {
  const { publicId } = useParams();
  const video = useVideo(publicId);
  const countView = useCountVideoView();
  const counted = useRef(false);
  const data = video.data;

  useEffect(() => {
    if (data?.status === 'ready' && !counted.current) {
      counted.current = true;
      countView.mutate(publicId);
    }
  }, [countView, data?.status, publicId]);

  if (video.isPending) return <main className="page">Loading video…</main>;
  if (video.isError)
    return (
      <main className="page">
        <h1>Video unavailable</h1>
        <p className="muted">That video does not exist or is not available to you.</p>
        <Link to="/">Back to feed</Link>
      </main>
    );
  if (!data) return null;

  return (
    <main className="page page-video">
      <Link to="/">Back to feed</Link>
      <h1>{data.title}</h1>
      <p className="muted">{data.description || 'No description'}</p>
      {data.status === 'ready' && data.playback_url ? (
        <HlsPlayer source={data.playback_url} />
      ) : data.status === 'failed' ? (
        <p className="form-error">Processing failed: {data.failure_reason || 'unknown error'}.</p>
      ) : (
        <div className="empty-state">
          <strong>Processing</strong>
          <span>720p will be ready in a minute or two.</span>
        </div>
      )}
      <div className="video-meta video-page-meta">
        <span>{data.view_count} views</span>
        <span>{data.visibility}</span>
      </div>
    </main>
  );
}
