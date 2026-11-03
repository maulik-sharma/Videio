import { useEffect, useRef } from 'react';
import { Link, NavLink } from 'react-router';
import { HomeIcon, LibraryIcon, MenuIcon, SignInIcon, UploadIcon } from './icons.jsx';
import Wordmark from './Wordmark.jsx';

function SideLink({ to, icon, label, onNavigate, end = false }) {
  return (
    <NavLink to={to} end={end} className="side-link" onClick={onNavigate}>
      {icon}
      <span>{label}</span>
    </NavLink>
  );
}

// The links themselves. The rail shows icon-over-label; the full sidebar and
// the drawer show icon-beside-label plus section headings.
function SideLinks({ me, onNavigate }) {
  return (
    <>
      <div className="side-section">
        <SideLink to="/" end icon={<HomeIcon />} label="Home" onNavigate={onNavigate} />
        {me ? (
          <>
            <SideLink
              to="/my-videos"
              icon={<LibraryIcon />}
              label="My videos"
              onNavigate={onNavigate}
            />
            <SideLink to="/upload" icon={<UploadIcon />} label="Upload" onNavigate={onNavigate} />
          </>
        ) : (
          <SideLink to="/login" icon={<SignInIcon />} label="Log in" onNavigate={onNavigate} />
        )}
      </div>
      {me ? null : (
        <div className="side-section side-prompt">
          <p>Log in to upload videos and keep track of them.</p>
          <Link to="/register" className="button" onClick={onNavigate}>
            Create account
          </Link>
        </div>
      )}
    </>
  );
}

export function Sidebar({ me }) {
  return (
    <nav className="sidebar" aria-label="Main">
      <SideLinks me={me} />
    </nav>
  );
}

// Slide-over version for phones and the watch page.
export function Drawer({ me, open, onClose }) {
  const panelRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement;
    panelRef.current?.querySelector('a, button')?.focus();
    const onKey = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="drawer-layer">
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <nav className="drawer" aria-label="Main" role="dialog" aria-modal="true" ref={panelRef}>
        <div className="drawer-head">
          <button type="button" className="icon-button" aria-label="Close menu" onClick={onClose}>
            <MenuIcon />
          </button>
          <Link to="/" className="brand" aria-label="Videio home" onClick={onClose}>
            <Wordmark />
          </Link>
        </div>
        <SideLinks me={me} onNavigate={onClose} />
      </nav>
    </div>
  );
}
