import { Link, NavLink, useNavigate } from 'react-router';
import { useLogout, useMe } from '../auth/session.js';

export default function Topbar() {
  const { data: me, isPending } = useMe();
  const logout = useLogout();
  const navigate = useNavigate();

  return (
    <header className="topbar">
      <Link to="/" className="brand">
        Videio
      </Link>
      <nav className="topbar-actions" aria-label="Account">
        {isPending ? null : me ? (
          <>
            <span className="topbar-user">
              {me.display_name}
              {me.role === 'admin' ? <span className="badge badge-inline">Admin</span> : null}
            </span>
            <button
              type="button"
              className="button button-quiet"
              disabled={logout.isPending}
              onClick={() => logout.mutate(undefined, { onSettled: () => navigate('/') })}
            >
              {logout.isPending ? 'Logging out…' : 'Log out'}
            </button>
          </>
        ) : (
          <>
            <NavLink to="/login" className="nav-link">
              Log in
            </NavLink>
            <NavLink to="/register" className="nav-link">
              Create account
            </NavLink>
          </>
        )}
      </nav>
    </header>
  );
}
