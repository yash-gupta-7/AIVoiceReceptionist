import { useCallback, useEffect, useState } from "react";
import { api, Appointment, Doctor, Patient } from "../api";

export default function Appointments() {
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [form, setForm] = useState({ patient_id: "", doctor_id: "", date: "", time: "" });
  const [slots, setSlots] = useState<string[]>([]);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    api<Appointment[]>("/api/appointments").then(setAppointments).catch(console.error);
  }, []);

  useEffect(() => {
    refresh();
    api<Doctor[]>("/api/doctors").then(setDoctors).catch(console.error);
    api<Patient[]>("/api/patients").then(setPatients).catch(console.error);
  }, [refresh]);

  useEffect(() => {
    if (form.doctor_id && form.date) {
      api<{ slots: string[] }>(`/api/doctors/${form.doctor_id}/availability?day=${form.date}`)
        .then((r) => setSlots(r.slots))
        .catch(() => setSlots([]));
    }
  }, [form.doctor_id, form.date]);

  const doctorName = (id: number) => doctors.find((d) => d.id === id)?.name ?? id;
  const patientName = (id: number) => patients.find((p) => p.id === id)?.name ?? id;

  async function book(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/api/appointments", {
        method: "POST",
        body: JSON.stringify({
          patient_id: Number(form.patient_id),
          doctor_id: Number(form.doctor_id),
          starts_at: form.time,
        }),
      });
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function cancel(id: number) {
    if (!confirm(`Cancel appointment #${id}?`)) return;
    await api(`/api/appointments/${id}`, { method: "DELETE" });
    refresh();
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Appointments</h1>
      <form onSubmit={book}
        className="mb-6 grid grid-cols-1 gap-3 rounded-lg bg-white p-4 shadow md:grid-cols-5">
        <select required aria-label="Patient" value={form.patient_id}
          onChange={(e) => setForm({ ...form, patient_id: e.target.value })}
          className="rounded border border-slate-300 px-2 py-1.5">
          <option value="">Patient…</option>
          {patients.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select required aria-label="Doctor" value={form.doctor_id}
          onChange={(e) => setForm({ ...form, doctor_id: e.target.value })}
          className="rounded border border-slate-300 px-2 py-1.5">
          <option value="">Doctor…</option>
          {doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        <input type="date" required aria-label="Date" value={form.date}
          onChange={(e) => setForm({ ...form, date: e.target.value })}
          className="rounded border border-slate-300 px-2 py-1.5" />
        <select required aria-label="Time slot" value={form.time}
          onChange={(e) => setForm({ ...form, time: e.target.value })}
          className="rounded border border-slate-300 px-2 py-1.5">
          <option value="">Slot…</option>
          {slots.map((s) => (
            <option key={s} value={s}>
              {new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </option>
          ))}
        </select>
        <button type="submit"
          className="rounded bg-slate-900 py-1.5 text-white hover:bg-slate-700">Book</button>
        {error && <p role="alert" className="col-span-full text-sm text-red-600">{error}</p>}
      </form>

      <div className="overflow-x-auto rounded-lg bg-white shadow">
        <table className="w-full text-left text-sm">
          <thead className="border-b bg-slate-50 text-slate-600">
            <tr>
              <th className="p-3">#</th><th className="p-3">Patient</th>
              <th className="p-3">Doctor</th><th className="p-3">When</th>
              <th className="p-3">Status</th><th className="p-3"></th>
            </tr>
          </thead>
          <tbody>
            {appointments.map((a) => (
              <tr key={a.id} className="border-b last:border-0">
                <td className="p-3">{a.id}</td>
                <td className="p-3">{patientName(a.patient_id)}</td>
                <td className="p-3">{doctorName(a.doctor_id)}</td>
                <td className="p-3">{new Date(a.starts_at).toLocaleString()}</td>
                <td className="p-3">
                  <span className={`rounded px-2 py-0.5 text-xs ${
                    a.status === "booked" ? "bg-green-100 text-green-800"
                      : "bg-slate-200 text-slate-600"}`}>{a.status}</span>
                </td>
                <td className="p-3">
                  {a.status === "booked" && (
                    <button onClick={() => cancel(a.id)}
                      className="text-red-600 hover:underline">Cancel</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
