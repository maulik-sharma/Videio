import { Link, NavLink } from 'react-router';
import { useMe } from '../auth/session.js';
import AccountMenu from './AccountMenu.jsx';
import ThemeToggle from './ThemeToggle.jsx';
import Wordmark from './Wordmark.jsx';

export default function Topbar() {
  const { data: me, isPending } = useMe();

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link to="/" className="brand" aria-label="Videio home">
          <Wordmark />
        </Link>
        <nav className="topbar-nav" aria-label="Main">
          <NavLink to="/" end className="nav-link">
            Home
          </NavLink>
          {me ? (
            <NavLink to="/my-videos" className="nav-link">
              My videos
            </NavLink>
          ) : null}
        </nav>
        <div className="topbar-actions">
          <ThemeToggle />
          {isPending ? null : me ? (
            <>
              <Link to="/upload" className="button button-primary topbar-upload">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                <span>Upload</span>
              </Link>
              <AccountMenu me={me} />
            </>
          ) : (
            <>
              <Link to="/login" className="button button-quiet">
                Log in
              </Link>
              <Link to="/register" className="button button-primary topbar-register">
                Create account
              </Link>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
