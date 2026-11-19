import { ArrowRight, Camera, CarFront, ClipboardCheck, History as HistoryIcon, LayoutDashboard, LogOut, ParkingSquare, Route, Settings } from "lucide-react";
import { createRoot } from "react-dom/client";
import { useEffect, useState, type FormEvent } from "react";
import { request, type Credential } from "./api";
import { Cameras, Live } from "./Cameras";
import { ErrorNote } from "./components";
import { EventPanel, History, Inside } from "./Movements";
import { Overview } from "./Overview";
import { VehicleTracking } from "./VehicleTracking";
import "./styles.css";

const navigation = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "live", label: "Live monitoring", icon: Camera },
  { key: "tracking", label: "Vehicle tracking", icon: Route },
  { key: "inside", label: "Vehicles inside", icon: ParkingSquare },
  { key: "history", label: "Movement history", icon: HistoryIcon },
  { key: "review", label: "Recognition review", icon: ClipboardCheck },
  { key: "cameras", label: "Camera administration", icon: Settings },
] as const;
type Page = typeof navigation[number]["key"];
const SESSION = "gate-monitor-session";

function Logo() {
  return <div className="brand"><span><CarFront size={22} /></span><div><strong>Gate Monitor</strong><small>Vehicle tracking</small></div></div>;
}

function SignIn({ onLogin }: { onLogin: (credential: Credential) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError("");
    try {
      const session = await request<Credential>("/auth/login", "", {
        method: "POST", body: JSON.stringify({ username, password }),
      });
      if (session.role === "INGEST") throw new Error("Use a dashboard account to sign in.");
      onLogin(session);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setWorking(false);
    }
  }
  return <div className="login-shell">
    <div className="login-brand"><Logo /></div>
    <form className="card login-card" onSubmit={submit}>
      <h1>Sign in</h1><p>Open your vehicle monitoring workspace.</p>
      <label>Username<input autoComplete="username" value={username} required onChange={e => setUsername(e.target.value)} /></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} required onChange={e => setPassword(e.target.value)} /></label>
      {error && <ErrorNote message={error} />}
      <button className="button primary" disabled={working}>{working ? "Signing in…" : "Sign in"} <ArrowRight size={16} /></button>
    </form>
  </div>;
}

function App() {
  const [credential, setCredential] = useState<Credential | null>(() => {
    try { return JSON.parse(sessionStorage.getItem(SESSION) || "null"); } catch { return null; }
  });
  const [page, setPage] = useState<Page>("overview");
  const [revision, setRevision] = useState(0);
  const [event, setEvent] = useState<string | null>(null);
  const [liveCamera, setLiveCamera] = useState("");
  function signOut() {
    sessionStorage.removeItem(SESSION);
    setCredential(null);
    setEvent(null);
    setPage("overview");
  }
  useEffect(() => {
    window.addEventListener("session-expired", signOut);
    return () => window.removeEventListener("session-expired", signOut);
  }, []);
  if (!credential) return <SignIn onLogin={session => {
    sessionStorage.setItem(SESSION, JSON.stringify(session));
    setCredential(session);
  }} />;
  const token = credential.access_token;
  const refresh = () => setRevision(value => value + 1);
  return <div className="shell">
    <aside className="sidebar"><Logo /><nav aria-label="Main navigation">
      {navigation.map(({ key, label, icon: Icon }) => <button key={key} aria-label={label} aria-current={page === key ? "page" : undefined}
        className={page === key ? "active" : ""} onClick={() => {
          if (key === "live" && page !== "live") setLiveCamera("");
          setPage(key);
        }}><Icon size={18} /><span>{label}</span></button>)}
    </nav></aside>
    <div className="workspace"><header className="topbar">
      <span>{navigation.find(item => item.key === page)?.label}</span>
      <div className="profile"><span className="avatar">{credential.username.slice(0, 2).toUpperCase()}</span>
        <div><strong>{credential.username}</strong><small>{credential.role.toLowerCase()}</small></div>
        <button className="icon-button" aria-label="Sign out" onClick={signOut}><LogOut size={17} /></button></div>
    </header>
      <main className="content">
        {page === "overview" && <Overview token={token} revision={revision} refresh={refresh} selectEvent={setEvent} />}
        {page === "live" && <Live token={token} selectEvent={setEvent} initialCamera={liveCamera} />}
        {page === "tracking" && <VehicleTracking token={token} role={credential.role} revision={revision} selectEvent={setEvent} openCamera={id => {
          setLiveCamera(id);
          setPage("live");
        }} />}
        {page === "inside" && <Inside token={token} revision={revision} selectEvent={setEvent} />}
        {page === "history" && <History key="history" token={token} revision={revision} selectEvent={setEvent} />}
        {page === "review" && <History key="review" review token={token} revision={revision} selectEvent={setEvent} />}
        {page === "cameras" && <Cameras token={token} admin={credential.role === "ADMIN"} revision={revision} refresh={refresh} />}
      </main>
    </div>
    {event && <EventPanel key={event} id={event} token={token} operator={["OPERATOR", "ADMIN"].includes(credential.role)}
      close={() => setEvent(null)} onSaved={refresh} />}
  </div>;
}

createRoot(document.getElementById("root")!).render(<App />);
