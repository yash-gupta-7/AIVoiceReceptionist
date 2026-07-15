import { useCallback, useEffect, useState } from "react";
import { api, Patient } from "../api";

export default function Patients() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [query, setQuery] = useState("");
  const [form, setForm] = useState({ name: "", phone: "", dob: "" });
  const [error, setError] = useState("");

  const refresh = useCallback((q = "") => {
    api<Patient[]>(`/api/patients?q=${encodeURIComponent(q)}`)
      .then(setPatients).catch(console.error);
  }, []);

  useEffect(() => refresh(), [refresh]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/api/patients", { method: "POST", body: JSON.stringify(form) });
      setForm({ name: "", phone: "", dob: "" });
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Patients</h1>
      <form onSubmit={create}
        className="mb-4 grid grid-cols-1 gap-3 rounded-lg bg-white p-4 shadow md:grid-cols-4">
        <input required placeholder="Full name" aria-label="Name" value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          className="rounded border border-slate-300 px-2 py-1.5" />
        <input required placeholder="Phone" aria-label="Phone" value={form.phone}
          onChange={(e) => setForm({ ...form, phone: e.target.value })}
          className="rounded border border-slate-300 px-2 py-1.5" />
        <input type="date" required aria-label="Date of birth" value={form.dob}
          onChange={(e) => setForm({ ...form, dob: e.target.value })}
          className="rounded border border-slate-300 px-2 py-1.5" />
        <button type="submit"
          className="rounded bg-slate-900 py-1.5 text-white hover:bg-slate-700">Add patient</button>
        {error && <p role="alert" className="col-span-full text-sm text-red-600">{error}</p>}
      </form>

      <input placeholder="Search name or phone…" aria-label="Search patients" value={query}
        onChange={(e) => { setQuery(e.target.value); refresh(e.target.value); }}
        className="mb-4 w-full rounded border border-slate-300 px-3 py-2 md:w-80" />

      <div className="overflow-x-auto rounded-lg bg-white shadow">
        <table className="w-full text-left text-sm">
          <thead className="border-b bg-slate-50 text-slate-600">
            <tr><th className="p-3">#</th><th className="p-3">Name</th>
              <th className="p-3">Phone</th><th className="p-3">DOB</th></tr>
          </thead>
          <tbody>
            {patients.map((p) => (
              <tr key={p.id} className="border-b last:border-0">
                <td className="p-3">{p.id}</td><td className="p-3">{p.name}</td>
                <td className="p-3">{p.phone}</td><td className="p-3">{p.dob}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
