import { useCallback, useState } from 'react';
import { Link, Route, Routes, useLocation } from 'react-router';
import { useMe } from './auth/session.js';
import { Drawer, Sidebar } from './components/Sidebar.jsx';
import Topbar from './components/Topbar.jsx';
import { readPref, writePref } from './utils/prefs.js';
import AuthPage from './pages/AuthPage.jsx';
import Home from './pages/Home.jsx';
import MyVideos from './pages/MyVideos.jsx';
import Upload from './pages/Upload.jsx';
import VideoPage from './pages/VideoPage.jsx';

function NotFound() {
  return (
    <main className="page">
      <div className="empty">
        <strong>Page not found</strong>
        <span>There is nothing at this address.</span>
        <Link className="button" to="/">
          Browse latest videos
        </Link>
      </div>
    </main>
  );
}

const SIDEBAR_KEY = 'videio-sidebar';
// Wide enough for the full sidebar; narrower screens get the icon rail.
const WIDE = '(min-width: 1200px)';
// Below this there is no room for a rail at all.
const NARROW = '(max-width: 720px)';

const matches = (query) => window.matchMedia?.(query).matches ?? false;

export default function App() {
  const { data: me } = useMe();
  const { pathname } = useLocation();
  const [expanded, setExpanded] = useState(() => readPref(SIDEBAR_KEY, 'open') === 'open');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);

  // The watch page gives all its width to the player; navigation lives in the drawer.
  const watching = pathname.startsWith('/v/');
  // The menu button collapses the full sidebar where there is one; elsewhere
  // (rail, phones, watch page) it opens the drawer.
  const togglesSidebar = () => !watching && matches(WIDE) && !matches(NARROW);

  const onMenu = () => {
    if (togglesSidebar()) {
      const next = !expanded;
      setExpanded(next);
      writePref(SIDEBAR_KEY, next ? 'open' : 'closed');
    } else {
      setDrawerOpen(true);
    }
  };

  const layout = watching ? 'shell shell-bare' : expanded ? 'shell shell-full' : 'shell shell-rail';

  return (
    <>
      <Topbar onMenu={onMenu} menuLabel="Menu" />
      <div className={layout}>
        {watching ? null : <Sidebar me={me} />}
        <div className="shell-content">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/my-videos" element={<MyVideos />} />
            <Route path="/v/:publicId" element={<VideoPage />} />
            <Route path="/login" element={<AuthPage mode="login" key="login" />} />
            <Route path="/register" element={<AuthPage mode="register" key="register" />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </div>
      </div>
      <Drawer me={me} open={drawerOpen} onClose={closeDrawer} />
    </>
  );
}
