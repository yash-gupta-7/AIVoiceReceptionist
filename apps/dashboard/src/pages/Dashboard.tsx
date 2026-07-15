import { useEffect, useState } from "react";
import { api, Metrics } from "../api";

const CARDS: { key: keyof Metrics; label: string }[] = [
  { key: "calls_total", label: "Total calls" },
  { key: "calls_active", label: "Active calls" },
  { key: "appointments_today", label: "Appointments today" },
  { key: "appointments_booked", label: "Open bookings" },
  { key: "patients_total", label: "Patients" },
];

export default function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  useEffect(() => {
    api<Metrics>("/api/metrics").then(setMetrics).catch(console.error);
    const timer = setInterval(
      () => api<Metrics>("/api/metrics").then(setMetrics).catch(console.error),
      10000,
    );
    return () => clearInterval(timer);
  }, []);

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Overview</h1>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        {CARDS.map(({ key, label }) => (
          <div key={key} className="rounded-lg bg-white p-4 shadow">
            <p className="text-sm text-slate-500">{label}</p>
            <p className="text-2xl font-semibold">{metrics ? metrics[key] : "…"}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
