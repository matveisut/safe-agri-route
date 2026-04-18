import { useState, FormEvent } from 'react';
import axios from 'axios';

interface Props {
  onLogin: () => void;
}

export default function LoginPage({ onLogin }: Props) {
  const [email, setEmail] = useState('operator@safegriroute.com');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.append('username', email);
      params.append('password', password);

      const res = await axios.post(
        `${import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}/auth/login`,
        params,
        { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
      );
      localStorage.setItem('access_token', res.data.access_token);
      onLogin();
    } catch {
      setError('Invalid credentials');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex h-screen w-full items-center justify-center bg-slate-900">
      <div className="w-full max-w-sm bg-slate-800 rounded-2xl border border-slate-700 p-8 shadow-2xl">
        <h1 className="text-2xl font-black text-center bg-clip-text text-transparent bg-gradient-to-r from-emerald-400 to-cyan-400 mb-1">
          SafeAgriRoute
        </h1>
        <p className="text-xs text-slate-400 text-center uppercase tracking-widest mb-8">
          Mission Control
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>

          {error && <p className="text-red-400 text-xs">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold py-2.5 rounded-xl transition-all"
          >
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>

        <p className="text-xs text-slate-500 text-center mt-6">
          operator@safegriroute.com / operator123
        </p>
      </div>
    </div>
  );
}
