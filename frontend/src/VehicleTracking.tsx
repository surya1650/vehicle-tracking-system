import { ArrowUpRight, Camera, MapPin, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { duration, useResource, when, type Parking, type VehicleRoute } from "./api";
import { Badge, Empty, ErrorNote, SecureImage } from "./components";
import { ParkingEditor, RegistryCheckEditor, VehicleDetails, VehicleProfileEditor, VisitHistory } from "./VehicleSiteRecords";

export function VehicleTracking({ token, role, revision, selectEvent, openCamera }: {
  token: string;
  role: string;
  revision: number;
  selectEvent: (id: string) => void;
  openCamera: (id: string) => void;
}) {
  const [input, setInput] = useState("");
  const [plate, setPlate] = useState("");
  const [lookup, setLookup] = useState(0);
  const [editProfile, setEditProfile] = useState(false);
  const [editRegistry, setEditRegistry] = useState(false);
  const [editParking, setEditParking] = useState<{ entryEventId: string; parking: Parking | null }>();
  const [message, setMessage] = useState("");
  const path = plate ? `/vehicles/${encodeURIComponent(plate)}/route` : null;
  const route = useResource<VehicleRoute>(path, token, 10000, revision + lookup);
  const result = route.data;
  const latest = result?.history[0];
  const inside = result?.visit_status === "INSIDE";
  const admin = role === "ADMIN";
  const operator = admin || role === "OPERATOR";
  const parking = result?.parking ?? null;

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPlate(input);
    setLookup(value => value + 1);
    setMessage("");
  }

  function saved(text: string) {
    setEditProfile(false);
    setEditRegistry(false);
    setEditParking(undefined);
    setLookup(value => value + 1);
    setMessage(text);
  }

  return <>
    <header className="page-heading">
      <div><h1>Vehicle tracking</h1><p>Find parking, visits, camera sightings, and vehicle details by registration.</p></div>
    </header>
    <form className="card route-search" onSubmit={search}>
      <label>Registration
        <div className="input-icon"><Search size={16} /><input className="plate" value={input}
          onChange={event => setInput(event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))}
          placeholder="Enter plate number" maxLength={24} required spellCheck={false} /></div>
      </label>
      <button className="button primary">Find vehicle</button>
    </form>
    {!plate && <div className="card route-section"><Empty title="Enter a vehicle registration"
      detail="Confirmed entries, sightings, and exits will appear here." /></div>}
    {message && <p className="form-status" role="status">{message}</p>}
    {plate && !result && !route.error && <p className="muted route-section" role="status">Looking up {plate}…</p>}
    {route.errorStatus === 404 ? <section className="card route-section route-summary">
      <div className="route-location"><div><h2>Vehicle not found</h2><p>{plate}</p></div></div>
      {admin && <div className="record-actions">
        <button className="button secondary" onClick={() => setEditProfile(true)}>Add site record</button>
        <button className="button secondary" onClick={() => setEditRegistry(true)}>Record official check</button>
      </div>}
    </section> : route.error && <ErrorNote message={route.error} />}
    {result && <>
      <section className="card route-section route-summary" aria-label={latest ? "Last confirmed camera" : "Vehicle status"}>
        <div className="route-location">
          <span className="route-pin"><MapPin size={23} /></span>
          {latest ? <div>
            <p>Last confirmed camera</p>
            <h2>{latest.camera_name}</h2>
            {latest.camera_area && <p>Camera area · {latest.camera_area}</p>}
            <time dateTime={latest.timestamp}>{when(latest.timestamp)}</time>
          </div> : <h2>No confirmed camera events</h2>}
        </div>
        <div className="route-visit">
          <span className="plate">{result.registration_number}</span>
          <Badge value={result.visit_status} />
        </div>
        {latest && <button className="button secondary" onClick={() => openCamera(latest.camera_id)}>
          <Camera size={16} /> Open camera
        </button>}
      </section>
      <div className="route-section site-records-grid">
        <div className="site-visit">
          <section className="card">
            <div className="section-heading"><h2>{inside ? "Current visit" : "Latest visit"}</h2></div>
            <dl className="record-details">
              <div><dt>Entered</dt><dd>{result.entry_time ? when(result.entry_time) : "No entry recorded"}</dd></div>
              <div><dt>Left</dt><dd>{result.exit_time ? when(result.exit_time) : inside ? "Still inside" : "No exit recorded"}</dd></div>
              <div><dt>Time on site</dt><dd>{duration(result.duration_seconds)}</dd></div>
            </dl>
          </section>
          <section className="card">
            <div className="section-heading"><h2>{parking && !inside ? "Last confirmed parking" : "Parking"}</h2>
              {inside && operator && result.entry_event_id && <button className="button secondary" onClick={() => {
                if (result.entry_event_id) setEditParking({ entryEventId: result.entry_event_id, parking });
              }}>{parking?.current ? "Update parking" : "Confirm parking"}</button>}</div>
            {parking ? <div className="parking-location">
              <MapPin size={19} />
              <div><h3>{parking.zone}{parking.bay && ` · Bay ${parking.bay}`}</h3>
                <p>Confirmed {when(parking.confirmed_at)}</p></div>
              {inside && !parking.current && <Badge value="NEEDS_RECONFIRMATION" />}
            </div> : <p className="parking-empty">No parking confirmation on record for this visit.</p>}
          </section>
        </div>
        <VehicleDetails profile={result.profile} siteDate={result.site_date} admin={admin}
          onEdit={() => setEditProfile(true)} onRegistry={() => setEditRegistry(true)} />
      </div>
      {latest && <section className="card route-section">
        <div className="section-heading"><h2>Recent camera history</h2></div>
        <ol className="route-events">
          {result.history.slice().reverse().map(row => (
            <li className="route-event" key={row.event_uuid}>
              <div className="route-event-image">
                <SecureImage path={row.evidence.includes("vehicle") ? `/events/${row.event_uuid}/evidence/vehicle` : null}
                  token={token} label={`Vehicle at ${row.camera_name}`} />
              </div>
              <div className="route-event-details">
                <div><div className="route-event-badges"><Badge value={row.direction} />
                  {row.verification_status === "VERIFIED" && <Badge value="VERIFIED" />}</div>
                  <h3>{row.camera_name}</h3>
                  <time dateTime={row.timestamp}>{when(row.timestamp)}</time></div>
                <button className="text-button" onClick={() => selectEvent(row.event_uuid)}
                  aria-label={`View evidence for ${row.camera_name}`}>Evidence <ArrowUpRight size={14} /></button>
              </div>
            </li>
          ))}
        </ol>
      </section>}
      <VisitHistory key={plate} plate={plate} token={token} revision={revision + lookup} />
    </>}
    {editProfile && <VehicleProfileEditor plate={plate} profile={result?.profile} token={token}
      close={() => setEditProfile(false)} onSaved={() => saved("Vehicle details saved.")} />}
    {editRegistry && <RegistryCheckEditor plate={plate} token={token}
      close={() => setEditRegistry(false)} onSaved={() => saved("Official check recorded.")} />}
    {editParking && <ParkingEditor plate={plate} {...editParking} token={token}
      close={() => setEditParking(undefined)} onSaved={saved} />}
  </>;
}
