import { Route, Routes } from 'react-router';
import Topbar from './components/Topbar.jsx';
import AuthPage from './pages/AuthPage.jsx';
import Home from './pages/Home.jsx';
import MyVideos from './pages/MyVideos.jsx';
import Upload from './pages/Upload.jsx';
import VideoPage from './pages/VideoPage.jsx';

function NotFound() {
  return (
    <main className="page">
      <h1>Page not found</h1>
      <p className="muted">There is nothing at this address.</p>
    </main>
  );
}

export default function App() {
  return (
    <>
      <Topbar />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/my-videos" element={<MyVideos />} />
        <Route path="/v/:publicId" element={<VideoPage />} />
        <Route path="/login" element={<AuthPage mode="login" key="login" />} />
        <Route path="/register" element={<AuthPage mode="register" key="register" />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </>
  );
}
