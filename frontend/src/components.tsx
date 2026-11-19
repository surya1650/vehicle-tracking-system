import { ArrowDownLeft, ArrowUpRight, CameraOff, ChevronLeft, ChevronRight, ImageOff, X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import {
  displayPlate, percent, readable, useProtectedImage, when, type EventRow,
} from "./api";

export function Badge({ value }: { value: string }) {
  const kind = ["ACTIVE", "IN", "AUTO", "VERIFIED", "INSIDE", "MATCHED", "VALID"].includes(value) ? "positive"
    : ["PENDING", "RECONNECTING", "AMBIGUOUS", "EXPIRED", "NEEDS_RECONFIRMATION"].includes(value) ? "warning"
    : ["OUT", "EXITED"].includes(value) ? "info" : "neutral";
  const label = ({ IN: "Entry", OUT: "Exit", AUTO: "Automatic", PENDING: "Needs review" } as Record<string, string>)[value] || readable(value);
  return <span className={`badge ${kind}`}>{value === "IN" && <ArrowDownLeft size={13} />}{value === "OUT" && <ArrowUpRight size={13} />}{label}</span>;
}

export function Empty({ title, detail }: { title: string; detail?: string }) {
  return <div className="empty"><strong>{title}</strong>{detail && <p>{detail}</p>}</div>;
}

export function ErrorNote({ message }: { message: string }) {
  return <p role="alert" className="error-note">{message}</p>;
}

export function SecureImage({ path, token, label, live = false }: {
  path: string | null; token: string; label: string; live?: boolean;
}) {
  const image = useProtectedImage(path, token, live ? 850 : 0);
  return image ? <img src={image} alt={label} className={live ? "camera-image" : "evidence-image"} />
    : <div className={live ? "camera-placeholder" : "evidence-placeholder"}>
      {live ? <CameraOff /> : <ImageOff />}<span>{live ? "Waiting for a camera frame" : "Image unavailable"}</span>
    </div>;
}

export function Pager({ page, length, size, onPage }: {
  page: number; length: number; size: number; onPage: (page: number) => void;
}) {
  return <div className="pager"><span>{length ? `${page * size + 1}–${page * size + length}` : "0"} results on this page</span>
    <div><button className="icon-button" aria-label="Previous page" disabled={page === 0} onClick={() => onPage(page - 1)}><ChevronLeft size={17} /></button>
    <button className="icon-button" aria-label="Next page" disabled={length < size} onClick={() => onPage(page + 1)}><ChevronRight size={17} /></button></div>
  </div>;
}

export function EventTable({ rows, onEvent }: { rows: EventRow[]; onEvent: (id: string) => void }) {
  if (!rows.length) return <Empty title="No events found" />;
  return <div className="table-wrap"><table>
    <thead><tr><th>Registration</th><th>Movement</th><th>Gate</th><th>Recorded at</th><th>Verification</th><th>OCR confidence</th><th><span className="sr-only">Details</span></th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.event_uuid}>
      <td><span className="plate">{displayPlate(row)}</span>{row.vehicle_type && <div className="subtle capitalize">{row.vehicle_type}</div>}</td>
      <td><Badge value={row.direction} /></td><td>{row.gate_id}</td><td className="muted">{when(row.timestamp)}</td>
      <td><Badge value={row.verification_status} /></td><td>{percent(row.ocr_confidence)}</td>
      <td><button type="button" className="text-button" onClick={() => onEvent(row.event_uuid)}>Details <ArrowUpRight size={14} /></button></td>
    </tr>)}</tbody>
  </table></div>;
}

export function Dialog({ title, children, close }: { title: string; children: ReactNode; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return <dialog ref={ref} className="dialog" onCancel={event => { event.preventDefault(); close(); }}>
    <div className="dialog-header"><h2>{title}</h2><button className="icon-button" aria-label="Close details" onClick={close}><X size={18} /></button></div>
    {children}
  </dialog>;
}
