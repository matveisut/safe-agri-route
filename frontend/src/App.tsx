import { useState, useEffect } from 'react';
import MapArea from './features/MapDashboard/MapArea';
import MissionPanel from './features/MissionControl/MissionPanel';
import DroneStatusPanel from './components/DroneStatusPanel';
import LoginPage from './components/LoginPage';

function App() {
  const [authed, setAuthed] = useState(() => !!localStorage.getItem('access_token'));

  useEffect(() => {
    const handler = () => setAuthed(false);
    window.addEventListener('auth:logout', handler);
    return () => window.removeEventListener('auth:logout', handler);
  }, []);

  if (!authed) {
    return <LoginPage onLogin={() => setAuthed(true)} />;
  }

  return (
    <div className="flex h-screen w-full bg-slate-900 overflow-hidden text-slate-100 font-sans">
      {/* Sidebar */}
      <div className="w-80 bg-slate-800 shadow-2xl z-10 flex flex-col p-6 border-r border-slate-700 overflow-y-auto">
        <div className="mb-8">
          <h1 className="text-2xl font-black bg-clip-text text-transparent bg-gradient-to-r from-emerald-400 to-cyan-400">
            SafeAgriRoute
          </h1>
          <p className="text-xs text-slate-400 mt-1 uppercase tracking-widest font-semibold">
            Mission Control
          </p>
        </div>

        <MissionPanel />

        {/* Drone status panel — only visible during an active mission */}
        <div className="mt-6">
          <DroneStatusPanel />
        </div>

        <div className="mt-auto pt-6 text-center text-xs text-slate-500 border-t border-slate-700/50">
          <p>Cybersecurity Routing Matrix MVP</p>
          <button
            onClick={() => { localStorage.removeItem('access_token'); setAuthed(false); }}
            className="mt-3 text-slate-600 hover:text-slate-400 transition-colors"
          >
            Sign out
          </button>
        </div>
      </div>

      {/* Main Map Viewer */}
      <div className="flex-1 p-4 md:p-6 bg-slate-900 relative">
        <MapArea />
      </div>
    </div>
  );
}

export default App;
