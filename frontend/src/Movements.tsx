import { ArrowUpRight, Filter, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  displayPlate, duration, percent, readable, request, useResource, when,
  type EventDetail, type EventRow, type Session,
} from "./api";
import { Badge, Dialog, Empty, ErrorNote, EventTable, Pager, SecureImage } from "./components";

const PAGE_SIZE = 20;
type Filters = { plate: string; gate: string; direction: string; verification: string; since: string; until: string };
const initialFilters: Filters = { plate: "", gate: "", direction: "", verification: "", since: "", until: "" };

export function History({ token, review = false, revision, selectEvent }: {
  token: string; review?: boolean; revision: number; selectEvent: (id: string) => void;
}) {
  const [filters, setFilters] = useState(initialFilters);
  const [applied, setApplied] = useState(initialFilters);
  const [page, setPage] = useState(0);
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
  for (const [key, value] of Object.entries(applied)) {
    if (!value.trim()) continue;
    if (key === "since" || key === "until") {
      const time = new Date(value + "T00:00:00");
      if (key === "until") time.setDate(time.getDate() + 1);
      params.set(key, time.toISOString());
    } else params.set(key, value.trim());
  }
  if (review) params.set("verification", "PENDING");
  const { data, error } = useResource<EventRow[]>(`/vehicles/history?${params}`, token, review ? 5000 : 0, revision);
  function change(key: keyof Filters, value: string) { setFilters(current => ({ ...current, [key]: value })); }
  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setApplied(filters);
    setPage(0);
  }
  return <>
    <header className="page-heading"><div><h1>{review ? "Recognition review" : "Movement history"}</h1>
      <p>{review ? "Inspect plate evidence and verify unresolved events." : "Search entries, exits, and camera sightings."}</p></div></header>
    {!review && <form className="card filter-card" onSubmit={apply}>
      <label>Registration<div className="input-icon"><Search size={16} /><input placeholder="Search plates" value={filters.plate}
        onChange={e => change("plate", e.target.value)} maxLength={24} /></div></label>
      <label>Gate<input value={filters.gate} onChange={e => change("gate", e.target.value)} placeholder="All gates" /></label>
      <label>Direction<select value={filters.direction} onChange={e => change("direction", e.target.value)}>
        <option value="">All movements</option><option value="IN">Entry</option><option value="OUT">Exit</option><option value="SIGHTING">Sighting</option>
      </select></label>
      <label>Verification<select value={filters.verification} onChange={e => change("verification", e.target.value)}>
        <option value="">All states</option><option value="PENDING">Needs review</option><option value="AUTO">Automatic</option><option value="VERIFIED">Verified</option>
      </select></label>
      <label>From<input type="date" value={filters.since} onChange={e => change("since", e.target.value)} /></label>
      <label>Through<input type="date" min={filters.since || undefined} value={filters.until} onChange={e => change("until", e.target.value)} /></label>
      <button className="button primary"><Filter size={15} /> Filter</button>
    </form>}
    <section className="card table-card"><div className="section-heading"><h2>{review ? "Review queue" : "Recorded movements"}</h2></div>
      {error && <ErrorNote message={error} />}
      {data ? <><EventTable rows={data} onEvent={selectEvent} /><Pager page={page} length={data.length} size={PAGE_SIZE} onPage={setPage} /></>
        : !error && <Empty title="Loading movements" />}
    </section>
  </>;
}

export function Inside({ token, revision, selectEvent }: {
  token: string; revision: number; selectEvent: (id: string) => void;
}) {
  const [page, setPage] = useState(0);
  const { data, error } = useResource<Session[]>(
    `/vehicles/inside?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`, token, 15000, revision,
  );
  return <>
    <header className="page-heading"><div><h1>Vehicles inside</h1><p>Open visits by registration, entry gate, and time on site.</p></div></header>
    <section className="card table-card">
      {error && <ErrorNote message={error} />}
      {!data?.length ? !error && <Empty title={data ? "No open visits" : "Loading vehicles"} />
        : <div className="table-wrap"><table>
          <thead><tr><th>Registration</th><th>Vehicle category</th><th>Entry gate</th><th>Entry time</th><th>Duration</th><th>Entry snapshot</th><th>Status</th></tr></thead>
          <tbody>{data.map(item => <tr key={item.id}><td className="plate">{item.registration_number}</td>
            <td className="capitalize">{item.vehicle_type || "—"}</td><td>{item.entry_gate || "—"}</td>
            <td>{when(item.entry_time)}</td><td>{duration(item.duration_seconds)}</td><td>
              {item.entry_event_id && <button className="thumbnail" onClick={() => selectEvent(item.entry_event_id!)} aria-label={`View entry for ${item.registration_number}`}>
                <SecureImage path={`/events/${item.entry_event_id}/evidence/vehicle`} token={token} label="Entry snapshot" /></button>}
            </td><td><Badge value={item.status} /></td></tr>)}</tbody>
        </table></div>}
      {data && <Pager page={page} length={data.length} size={PAGE_SIZE} onPage={setPage} />}
    </section>
  </>;
}

function VerificationForm({ row, token, onSaved }: {
  row: EventDetail; token: string; onSaved: (event: EventDetail) => void;
}) {
  const [plate, setPlate] = useState(row.registration_number || row.plate_number || "");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const verified = await request<EventDetail>("/events/manual-verify", token, {
        method: "POST", body: JSON.stringify({ event_uuid: row.event_uuid, plate_number: plate, notes }),
      });
      setSaved(true);
      onSaved(verified);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return <form className="verification-form" onSubmit={submit}>
    <label>Corrected registration<input value={plate} maxLength={24} required pattern="[A-Z0-9]+"
      onChange={event => { setSaved(false); setPlate(event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "")); }} /></label>
    <label>Notes<input value={notes} maxLength={500} placeholder="Optional audit note" onChange={event => setNotes(event.target.value)} /></label>
    <button className="button primary" disabled={saving || saved}>{saving ? "Saving…" : "Verify registration"}</button>
    {error && <ErrorNote message={error} />}{saved && <span role="status" className="form-status">Correction saved</span>}
  </form>;
}

export function EventPanel({ id, token, operator, close, onSaved }: {
  id: string; token: string; operator: boolean; close: () => void; onSaved: () => void;
}) {
  const [verified, setVerified] = useState<EventDetail>();
  const { data: original, error } = useResource<EventDetail>(`/events/${id}`, token);
  const data = verified ?? original;
  return <Dialog title="Movement details" close={close}>
    {error && <ErrorNote message={error} />}
    {data ? <>
      <div className="dialog-meta"><div><strong className="plate">{displayPlate(data)}</strong><p>{when(data.timestamp)} · {data.gate_id} · {data.camera_id}</p></div>
        <div><Badge value={data.direction} /> <Badge value={data.verification_status} /></div></div>
      {data.reason && <p className="review-reason">Recognition result <strong>{readable(data.reason)}</strong></p>}
      <div className="evidence-grid">{(["vehicle", "plate"] as const).map(kind => <figure key={kind}>
        <SecureImage path={data.evidence.includes(kind) ? `/events/${id}/evidence/${kind}` : null} token={token}
          label={kind === "vehicle" ? "Vehicle evidence" : "Plate crop"} />
        <figcaption>{kind === "vehicle" ? "Vehicle snapshot" : "Plate crop"}</figcaption></figure>)}</div>
      <h3>OCR observations</h3>
      {data.observations.length ? <div className="table-wrap observations"><table>
        <thead><tr><th>Frame</th><th>Raw</th><th>Normalized</th><th>OCR</th><th>Plate</th><th>Association</th></tr></thead>
        <tbody>{data.observations.map((item, index) => <tr key={index}><td>{item.frame_number}</td><td>{item.raw_text || "—"}</td>
          <td>{item.normalized_text || "—"}{!item.valid_format && item.normalized_text && <div className="subtle">Format needs review</div>}</td>
          <td>{percent(item.ocr_confidence)}</td><td>{percent(item.plate_confidence)}</td><td><Badge value={item.association} /></td></tr>)}</tbody>
      </table></div> : <Empty title="No plate observations recorded" />}
      {operator && <VerificationForm row={data} token={token} onSaved={updated => {
        setVerified(updated);
        onSaved();
      }} />}
    </> : !error && <Empty title="Loading movement details" />}
  </Dialog>;
}

export function EventLink({ row, selectEvent }: { row: EventRow; selectEvent: (id: string) => void }) {
  return <button className="event-link" onClick={() => selectEvent(row.event_uuid)}>
    <span><strong className="plate">{displayPlate(row)}</strong><small>{when(row.timestamp)} · Track {row.track_id ?? "—"}</small>
    <small>OCR {percent(row.ocr_confidence)} · {readable(row.recognition_status || row.verification_status)}</small></span>
    <span className="event-link-right"><Badge value={row.direction} /><ArrowUpRight size={15} /></span>
  </button>;
}
