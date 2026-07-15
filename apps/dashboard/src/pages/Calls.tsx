import { useEffect, useState } from "react";
import { api, Call, Message } from "../api";

export default function Calls() {
  const [calls, setCalls] = useState<Call[]>([]);
  const [selected, setSelected] = useState<Call | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);

  useEffect(() => {
    const load = () => api<Call[]>("/api/calls").then(setCalls).catch(console.error);
    load();
    const timer = setInterval(load, 10000); // live-ish call list
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (selected) {
      api<Message[]>(`/api/calls/${selected.id}/messages`)
        .then(setMessages).catch(console.error);
    }
  }, [selected]);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div>
        <h1 className="mb-4 text-xl font-semibold">Calls</h1>
        <div className="overflow-x-auto rounded-lg bg-white shadow">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-slate-50 text-slate-600">
              <tr><th className="p-3">Caller</th><th className="p-3">Started</th>
                <th className="p-3">Outcome</th></tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id} onClick={() => setSelected(c)}
                  className={`cursor-pointer border-b last:border-0 hover:bg-slate-50 ${
                    selected?.id === c.id ? "bg-slate-100" : ""}`}>
                  <td className="p-3">{c.caller}</td>
                  <td className="p-3">{new Date(c.started_at).toLocaleString()}</td>
                  <td className="p-3">{c.outcome ?? (c.ended_at ? "ended" : "🟢 live")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div>
        <h2 className="mb-4 text-xl font-semibold">
          {selected ? `Transcript — ${selected.caller}` : "Select a call"}
        </h2>
        {selected && (
          <div className="space-y-2 rounded-lg bg-white p-4 shadow">
            {messages.map((m, i) => (
              <div key={i} className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                m.role === "caller" ? "bg-slate-100" : "ml-auto bg-blue-50"}`}>
                <p className="mb-0.5 text-xs text-slate-500">
                  {m.role}{m.intent ? ` · ${m.intent}` : ""}
                </p>
                {m.text}
              </div>
            ))}
            {messages.length === 0 && <p className="text-sm text-slate-500">No messages.</p>}
          </div>
        )}
      </div>
    </div>
  );
}
