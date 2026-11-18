import { ArrowDownLeft, ArrowUpRight, Camera, CameraOff, CircleAlert, ClipboardCheck, ParkingSquare, RefreshCw } from "lucide-react";
import { useState } from "react";
import { number, useResource, type Alert, type EventRow, type MovementPoint, type Summary } from "./api";
import { Empty, ErrorNote, EventTable } from "./components";

function MovementChart({ points, daily }: { points: MovementPoint[]; daily: boolean }) {
  const max = Math.max(1, ...points.flatMap(point => [point.entries, point.exits]));
  return <div className="chart" role="img" aria-label={daily ? "Daily entries and exits" : "Hourly entries and exits"}>
    <div className="chart-scale"><span>{max}</span><span>{max / 2}</span><span>0</span></div>
    <div className={`chart-columns ${daily ? "daily" : ""}`}>
      {points.map((item, index) => <div className="chart-column" key={item.label}
        title={`${item.label}: ${item.entries} entries, ${item.exits} exits`}>
        <div className="bar-pair"><span className="bar entry" style={{ height: `${item.entries / max * 100}%` }} />
          <span className="bar exit" style={{ height: `${item.exits / max * 100}%` }} /></div>
        <div className="chart-label">{daily ? item.label.slice(5) : index % 4 === 0 ? item.label : "\u00a0"}</div>
      </div>)}
    </div>
  </div>;
}

const stats = [
  ["entries", "Entries today", ArrowDownLeft, "green"],
  ["exits", "Exits today", ArrowUpRight, "blue"],
  ["inside", "Vehicles inside", ParkingSquare, "blue"],
  ["pending", "Awaiting review", ClipboardCheck, "gold"],
  ["unmatched_exits", "Unmatched exits", CircleAlert, "gold"],
  ["active_cameras", "Active cameras", Camera, "green"],
  ["offline_cameras", "Offline cameras", CameraOff, "gray"],
] as const;

export function Overview({ token, revision, refresh, selectEvent }: {
  token: string; revision: number; refresh: () => void; selectEvent: (id: string) => void;
}) {
  const [range, setRange] = useState<"hourly" | "daily">("hourly");
  const { data, error } = useResource<Summary>("/dashboard/summary", token, 15000, revision);
  const events = useResource<EventRow[]>("/events?limit=6", token, 15000, revision);
  const alerts = useResource<Alert[]>("/alerts?limit=3", token, 15000, revision);
  const sum = Object.values(data?.categories || {}).reduce((a, b) => a + b, 0);
  return <>
    <div className="page-heading"><div><h1>Overview</h1><p>Vehicle movements and camera activity across your gates.</p></div>
      <button className="button secondary" onClick={refresh}><RefreshCw size={15} /> Refresh</button></div>
    {error && <ErrorNote message={error} />}
    <div className="stat-grid">{stats.map(([field, label, Icon, tone]) => <div className="card stat" key={field}>
      <span className={`stat-icon ${tone}`}><Icon size={18} /></span><span className="stat-label">{label}</span>
      <strong>{data ? number(data[field]) : "—"}</strong></div>)}</div>
    <div className="overview-grid">
      <section className="card chart-card">
        <div className="section-heading"><div><h2>Movement volume</h2><p>{data ? `${data.date} · ${data.timezone}` : "Gate activity by time"}</p></div>
          <div className="segmented"><button className={range === "hourly" ? "selected" : ""} onClick={() => setRange("hourly")}>Today</button>
          <button className={range === "daily" ? "selected" : ""} onClick={() => setRange("daily")}>7 days</button></div></div>
        {data ? <MovementChart points={data[range]} daily={range === "daily"} /> : <Empty title="Loading movement data" />}
        <div className="legend"><span><i className="entry" />Entries</span><span><i className="exit" />Exits</span></div>
      </section>
      <section className="card chart-card">
        <div className="section-heading"><div><h2>Vehicle categories</h2><p>Today's verified entries</p></div></div>
        {sum ? <div className="mix">{Object.entries(data!.categories).sort((a, b) => b[1] - a[1]).map(([kind, count]) =>
          <div key={kind}><div><span className="capitalize">{kind}</span><strong>{count}</strong></div>
            <div className="mix-track"><i style={{ width: `${count / sum * 100}%` }} /></div></div>)}</div>
          : <Empty title="No verified entries today" />}
      </section>
    </div>
    <section className="card table-card"><div className="section-heading"><div><h2>Recent activity</h2><p>Latest crossing events in recorded order</p></div></div>
      {events.error && <ErrorNote message={events.error} />}{events.data ? <EventTable rows={events.data} onEvent={selectEvent} /> : <Empty title="Loading activity" />}
    </section>
    {!!alerts.data?.length && <section className="card table-card"><div className="section-heading"><h2>Movement alerts</h2></div>
      <ul className="alert-list">{alerts.data.map(alert => <li key={alert.id}><CircleAlert size={17} />
        <button className="text-button" onClick={() => selectEvent(alert.event_uuid)}>{alert.registration_number}</button>
        <span>{alert.kind.replaceAll("_", " ").toLowerCase()}</span></li>)}</ul>
    </section>}
  </>;
}
