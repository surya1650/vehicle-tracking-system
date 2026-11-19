import { Camera as CameraIcon, Plus, Radio, Save } from "lucide-react";
import { useState, type FormEvent } from "react";
import { request, useResource, when, type Camera, type EventRow, type LiveStatus } from "./api";
import { Badge, Empty, ErrorNote, SecureImage } from "./components";
import { EventLink } from "./Movements";

export function Live({ token, selectEvent, initialCamera }: {
  token: string; selectEvent: (id: string) => void; initialCamera: string;
}) {
  const [cameraId, setCameraId] = useState(initialCamera);
  const cameras = useResource<Camera[]>("/cameras", token, 5000);
  const listed = cameras.data?.find(item => item.id === cameraId);
  const selected = useResource<Camera>(cameraId && !listed ? `/cameras/${cameraId}` : null, token, 5000);
  const available = cameras.data?.filter(camera => camera.enabled || camera.id === cameraId) || [];
  if (selected.data) available.push(selected.data);
  const camera = cameraId ? listed || selected.data : available[0];
  const path = camera ? `/cameras/${camera.id}` : null;
  const live = useResource<LiveStatus>(path && path + "/live", token, 2000);
  const events = useResource<EventRow[]>(camera ? `/events?camera=${camera.id}&limit=6` : null, token, 5000);
  return <>
    <header className="page-heading"><div><h1>Live monitoring</h1><p>Latest annotated frame and crossing activity by camera.</p></div>
      <label className="camera-selector"><CameraIcon size={17} /><select aria-label="Camera" value={camera?.id || ""}
        onChange={event => setCameraId(event.target.value)}>
        {!camera && <option value="">No camera configured</option>}{available.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label></header>
    {cameras.error && <ErrorNote message={cameras.error} />}
    {selected.error && <ErrorNote message={selected.error} />}
    <div className="live-grid">
      <section className="card feed-card"><div className="section-heading"><div><h2>{camera?.name || "Camera feed"}</h2>
        {camera && <p>{camera.gate_id} · {camera.width && camera.height ? `${camera.width} × ${camera.height}` : "Source resolution"}</p>}</div>
        {camera && <Badge value={camera.status} />}</div>
        <div className="feed"><SecureImage path={path && path + "/image"} token={token} live label="Latest annotated camera frame" /></div>
        <div className="feed-meta"><Radio size={15} /><span>{live.data ? `Frame ${live.data.number} · ${live.data.tracks} visible tracks · ${live.data.crossings} crossings`
          : "No frame telemetry"}</span><span>{live.data ? when(live.data.published_at) : "—"}</span></div></section>
      <section className="card live-events"><div className="section-heading"><div><h2>Camera events</h2><p>Registration, track, and recognition status</p></div></div>
        {events.error && <ErrorNote message={events.error} />}
        {events.data?.length ? events.data.map(row => <EventLink key={row.event_uuid} row={row} selectEvent={selectEvent} />)
          : !events.error && <Empty title="No camera events yet" />}</section>
    </div>
  </>;
}

function CameraForm({ camera, token, onSaved }: { camera: Camera | null; token: string; onSaved: (id: string) => void }) {
  const [state, setState] = useState({
    id: camera?.id || "", name: camera?.name || "", gate_id: camera?.gate_id || "", area: camera?.area || "",
    source_binding: camera?.source_binding || "", direction: camera?.direction || "AUTO", entry_side: camera?.entry_side || "positive",
    width: camera?.width?.toString() || "", height: camera?.height?.toString() || "",
    tripwire: camera?.tripwire?.join(",") || "",
    detection_threshold: camera?.detection_threshold.toString() || "0.1", enabled: camera?.enabled ?? true,
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  function change(key: keyof typeof state, value: string | boolean) {
    setState(current => ({ ...current, [key]: value }));
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const wire = state.tripwire.trim().split(",").map(value => value.trim());
    if (state.tripwire.trim() && (wire.length !== 4 || wire.some(value => !value || !Number.isFinite(Number(value))))) {
      setError("Enter four comma-separated tripwire coordinates.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const body = {
        ...state, width: state.width ? Number(state.width) : null,
        height: state.height ? Number(state.height) : null,
        tripwire: state.tripwire.trim() ? wire.map(Number) : null,
        detection_threshold: Number(state.detection_threshold),
        source_binding: state.source_binding || null,
        area: state.area.trim() || null,
      };
      await request(`/cameras/${state.id}`, token, { method: "PUT", body: JSON.stringify(body) });
      onSaved(state.id);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return <form onSubmit={save} className="camera-form">
    <label>Camera ID<input value={state.id} onChange={e => change("id", e.target.value)} required readOnly={!!camera} /></label>
    <label>Camera name<input value={state.name} onChange={e => change("name", e.target.value)} required maxLength={120} /></label>
    <label>Gate ID<input value={state.gate_id} onChange={e => change("gate_id", e.target.value)} required /></label>
    <label>Camera area<input value={state.area} maxLength={120} onChange={e => change("area", e.target.value)}
      placeholder="e.g. North car park" /></label>
    <label>Camera mode<select value={state.direction} onChange={e => change("direction", e.target.value)}>
      <option value="AUTO">Automatic</option><option value="ENTRY">Entry only</option><option value="EXIT">Exit only</option>
      <option value="TRACKING">Tracking / sightings</option></select></label>
    {state.direction === "AUTO" && <label className="wide">Entry side<select value={state.entry_side} onChange={e => change("entry_side", e.target.value)}>
      <option value="positive">Positive side of directed line</option><option value="negative">Negative side of directed line</option></select></label>}
    <label className="wide">Source variable<input value={state.source_binding} onChange={e => change("source_binding", e.target.value)}
      placeholder="CAMERA_SOURCE_MAIN_GATE" /><small>Environment variable name configured on the camera worker.</small></label>
    <label>Width (px)<input type="number" min={1} max={16384} value={state.width} onChange={e => change("width", e.target.value)} /></label>
    <label>Height (px)<input type="number" min={1} max={16384} value={state.height} onChange={e => change("height", e.target.value)} /></label>
    <label className="wide">Tripwire coordinates<input value={state.tripwire} onChange={e => change("tripwire", e.target.value)} placeholder="x1,y1,x2,y2" /></label>
    <label>Detection threshold<input type="number" min={0.01} max={1} step={0.01} required value={state.detection_threshold}
      onChange={e => change("detection_threshold", e.target.value)} /></label>
    <label className="check-label"><input type="checkbox" checked={state.enabled} onChange={e => change("enabled", e.target.checked)} /> Camera enabled</label>
    <div className="wide">{error && <ErrorNote message={error} />}<button className="button primary" disabled={saving}><Save size={16} /> {saving ? "Saving…" : "Save camera"}</button></div>
  </form>;
}

export function Cameras({ token, admin, revision, refresh }: {
  token: string; admin: boolean; revision: number; refresh: () => void;
}) {
  const cameras = useResource<Camera[]>("/cameras", token, 10000, revision);
  const [selected, setSelected] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const current = cameras.data?.find(camera => camera.id === selected) || null;
  return <>
    <header className="page-heading"><div><h1>Camera administration</h1><p>Manage gate assignments, source bindings, and tripwire settings.</p></div>
      {admin && <button className="button primary" onClick={() => { setSelected(""); setMessage(""); }}><Plus size={17} /> Add camera</button>}</header>
    {cameras.error && <ErrorNote message={cameras.error} />}
    {message && <p role="status" className="form-status">{message}</p>}
    <div className="admin-grid">
      <section className="card table-card"><div className="section-heading"><h2>Cameras</h2></div>
        {cameras.data?.length ? cameras.data.map(camera => <button className={`camera-row ${camera.id === selected ? "selected" : ""}`} key={camera.id}
          onClick={() => { setSelected(camera.id); setMessage(""); }}><span><strong>{camera.name}</strong><small>{camera.id} · {camera.gate_id}</small></span>
          <Badge value={camera.status} /></button>) : <Empty title={cameras.data ? "No cameras configured" : "Loading cameras"} />}</section>
      <section className="card table-card camera-settings"><div className="section-heading"><h2>{current ? current.name : selected === "" ? "New camera" : "Camera settings"}</h2></div>
        {admin && (current || selected === "") ? <CameraForm key={selected} camera={current} token={token} onSaved={id => {
          setSelected(id); setMessage("Camera settings saved."); refresh();
        }} /> : current ? <dl className="camera-detail">
          <dt>Gate</dt><dd>{current.gate_id}</dd><dt>Camera area</dt><dd>{current.area || "—"}</dd>
          <dt>Direction</dt><dd>{current.direction}</dd>
          <dt>Source variable</dt><dd>{current.source_binding || "—"}</dd><dt>Resolution</dt><dd>{current.width || "—"} × {current.height || "—"}</dd>
          <dt>Tripwire</dt><dd>{current.tripwire?.join(", ") || "—"}</dd><dt>Detection threshold</dt><dd>{current.detection_threshold}</dd>
          <dt>Last seen</dt><dd>{when(current.last_seen)}</dd></dl>
          : <Empty title="Select a camera" detail={admin ? "Choose a configured camera or add a new one." : undefined} />}
      </section>
    </div>
  </>;
}
