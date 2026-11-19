import { useEffect, useState } from "react";

export type Credential = { access_token: string; username: string; role: string };
export type EventRow = {
  event_uuid: string; registration_number: string | null; plate_number: string | null;
  direction: "IN" | "OUT" | "SIGHTING"; timestamp: string; camera_id: string; gate_id: string;
  verification_status: "AUTO" | "VERIFIED" | "PENDING"; vehicle_type: string | null;
  recognition_status: string | null; reason: string | null; track_id: number | null;
  ocr_confidence: number | null; plate_confidence: number | null; evidence: string[];
};
export type Observation = {
  frame_number: number; raw_text: string | null; normalized_text: string;
  plate_confidence: number; ocr_confidence: number | null; association: string;
  valid_format: boolean;
};
export type EventDetail = EventRow & { observations: Observation[] };
export type RegistryService = "VAHAN_RC" | "PARIVAHAN_PUC";
export type ProfileSource = { source: RegistryService | "SITE"; checked_at: string };
export type VehicleProfile = {
  vehicle_type: string | null; owner_or_department: string | null; manufacturer: string | null;
  model: string | null; color: string | null; fuel_type: string | null;
  registration_valid_until: string | null; insurance_valid_until: string | null;
  fitness_valid_until: string | null; puc_certificate_number: string | null;
  puc_valid_until: string | null; updated_at: string | null; sources: Record<string, ProfileSource>;
};
export type Parking = {
  zone: string; bay: string | null; confirmed_at: string; current: boolean;
};
export type VehicleRoute = {
  registration_number: string; visit_status: "INSIDE" | "EXITED" | "UNKNOWN";
  entry_event_id: string | null; entry_time: string | null; exit_time: string | null;
  duration_seconds: number | null; site_date: string; profile: VehicleProfile; parking: Parking | null;
  history: (EventRow & { camera_name: string; camera_area: string | null })[];
};
export type Session = {
  id: string; registration_number: string; vehicle_type: string | null; entry_event_id: string | null;
  entry_time: string | null; exit_time: string | null; entry_gate: string | null; entry_camera_id: string | null;
  duration_seconds: number | null; status: string;
};
export type Camera = {
  id: string; name: string; gate_id: string; direction: "AUTO" | "ENTRY" | "EXIT" | "TRACKING";
  area: string | null;
  entry_side: "positive" | "negative"; source_binding: string | null; width: number | null; height: number | null;
  tripwire: [number, number, number, number] | null; enabled: boolean;
  detection_threshold: number; status: string; last_seen: string | null;
};
export type MovementPoint = { label: string; entries: number; exits: number };
export type Summary = {
  date: string; timezone: string; entries: number; exits: number; inside: number; pending: number;
  unmatched_exits: number; active_cameras: number; offline_cameras: number;
  hourly: MovementPoint[]; daily: MovementPoint[]; categories: Record<string, number>;
};
export type LiveStatus = {
  number: number; tracks: number; crossings: number; timestamp: string | null;
  elapsed_seconds: number; published_at: string;
};
export type Alert = { id: string; event_uuid: string; registration_number: string; kind: string; timestamp: string };

export const api = "/api/v1";

class RequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

async function fetchApi(path: string, token: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", "Bearer " + token);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(api + path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && token) window.dispatchEvent(new Event("session-expired"));
    const detail = typeof body.detail === "string" ? body.detail
      : response.status === 422 ? "Check the values entered and try again."
      : `Request failed (${response.status}).`;
    throw new RequestError(detail, response.status);
  }
  return response;
}

export async function request<T>(path: string, token: string, options?: RequestInit): Promise<T> {
  const response = await fetchApi(path, token, options);
  return response.status === 204 ? undefined as T : response.json();
}

type Resource<T> = { data?: T; error?: string; errorStatus?: number };

export function useResource<T>(path: string | null, token: string, interval = 0, revision = 0): Resource<T> {
  const [result, setResult] = useState<Resource<T> & { key: string }>();
  const key = `${path}:${revision}`;
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let timer: number;
    async function load() {
      try {
        const data = await request<T>(path!, token, { signal: controller.signal });
        if (!controller.signal.aborted) setResult({ key, data });
      } catch (error) {
        if (!controller.signal.aborted) setResult({
          key, error: (error as Error).message, errorStatus: error instanceof RequestError ? error.status : undefined,
        });
      } finally {
        if (interval && !controller.signal.aborted) timer = window.setTimeout(load, interval);
      }
    }
    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [path, token, interval, key]);
  return result?.key === key ? result : {};
}

export function useProtectedImage(path: string | null, token: string, interval = 0) {
  const [image, setImage] = useState<{ path: string; url: string }>();
  useEffect(() => {
    if (!path) return;
    let current: string | undefined;
    let timer: number;
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetchApi(path!, token, { signal: controller.signal, cache: "no-store" });
        const bytes = await response.blob();
        if (controller.signal.aborted) return;
        if (current) URL.revokeObjectURL(current);
        current = URL.createObjectURL(bytes);
        setImage({ path: path!, url: current });
      } catch {
        if (!controller.signal.aborted) {
          if (current) URL.revokeObjectURL(current);
          current = undefined;
          setImage(undefined);
        }
      } finally {
        if (interval && !controller.signal.aborted) timer = window.setTimeout(load, interval);
      }
    }
    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      if (current) URL.revokeObjectURL(current);
    };
  }, [path, token, interval]);
  return image?.path === path ? image.url : undefined;
}

export const number = (input: number) => new Intl.NumberFormat().format(input);
export const readable = (input: string) => input.toLowerCase().replaceAll("_", " ");
export const when = (input: string | null) => input
  ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(input)) : "—";
export const percent = (input: number | null) => input === null ? "—" : `${Math.round(input * 100)}%`;
export const displayPlate = (row: EventRow) => row.registration_number || row.plate_number || "Unidentified";
export function duration(seconds: number | null) {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return "< 1 min";
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days) return `${days} d ${hours % 24} h ${minutes % 60} min`;
  return hours ? `${hours} h ${minutes % 60} min` : `${minutes} min`;
}
