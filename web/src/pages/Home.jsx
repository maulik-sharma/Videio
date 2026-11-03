import { useEffect, useState } from 'react';
import { useMe } from '../auth/session.js';

function useApiHealth() {
  const [health, setHealth] = useState({ state: 'checking' });

  useEffect(() => {
    const controller = new AbortController();
    fetch('/healthz', { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((body) => setHealth({ state: 'ok', version: body.version }))
      .catch((err) => {
        if (err.name !== 'AbortError') setHealth({ state: 'error', message: err.message });
      });
    return () => controller.abort();
  }, []);

  return health;
}

function ApiStatus() {
  const health = useApiHealth();
  if (health.state === 'checking') return <span className="badge">Checking API</span>;
  if (health.state === 'error')
    return <span className="badge badge-error">API unreachable: {health.message}</span>;
  return <span className="badge badge-ok">API ok, version {health.version}</span>;
}

export default function Home() {
  const { data: me } = useMe();
  return (
    <main className="page">
      <h1>Nothing to watch yet</h1>
      <p className="muted">
        Uploads, the feed and playback arrive in later phases. This page confirms the build and
        deploy pipeline works end to end.
      </p>
      <dl className="facts">
        <dt>API</dt>
        <dd>
          <ApiStatus />
        </dd>
        <dt>Session</dt>
        <dd>{me ? `Logged in as ${me.email}` : 'Not logged in'}</dd>
      </dl>
    </main>
  );
}
