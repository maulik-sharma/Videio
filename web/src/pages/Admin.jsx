import { useState } from 'react';
import { Link, Navigate } from 'react-router';
import { useMe } from '../auth/session.js';
import {
  useAdminVideos,
  useAuditLog,
  useDisableUser,
  useRestore,
  useSystem,
  useTakedown,
} from '../api/admin.js';
import ReasonDialog from '../components/ReasonDialog.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import Thumbnail from '../components/Thumbnail.jsx';
import { formatBytes, formatDate, formatRelative } from '../utils/format.js';

const number = new Intl.NumberFormat('en');

function count(value, one, many) {
  return `${number.format(value)} ${value === 1 ? one : many}`;
}

function cacheDetail(stats) {
  return `${count(stats.hits, 'hit', 'hits')}, ${count(stats.misses, 'miss', 'misses')}`;
}

function percent(ratio) {
  return ratio === null || ratio === undefined ? '-' : `${Math.round(ratio * 100)}%`;
}

function Stat({ label, value, detail, tone }) {
  return (
    <div className={tone ? `stat stat-${tone}` : 'stat'}>
      <dt>{label}</dt>
      <dd>
        <span className="stat-value">{value}</span>
        {detail ? <span className="stat-detail">{detail}</span> : null}
      </dd>
    </div>
  );
}

function StatGroup({ title, children, note }) {
  return (
    <section className="stat-group" aria-label={title}>
      <h3>{title}</h3>
      <dl className="stats">{children}</dl>
      {note ? <p className="stat-note">{note}</p> : null}
    </section>
  );
}

function SystemPanel() {
  const system = useSystem();
  if (system.isPending) return <div className="skeleton stat-skeleton" aria-hidden="true" />;
  if (system.isError) {
    return (
      <div className="notice notice-error" role="alert">
        <strong>System numbers could not be loaded.</strong>
        <span>{system.error.message}</span>
      </div>
    );
  }
  const data = system.data;
  const queue = Object.fromEntries(data.queues.queues.map((item) => [item.name, item]));
  const tasks = queue['videio.tasks'];
  const delayed = queue['videio.tasks.delay'];
  const dlq = queue['transcode.dlq'];
  const [replica] = data.replication.replicas;
  const statuses = data.videos_by_status;

  return (
    <div className="system">
      <StatGroup
        title="Queue"
        note={data.queues.error ?? 'Transcode jobs on RabbitMQ. Ready: waiting for a worker.'}
      >
        <Stat
          label="Ready"
          value={tasks ? number.format(tasks.ready) : '-'}
          detail={tasks ? `${tasks.consumers} worker${tasks.consumers === 1 ? '' : 's'}` : null}
        />
        <Stat label="In progress" value={tasks ? number.format(tasks.unacked) : '-'} />
        <Stat label="Waiting to retry" value={delayed ? number.format(delayed.ready) : '-'} />
        <Stat
          label="Dead-lettered"
          value={dlq ? number.format(dlq.ready) : '-'}
          detail={dlq && dlq.ready > 0 ? 'Needs a look' : 'None'}
          tone={dlq && dlq.ready > 0 ? 'bad' : null}
        />
      </StatGroup>

      <StatGroup title="Videos" note="By processing status, not counting removed videos.">
        <Stat label="Ready" value={number.format(statuses.ready)} />
        <Stat
          label="Processing"
          value={number.format(statuses.processing + statuses.uploaded)}
          detail={`${statuses.uploaded} queued`}
        />
        <Stat label="Waiting for upload" value={number.format(statuses.pending_upload)} />
        <Stat
          label="Failed"
          value={number.format(statuses.failed)}
          tone={statuses.failed > 0 ? 'bad' : null}
        />
        <Stat label="Removed" value={number.format(data.removed_videos)} />
      </StatGroup>

      <StatGroup
        title="Database replica"
        note={
          data.replication.read_db_in_recovery
            ? 'Reads go to a hot standby.'
            : 'The read engine is not on a standby.'
        }
      >
        <Stat
          label="State"
          value={replica ? replica.state : 'Not connected'}
          tone={replica?.state === 'streaming' ? null : 'bad'}
        />
        <Stat
          label="Lag"
          value={!replica ? '-' : replica.lag_bytes === 0 ? 'None' : formatBytes(replica.lag_bytes)}
        />
        <Stat
          label="Replay delay"
          value={
            replica?.replay_lag_seconds === null || !replica
              ? 'Caught up'
              : `${replica.replay_lag_seconds.toFixed(2)} s`
          }
        />
      </StatGroup>

      <StatGroup title="Cache" note="Since Redis last started.">
        <Stat
          label="Video pages"
          value={percent(data.cache.video.hit_ratio)}
          detail={cacheDetail(data.cache.video)}
        />
        <Stat
          label="Feed"
          value={percent(data.cache.feed.hit_ratio)}
          detail={cacheDetail(data.cache.feed)}
        />
        <Stat
          label="Videos with new views"
          value={number.format(data.views_pending_flush)}
          detail="Saved to Postgres every minute"
        />
      </StatGroup>
    </div>
  );
}

const FILTERS = [
  ['all', 'All'],
  ['live', 'Live'],
  ['removed', 'Removed'],
];

function VideoTable() {
  const [filter, setFilter] = useState('all');
  const [action, setAction] = useState(null); // { kind, video }
  const videos = useAdminVideos(filter);
  const takedown = useTakedown();
  const restore = useRestore();
  const disable = useDisableUser();
  const items = videos.data?.pages.flatMap((page) => page.items) ?? [];
  const close = () => setAction(null);

  const dialogs = {
    takedown: {
      title: 'Take this video down?',
      description: 'It disappears for everyone but admins. The owner sees your reason.',
      confirmLabel: 'Take down',
      danger: true,
      mutation: takedown,
      id: (video) => video.public_id,
    },
    restore: {
      title: 'Restore this video?',
      description: 'It comes back with the visibility the owner chose.',
      confirmLabel: 'Restore',
      mutation: restore,
      id: (video) => video.public_id,
    },
    disable: {
      title: 'Disable this account?',
      description:
        'The owner is signed out everywhere and cannot log in again. Their videos stay up.',
      confirmLabel: 'Disable account',
      danger: true,
      mutation: disable,
      id: (video) => video.owner_id,
    },
  };
  const current = action ? dialogs[action.kind] : null;

  return (
    <section aria-labelledby="admin-videos">
      <div className="section-heading">
        <h2 id="admin-videos">Videos</h2>
        <div className="segmented" role="group" aria-label="Show">
          {FILTERS.map(([value, label]) => (
            <button
              type="button"
              key={value}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {videos.isError ? (
        <div className="notice notice-error" role="alert">
          <span>{videos.error.message}</span>
        </div>
      ) : null}
      {!videos.isPending && !videos.isError && items.length === 0 ? (
        <div className="empty">
          <strong>No videos here</strong>
          <span>
            {filter === 'removed' ? 'Nothing has been taken down.' : 'Nobody has uploaded yet.'}
          </span>
        </div>
      ) : null}

      {items.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Video</th>
                <th scope="col">Owner</th>
                <th scope="col">Status</th>
                <th scope="col">Visibility</th>
                <th scope="col">Uploaded</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((video) => (
                <tr key={video.id}>
                  <td>
                    <div className="table-video">
                      <Thumbnail src={video.thumbnail_url} renditions={video.renditions} />
                      <Link to={`/v/${video.public_id}`}>{video.title}</Link>
                    </div>
                  </td>
                  <td>
                    <div className="table-owner">
                      <span>{video.owner_name}</span>
                      <span className="table-sub">{video.owner_email}</span>
                      {video.owner_disabled ? <span className="badge">Disabled</span> : null}
                    </div>
                  </td>
                  <td>
                    <div className="table-status">
                      <StatusBadge
                        status={video.status}
                        removedReason={video.removed_at ? video.removal_reason : null}
                        short
                      />
                      {video.removed_at || video.status === 'failed' ? (
                        <span className="table-sub">
                          {video.removed_at ? video.removal_reason : video.failure_reason}
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td className="table-cap">{video.visibility}</td>
                  <td className="table-nowrap" title={formatDate(video.created_at)}>
                    {formatRelative(video.created_at)}
                  </td>
                  <td>
                    <div className="table-actions">
                      {video.removed_at ? (
                        <button
                          type="button"
                          className="button button-small"
                          onClick={() => setAction({ kind: 'restore', video })}
                        >
                          Restore
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="button button-small button-quiet-danger"
                          onClick={() => setAction({ kind: 'takedown', video })}
                        >
                          Take down
                        </button>
                      )}
                      {video.owner_disabled ? null : (
                        <button
                          type="button"
                          className="button button-small button-quiet"
                          onClick={() => setAction({ kind: 'disable', video })}
                        >
                          Disable owner
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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

      {current ? (
        <ReasonDialog
          open
          key={`${action.kind}-${action.video.id}`}
          onClose={close}
          title={current.title}
          description={
            action.kind === 'disable'
              ? `${action.video.owner_name} (${action.video.owner_email}). ${current.description}`
              : `"${action.video.title}". ${current.description}`
          }
          confirmLabel={current.confirmLabel}
          danger={current.danger}
          mutation={current.mutation}
          onConfirm={(reason, done) =>
            current.mutation.mutate({ id: current.id(action.video), reason }, { onSuccess: done })
          }
        />
      ) : null}
    </section>
  );
}

const ACTIONS = {
  'video.takedown': 'took down',
  'video.restore': 'restored',
  'user.disable': 'disabled',
};

function AuditLog() {
  const audit = useAuditLog();
  if (audit.isPending || audit.isError) return null;
  return (
    <section aria-labelledby="admin-audit">
      <div className="section-heading">
        <h2 id="admin-audit">Recent admin actions</h2>
      </div>
      {audit.data.length === 0 ? (
        <p className="page-subtitle">No admin actions yet.</p>
      ) : (
        <ol className="audit">
          {audit.data.map((entry) => (
            <li key={entry.id}>
              <span>
                <strong>{entry.actor_name ?? 'A deleted admin'}</strong>{' '}
                {ACTIONS[entry.action] ?? entry.action}{' '}
                {entry.target_type === 'video' ? (
                  <Link to={`/v/${entry.target_id}`}>video {entry.target_id}</Link>
                ) : (
                  <>an account</>
                )}
                : {entry.reason}
              </span>
              <time dateTime={entry.created_at} title={formatDate(entry.created_at)}>
                {formatRelative(entry.created_at)}
              </time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function Admin() {
  const { data: me, isPending } = useMe();
  if (isPending) return <main className="page" aria-busy="true" />;
  if (!me) return <Navigate to="/login?next=/admin" replace />;
  if (me.role !== 'admin') {
    return (
      <main className="page">
        <div className="empty">
          <strong>Admins only</strong>
          <span>Your account cannot open this page.</span>
          <Link className="button" to="/">
            Browse latest videos
          </Link>
        </div>
      </main>
    );
  }
  return (
    <main className="page page-admin">
      <div className="page-heading">
        <div>
          <h1>Admin</h1>
          <p className="page-subtitle">System numbers refresh every 5 seconds.</p>
        </div>
      </div>
      <SystemPanel />
      <VideoTable />
      <AuditLog />
    </main>
  );
}
