import type { Health, Run, RunBrief, RowsPage, Sample, Schema, Source, WriteMode } from "./types";

const KEY_STORAGE = "loopsql.accessKey";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function accessKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setAccessKey(key: string): void {
  try {
    sessionStorage.setItem(KEY_STORAGE, key);
  } catch {
    /* storage unavailable: key lives for this page only */
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const key = accessKey();
  if (key) headers.set("Authorization", `Bearer ${key}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/api/health"),
  samples: () => request<Sample[]>("/api/samples"),
  sources: () => request<Source[]>("/api/sources"),
  connect: (type: "sample" | "path" | "url", value: string) => request<Source>("/api/sources", json({ type, value })),
  upload: (file: File | Blob, filename: string) => {
    const fd = new FormData();
    fd.append("file", file, filename);
    return request<Source>("/api/sources/upload", { method: "POST", body: fd });
  },
  schema: (id: string) => request<Schema>(`/api/sources/${id}/schema`),
  removeSource: (id: string) => request<{ status: string }>(`/api/sources/${id}`, { method: "DELETE" }),
  startRun: (body: { request: string; source_id: string | null; require_approval: boolean; write_mode: WriteMode }) =>
    request<Run>("/api/runs", json(body)),
  run: (id: string, since = 0) => request<Run>(`/api/runs/${id}?since=${since}`),
  runs: () => request<RunBrief[]>("/api/runs"),
  respond: (id: string, body: Record<string, unknown>) => request<{ status: string }>(`/api/runs/${id}/respond`, json(body)),
  cancel: (id: string) => request<{ status: string }>(`/api/runs/${id}/cancel`, { method: "POST" }),
  rows: (id: string, page: number, pageSize: number, search: string) =>
    request<RowsPage>(`/api/runs/${id}/rows?page=${page}&page_size=${pageSize}&search=${encodeURIComponent(search)}`),
  exportNow: (id: string, fmt: "xlsx" | "csv") =>
    request<{ id: string; url: string; filename: string }>(`/api/runs/${id}/export?fmt=${fmt}`, { method: "POST" }),
};

/** Fetch a file with the auth header and return it as a Blob. */
export async function fetchBlob(url: string): Promise<Blob> {
  const headers = new Headers();
  const key = accessKey();
  if (key) headers.set("Authorization", `Bearer ${key}`);
  const res = await fetch(url, { headers });
  if (!res.ok) throw new ApiError(res.status, `Download failed (HTTP ${res.status})`);
  return res.blob();
}

export async function download(url: string, filename: string): Promise<void> {
  const blob = await fetchBlob(url);
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
