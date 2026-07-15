import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { clearToken, getToken } from "./api";
import Appointments from "./pages/Appointments";
import Calls from "./pages/Calls";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Patients from "./pages/Patients";

const NAV = [
  { to: "/", label: "Dashboard" },
  { to: "/appointments", label: "Appointments" },
  { to: "/patients", label: "Patients" },
  { to: "/calls", label: "Calls" },
];

function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  if (!getToken()) return <Navigate to="/login" replace />;
  return (
    <div className="min-h-screen bg-slate-100">
      <nav className="bg-slate-900 text-white" aria-label="Main navigation">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-1 px-4 py-3">
          <span className="mr-6 font-semibold">🩺 Clinic Receptionist</span>
          {NAV.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              aria-current={location.pathname === item.to ? "page" : undefined}
              className={`rounded px-3 py-1.5 text-sm hover:bg-slate-700 ${
                location.pathname === item.to ? "bg-slate-700" : ""
              }`}
            >
              {item.label}
            </Link>
          ))}
          <button
            onClick={() => {
              clearToken();
              navigate("/login");
            }}
            className="ml-auto rounded px-3 py-1.5 text-sm hover:bg-slate-700"
          >
            Log out
          </button>
        </div>
      </nav>
      <main className="mx-auto max-w-6xl p-4">{children}</main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Layout><Dashboard /></Layout>} />
      <Route path="/appointments" element={<Layout><Appointments /></Layout>} />
      <Route path="/patients" element={<Layout><Patients /></Layout>} />
      <Route path="/calls" element={<Layout><Calls /></Layout>} />
    </Routes>
  );
}
