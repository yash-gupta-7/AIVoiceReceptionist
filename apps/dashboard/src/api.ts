// Thin fetch wrapper: JWT from localStorage, JSON errors surfaced as exceptions.
const TOKEN_KEY = "clinic_token";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      ...options.headers,
    },
  });
  if (res.status === 401) {
    clearToken();
    window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail ?? "Request failed");
  }
  return res.json();
}

export interface Patient {
  id: number;
  name: string;
  phone: string;
  dob: string;
  insurance: string | null;
}
export interface Doctor {
  id: number;
  name: string;
  department: string;
}
export interface Appointment {
  id: number;
  patient_id: number;
  doctor_id: number;
  starts_at: string;
  status: string;
}
export interface Call {
  id: number;
  sid: string;
  caller: string;
  language: string;
  state: string;
  outcome: string | null;
  started_at: string;
  ended_at: string | null;
}
export interface Message {
  role: string;
  text: string;
  intent: string | null;
  created_at: string;
}
export interface Metrics {
  calls_total: number;
  calls_active: number;
  appointments_booked: number;
  patients_total: number;
  appointments_today: number;
}
