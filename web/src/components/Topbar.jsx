import { Link } from 'react-router';
import { useMe } from '../auth/session.js';
import AccountMenu from './AccountMenu.jsx';
import { MenuIcon, PlusIcon } from './icons.jsx';
import ThemeToggle from './ThemeToggle.jsx';
import Wordmark from './Wordmark.jsx';

export default function Topbar({ onMenu, menuLabel }) {
  const { data: me, isPending } = useMe();

  return (
    <header className="topbar">
      <div className="topbar-start">
        <button
          type="button"
          className="icon-button menu-button"
          aria-label={menuLabel}
          onClick={onMenu}
        >
          <MenuIcon />
        </button>
        <Link to="/" className="brand" aria-label="Videio home">
          <Wordmark />
        </Link>
      </div>
      <div className="topbar-actions">
        <ThemeToggle />
        {isPending ? null : me ? (
          <>
            <Link to="/upload" className="button topbar-upload">
              <PlusIcon />
              <span>Upload</span>
            </Link>
            <AccountMenu me={me} />
          </>
        ) : (
          <>
            <Link to="/login" className="button button-quiet topbar-login">
              Log in
            </Link>
            <Link to="/register" className="button button-primary">
              Create account
            </Link>
          </>
        )}
      </div>
    </header>
  );
}
