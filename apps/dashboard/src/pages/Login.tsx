import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../api";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await api<{ access_token: string }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setToken(res.access_token);
      navigate("/");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100">
      <form onSubmit={submit} className="w-80 rounded-lg bg-white p-6 shadow">
        <h1 className="mb-4 text-lg font-semibold">Clinic Dashboard</h1>
        <label className="mb-1 block text-sm" htmlFor="email">Email</label>
        <input id="email" type="email" required value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mb-3 w-full rounded border border-slate-300 px-2 py-1.5" />
        <label className="mb-1 block text-sm" htmlFor="password">Password</label>
        <input id="password" type="password" required value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-4 w-full rounded border border-slate-300 px-2 py-1.5" />
        {error && <p role="alert" className="mb-3 text-sm text-red-600">{error}</p>}
        <button type="submit"
          className="w-full rounded bg-slate-900 py-2 text-white hover:bg-slate-700">
          Sign in
        </button>
      </form>
    </div>
  );
}
