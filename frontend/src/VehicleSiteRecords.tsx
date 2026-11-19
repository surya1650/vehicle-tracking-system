import { useState, type FormEvent } from "react";
import {
  duration, request, useResource, when, type Parking, type ProfileSource, type RegistryService, type Session,
  type VehicleProfile,
} from "./api";
import { Badge, Dialog, Empty, ErrorNote, Pager } from "./components";

const profileFields = [
  ["owner_or_department", "Owner or department", 120],
  ["manufacturer", "Manufacturer", 80],
  ["model", "Model", 80],
  ["color", "Color", 40],
  ["fuel_type", "Fuel type", 40],
  ["puc_certificate_number", "PUC certificate number", 80],
] as const;
const expiryFields = [
  ["registration_valid_until", "Registration"],
  ["puc_valid_until", "Pollution (PUC)"],
  ["insurance_valid_until", "Insurance"],
  ["fitness_valid_until", "Fitness"],
] as const;
const fields = [...profileFields.map(([id, label, size]) => ({ id, label, size, type: "text" })),
  ...expiryFields.map(([id, label]) => ({ id, label: `${label} valid until`, size: undefined, type: "date" }))];
const expiryDate = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });
const registries = {
  VAHAN_RC: {
    label: "VAHAN registration (RC)",
    url: "https://vahan.parivahan.gov.in/nrservices/faces/user/login.xhtml",
  },
  PARIVAHAN_PUC: {
    label: "Parivahan pollution certificate (PUCC)",
    url: "https://puc.parivahan.gov.in/puc/views/PucCertificate.xhtml",
  },
};

function RecordSource({ record }: { record: ProfileSource | undefined }) {
  if (!record || record.source === "SITE") return null;
  return <small className="registry-source">{registries[record.source].label} · {when(record.checked_at)}</small>;
}

export function VehicleDetails({ profile, siteDate, admin, onEdit, onRegistry }: {
  profile: VehicleProfile; siteDate: string; admin: boolean; onEdit: () => void; onRegistry: () => void;
}) {
  const details = profileFields.filter(([id]) => profile[id]);
  return <section className="card">
    <div className="section-heading record-heading"><h2>Vehicle details</h2>
      {admin && <div className="record-actions">
        <button className="button secondary" onClick={onEdit}>Edit details</button>
        <button className="button secondary" onClick={onRegistry}>Record official check</button>
      </div>}</div>
    <dl className="record-details">
      {profile.vehicle_type && <div><dt>Vehicle type</dt><dd className="capitalize">{profile.vehicle_type}</dd></div>}
      {details.map(([id, label]) => <div key={id}><dt>{label}</dt><dd>{profile[id]}
        <RecordSource record={profile.sources[id]} /></dd></div>)}
      {expiryFields.map(([id, label]) => {
        const expiry = profile[id];
        return <div key={id}><dt>{label}</dt><dd>
          {expiry ? <><span className="record-validity"><time dateTime={expiry}>{expiryDate.format(new Date(expiry))}</time>
            <Badge value={expiry < siteDate ? "EXPIRED" : "VALID"} /></span>
            <RecordSource record={profile.sources[id]} /></> : "No expiry recorded"}
        </dd></div>;
      })}
    </dl>
    <div className="record-links">
      <a className="text-button" href={registries.VAHAN_RC.url}
        target="_blank" rel="noreferrer">Open VAHAN</a>
      <a className="text-button" href={registries.PARIVAHAN_PUC.url}
        target="_blank" rel="noreferrer">Check PUC certificate</a>
    </div>
  </section>;
}

export function RegistryCheckEditor({ plate, token, close, onSaved }: {
  plate: string; token: string; close: () => void; onSaved: () => void;
}) {
  const [service, setService] = useState<RegistryService>("VAHAN_RC");
  const [registration, setRegistration] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const official = registries[service];
  const visible = fields.filter(({ id }) => id !== "owner_or_department"
    && id.startsWith("puc_") === (service === "PARIVAHAN_PUC"));
  const details = Object.fromEntries(visible.flatMap(({ id }) => {
    const value = values[id]?.trim();
    return value ? [[id, value]] : [];
  }));

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      await request<VehicleProfile>(`/vehicles/${plate}/registry-check`, token, {
        method: "POST", body: JSON.stringify({ service, registration_number: registration, details }),
      });
      onSaved();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return <Dialog title={`Record official check for ${plate}`} close={close}>
    <form className="record-form" onSubmit={save}>
      <label>Service<select value={service} onChange={event => setService(event.target.value as RegistryService)}>
        <option value="VAHAN_RC">{registries.VAHAN_RC.label}</option>
        <option value="PARIVAHAN_PUC">{registries.PARIVAHAN_PUC.label}</option>
      </select></label>
      <label>Registration on official record<input value={registration} required maxLength={24}
        onChange={event => setRegistration(event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))} /></label>
      <p className="wide subtle">Enter the fields you checked on the official site. <a className="text-button"
        href={official.url} target="_blank" rel="noreferrer">Open {official.label}</a></p>
      {visible.map(({ id, label, type, size }) => <label key={id}>{label}
        <input type={type} maxLength={size} value={values[id] || ""}
          onChange={event => setValues(previous => ({ ...previous, [id]: event.target.value }))} />
      </label>)}
      {error && <div className="wide"><ErrorNote message={error} /></div>}
      <div className="wide"><button className="button primary" disabled={saving || !Object.keys(details).length}>
        {saving ? "Saving…" : "Save check"}</button></div>
    </form>
  </Dialog>;
}

export function VehicleProfileEditor({ plate, profile, token, close, onSaved }: {
  plate: string; profile?: VehicleProfile; token: string; close: () => void; onSaved: () => void;
}) {
  const [initial] = useState(profile);
  const [values, setValues] = useState(() => Object.fromEntries(fields.map(({ id }) => [id, initial?.[id] || ""])));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const update = Object.fromEntries(fields.flatMap(({ id }) => {
    const value = values[id].trim() || null;
    return value === (initial?.[id] ?? null) ? [] : [[id, value]];
  }));
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      await request<VehicleProfile>(`/vehicles/${plate}/profile`, token, {
        method: "PATCH", body: JSON.stringify(update),
      });
      onSaved();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return <Dialog title={`Vehicle details for ${plate}`} close={close}>
    <form className="record-form" onSubmit={save}>
      {fields.map(({ id, label, type, size }) => <label key={id}>{label}
        <input type={type} maxLength={size} value={values[id]}
          onChange={event => setValues(previous => ({ ...previous, [id]: event.target.value }))} />
      </label>)}
      {error && <div className="wide"><ErrorNote message={error} /></div>}
      <div className="wide"><button className="button primary" disabled={saving || !Object.keys(update).length}>
        {saving ? "Saving…" : "Save details"}</button></div>
    </form>
  </Dialog>;
}

export function ParkingEditor({ plate, entryEventId, parking, token, close, onSaved }: {
  plate: string; entryEventId: string; parking: Parking | null; token: string;
  close: () => void; onSaved: (message: string) => void;
}) {
  const [zone, setZone] = useState(parking?.zone || "");
  const [bay, setBay] = useState(parking?.bay || "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function save(clear: boolean) {
    setError("");
    setSaving(true);
    try {
      await request<void>(`/vehicles/${plate}/parking`, token, {
        method: "PUT", body: JSON.stringify({
          entry_event_id: entryEventId, zone: clear ? null : zone.trim(), bay: clear ? null : bay.trim() || null,
        }),
      });
      onSaved(clear ? "Parking confirmation cleared." : "Parking confirmed.");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return <Dialog title={`Confirm parking for ${plate}`} close={close}>
    <form className="record-form" onSubmit={event => { event.preventDefault(); void save(false); }}>
      <label>Parking zone<input value={zone} maxLength={80} required onChange={event => setZone(event.target.value)} /></label>
      <label>Bay number (optional)<input value={bay} maxLength={40} onChange={event => setBay(event.target.value)} /></label>
      {error && <div className="wide"><ErrorNote message={error} /></div>}
      <div className="wide record-actions">
        <button className="button primary" disabled={saving || !zone.trim()}>{saving ? "Saving…" : "Save parking"}</button>
        {parking && <button className="button secondary" disabled={saving} type="button"
          onClick={() => void save(true)}>Clear confirmation</button>}
      </div>
    </form>
  </Dialog>;
}

export function VisitHistory({ plate, token, revision }: { plate: string; token: string; revision: number }) {
  const [page, setPage] = useState(0);
  const size = 10;
  const visits = useResource<Session[]>(`/vehicles/${plate}/visits?limit=${size}&offset=${page * size}`, token, 10000, revision);
  return <section className="card table-card">
    <div className="section-heading"><h2>Visit history</h2></div>
    {visits.error && <ErrorNote message={visits.error} />}
    {visits.data?.length ? <div className="table-wrap"><table>
      <thead><tr><th>Entered</th><th>Left</th><th>Time on site</th><th>Status</th></tr></thead>
      <tbody>{visits.data.map(visit => <tr key={visit.id}>
        <td>{when(visit.entry_time)}</td><td>{when(visit.exit_time)}</td>
        <td>{duration(visit.duration_seconds)}</td><td><Badge value={visit.status} /></td>
      </tr>)}</tbody>
    </table></div> : visits.data && <Empty title="No visits recorded" />}
    {(page > 0 || (visits.data?.length ?? 0) >= size) &&
      <Pager page={page} size={size} length={visits.data?.length ?? 0} onPage={setPage} />}
  </section>;
}
