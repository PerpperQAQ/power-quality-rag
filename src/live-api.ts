import type { AppState, KnowledgeDoc, Role } from "./types";

export interface StateEnvelope {
  state: AppState;
  revision: number;
  instance?: string;
}
export function stateEnvelopeStatus(before: StateEnvelope, incoming: StateEnvelope): "replace" | "stale" | "accept" {
  if (before.instance && incoming.instance && before.instance !== incoming.instance) return "replace";
  return incoming.revision < before.revision ? "stale" : "accept";
}
let activeInstance: string | undefined;
export function setStateInstance(instance?: string) { activeInstance = instance; }
export interface UnchangedState { unchanged: true; revision: number; role: Role }
export function mergeDocumentDetail(summary: KnowledgeDoc, detail?: KnowledgeDoc): KnowledgeDoc {
  if (!summary.detailsOmitted || !detail || detail.updatedAt !== summary.updatedAt) return summary;
  return { ...summary, content: detail.content, segments: detail.segments,
    metadataExtraction: detail.metadataExtraction, detailsOmitted: false };
}
export interface EntityChange {
  upsert: Record<string, unknown>[];
  delete: string[];
}
export type StateChanges = Record<string, EntityChange>;
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
const sessionKey = "knowledge-hub-live-session-v1";
export function sessionId() {
  let id = sessionStorage.getItem(sessionKey);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(sessionKey, id);
  }
  return id;
}
export function requestHeaders(role: Role) {
  return { "X-Role": role, "X-Session-ID": sessionId(), "X-State-View": "compact", ...(activeInstance ? { "X-State-Instance": activeInstance } : {}) };
}
export async function api<T>(
  path: string,
  role: Role,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: {
        ...requestHeaders(role),
        ...(options.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...options.headers,
      },
    });
  } catch {
    throw new ApiError("无法连接本机后端，请检查服务是否已启动。", 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    if (response.status === 401 && payload?.error?.code === "AUTH_REQUIRED") window.location.reload();
    throw new ApiError(
      payload?.error?.message || `后端请求失败（${response.status}）`,
      response.status,
      payload?.error?.code,
    );
  }
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json"))
    throw new ApiError(
      "后端没有返回有效的 JSON 响应，请检查 API 服务地址。",
      response.status,
    );
  return response.json() as Promise<T>;
}
const entities = [
  "knowledgeBases",
  "documents",
  "assistants",
  "conversations",
  "models",
  "catalog",
] as const;
export function diffState(before: AppState, after: AppState): StateChanges {
  const changes: StateChanges = {};
  for (const key of entities) {
    const oldItems = (before[key] || []) as unknown as Record<
      string,
      unknown
    >[];
    const newItems = (after[key] || []) as unknown as Record<string, unknown>[];
    const identity = key === "models" ? "capability" : "id";
    const oldMap = new Map(
      oldItems.map((item) => [String(item[identity]), item]),
    );
    const newIds = new Set(newItems.map((item) => String(item[identity])));
    const upsert = newItems.filter(
      (item) =>
        JSON.stringify(oldMap.get(String(item[identity]))) !==
        JSON.stringify(item),
    ).map((item) => {
      if (key !== "documents" || !item.detailsOmitted) return item;
      // Empty summary placeholders are not edits to the original or chunks.
      const { segments: _segments, content: _content, metadataExtraction: _extraction,
        detailsOmitted: _omitted, enabledChunks: _enabled, ...writable } = item;
      return writable;
    });
    const deleted = oldItems
      .filter((item) => !newIds.has(String(item[identity])))
      .map((item) => String(item[identity]));
    if (upsert.length || deleted.length)
      changes[key] = { upsert, delete: deleted };
  }
  if (
    JSON.stringify(before.provider) !== JSON.stringify(after.provider) &&
    after.provider
  ) {
    const { name, region, endpoint } = after.provider;
    changes.provider = { upsert: [{ name, region, endpoint }], delete: [] };
  }
  return changes;
}
export function applyChanges(state: AppState, changes: StateChanges): AppState {
  const result = { ...state };
  for (const key of entities) {
    const change = changes[key];
    if (!change) continue;
    const identity = key === "models" ? "capability" : "id";
    const replacements = new Map(
      change.upsert.map((item) => [String(item[identity]), item]),
    );
    const deleted = new Set(change.delete);
    const existing = (
      (result[key] || []) as unknown as Record<string, unknown>[]
    )
      .filter((item) => !deleted.has(String(item[identity])))
      .map((item) => {
        const id = String(item[identity]);
        const replacement = replacements.get(id);
        replacements.delete(id);
        return replacement || item;
      });
    Object.assign(result, { [key]: [...replacements.values(), ...existing] });
  }
  if (changes.provider?.upsert[0])
    result.provider = {
      ...state.provider,
      ...changes.provider.upsert[0],
    } as AppState["provider"];
  return result;
}
