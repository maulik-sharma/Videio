import { useEffect, useId, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { useLogout } from '../auth/session.js';
import Avatar from './Avatar.jsx';

export default function AccountMenu({ me }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const menuId = useId();
  const logout = useLogout();
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const close = () => setOpen(false);

  return (
    <div className="account" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="account-button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`Account: ${me.display_name}`}
        onClick={() => setOpen((value) => !value)}
      >
        <Avatar name={me.display_name} />
      </button>
      {open ? (
        <div className="account-menu" id={menuId}>
          <div className="account-who">
            <Avatar name={me.display_name} size="lg" />
            <div>
              <strong>
                {me.display_name}
                {me.role === 'admin' ? <span className="badge">Admin</span> : null}
              </strong>
              <span>{me.email}</span>
            </div>
          </div>
          <Link to="/my-videos" className="menu-item" onClick={close}>
            My videos
          </Link>
          <Link to="/upload" className="menu-item" onClick={close}>
            Upload a video
          </Link>
          <button
            type="button"
            className="menu-item"
            disabled={logout.isPending}
            onClick={() =>
              logout.mutate(undefined, {
                onSettled: () => {
                  close();
                  navigate('/');
                },
              })
            }
          >
            {logout.isPending ? 'Logging out…' : 'Log out'}
          </button>
        </div>
      ) : null}
    </div>
  );
}
