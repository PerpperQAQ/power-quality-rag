import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode, SetStateAction } from "react";
import { App as AntApp } from "antd";
import { StoreContext } from "./store-context";
import { useAuth } from "./AuthGate";
import type { DataMode, ImportFile, RetrievalRequest } from "./store-context";
import type { AppState, Capability, Citation, KnowledgeDoc, Role } from "./types";
import {
  api,
  ApiError,
  applyChanges,
  diffState,
  mergeDocumentDetail,
  requestHeaders,
  stateEnvelopeStatus,
  setStateInstance,
} from "./live-api";
import type { StateChanges, StateEnvelope, UnchangedState } from "./live-api";

const emptyState = (role: Role): AppState => ({
  version: 1,
  role,
  knowledgeBases: [],
  documents: [],
  models: [],
  catalog: [],
  assistants: [],
  conversations: [],
  logs: [],
});
interface Pending {
  changes: StateChanges;
  role: Role;
}
export function LiveStoreProvider({
  children,
  setMode,
}: {
  children: ReactNode;
  setMode: (mode: DataMode) => void;
}) {
  const auth=useAuth();
  const { message } = AntApp.useApp();
  const [state, setState] = useState<AppState>(() => {
    const saved = localStorage.getItem("knowledge-hub-live-role");
    return emptyState(
      saved === "admin" || saved === "manager" || saved === "user"
        ? saved
        : "admin",
    );
  });
  const [loading, setLoading] = useState(true);
  const [connected, setConnected] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const stateRef = useRef(state);
  const server = useRef<StateEnvelope>({ state, revision: 0 });
  const queue = useRef<Pending[]>([]);
  const processing = useRef(false);
  const mounted = useRef(true);
  const ready = useRef(false);
  const requestBusy = useRef(false);
  const generation = useRef(0);
  const waiters = useRef<
    { resolve: () => void; reject: (error: Error) => void }[]
  >([]);
  const lastError = useRef<Error | null>(null);
  const lastSaveError = useRef<Error | null>(null);
  const acceptedRole = useRef<Role | null>(null);
  const documentDetails = useRef(new Map<string, KnowledgeDoc>());
  const detailRequests = useRef(new Map<string, Promise<void>>());
  const publish = useCallback(() => {
    if (!mounted.current) return;
    const rebased = queue.current.reduce(
      (value, pending) => applyChanges(value, pending.changes),
      server.current.state,
    );
    const next = { ...rebased, role: stateRef.current.role };
    stateRef.current = next;
    setState(next);
  }, []);
  const accept = useCallback(
    (envelope: StateEnvelope) => {
      if (!envelope?.state || !Number.isInteger(envelope.revision))
        throw new Error("后端状态响应格式不正确");
      if (envelope.state.role !== stateRef.current.role) {
        if (!auth.enabled) return;
        localStorage.setItem("knowledge-hub-live-role",envelope.state.role);
        stateRef.current={...stateRef.current,role:envelope.state.role};
      }
      const transition = stateEnvelopeStatus(server.current, envelope);
      if (ready.current && transition === "stale") return;
      if (ready.current && transition === "replace") {
        generation.current++;
        documentDetails.current.clear();
        detailRequests.current.clear();
        if (queue.current.length) {
          queue.current = [];
          const error = new Error("资料已恢复，未保存的修改已撤回，请核对后重新操作。");
          lastSaveError.current = error;
          lastError.current = error;
          setConnectionError(error.message);
        }
        message.info("本机资料已切换到恢复副本，页面数据已更新。");
      }
      setStateInstance(envelope.instance);
      const ids = new Set(envelope.state.documents.map((doc) => doc.id));
      for (const id of documentDetails.current.keys()) if (!ids.has(id)) documentDetails.current.delete(id);
      const documents = envelope.state.documents.map((doc) => {
        if (!doc.detailsOmitted) documentDetails.current.set(doc.id, doc);
        return mergeDocumentDetail(doc, documentDetails.current.get(doc.id));
      });
      server.current = { ...envelope, state: { ...envelope.state, documents } };
      acceptedRole.current = envelope.state.role;
      ready.current = true;
      setConnected(true);
      publish();
    },
    [auth.enabled, message, publish],
  );
  const reconnect = useCallback(
    (manual = true) => {
      if (manual) {
        lastError.current = null;
        lastSaveError.current = null;
      }
      if (processing.current || requestBusy.current) return;
      requestBusy.current = true;
      const requestGeneration = generation.current;
      const role = stateRef.current.role;
      const path = ready.current && acceptedRole.current === role
        ? `/state?sinceRevision=${server.current.revision}${server.current.instance ? `&instance=${encodeURIComponent(server.current.instance)}` : ""}` : "/state";
      api<StateEnvelope | UnchangedState>(path, role)
        .then((response) => {
          if (
            !mounted.current ||
            requestGeneration !== generation.current ||
            processing.current
          )
            return;
          if ("unchanged" in response) {
            if (response.role !== stateRef.current.role) return;
            setConnected(true);
          } else accept(response);
          if (!lastError.current) setConnectionError("");
        })
        .catch((error: Error) => {
          if (mounted.current) setConnectionError(error.message);
        })
        .finally(() => {
          requestBusy.current = false;
          if (mounted.current) setLoading(false);
        });
    },
    [accept],
  );
  useEffect(() => {
    mounted.current = true;
    reconnect();
    const timer = window.setInterval(() => reconnect(false), 1400);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
    };
  }, [reconnect]);
  const drain = useCallback(async () => {
    if (processing.current) return;
    processing.current = true;
    generation.current++;
    lastError.current = null;
    lastSaveError.current = null;
    setSyncing(true);
    while (queue.current.length && mounted.current) {
      const pending = queue.current[0];
      let error: Error | null = null;
      for (let attempts = 0; attempts < 3; attempts++) {
        try {
          const result = await api<StateEnvelope>("/state", pending.role, {
            method: "PATCH",
            body: JSON.stringify({
              baseRevision: server.current.revision,
              changes: pending.changes,
            }),
          });
          queue.current.shift();
          accept(result);
          error = null;
          break;
        } catch (caught) {
          error = caught instanceof Error ? caught : new Error("保存失败");
          if (caught instanceof ApiError && caught.status === 409 && caught.code !== "STATE_REPLACED") {
            try {
              const fresh = await api<StateEnvelope>("/state", pending.role);
              const replaced = stateEnvelopeStatus(server.current, fresh) === "replace";
              accept(fresh);
              if (replaced) { error = new Error("资料已恢复，请核对后重新操作。"); break; }
            } catch {
              break;
            }
          } else break;
        }
      }
      if (error) {
        queue.current.shift();
        lastError.current = error;
        lastSaveError.current = error;
        setConnectionError(
          `变更保存失败：${error.message}。本次变更已撤回，请检查后重新操作。`,
        );
        try {
          accept(await api<StateEnvelope>(
            "/state",
            stateRef.current.role,
          ));
        } catch {
          /* retain last confirmed server state */
        }
        publish();
      }
    }
    processing.current = false;
    setSyncing(false);
    for (const waiter of waiters.current.splice(0))
      lastSaveError.current
        ? waiter.reject(lastSaveError.current)
        : waiter.resolve();
  }, [accept, publish]);
  const update = useCallback(
    (action: SetStateAction<AppState>) => {
      if (!ready.current) {
        setConnectionError("后端尚未连接，暂时不能保存变更。");
        return;
      }
      const before = stateRef.current;
      const after = typeof action === "function" ? action(before) : action;
      if (after.role !== before.role) {
        if (processing.current || queue.current.length) {
          setConnectionError("正在保存变更，请等待完成后再切换角色。");
          return;
        }
        generation.current++;
        // Role is a local demo selector, while log visibility is enforced server-side.
        // Drop the previous role's view before any request can publish another snapshot.
        server.current = {
          ...server.current,
          state: {
            ...server.current.state,
            role: after.role,
            logs: [],
            assistants:
              after.role === "user"
                ? server.current.state.assistants.filter((item) => item.enabled)
                : server.current.state.assistants,
          },
        };
        stateRef.current = { ...before, role: after.role };
        localStorage.setItem("knowledge-hub-live-role", after.role);
      }
      const changes = diffState(before, after);
      if (Object.keys(changes).length)
        queue.current.push({ changes, role: after.role });
      publish();
      if (queue.current.length) void drain();
    },
    [drain, publish],
  );
  const flush = useCallback(() => {
    if (!ready.current) return Promise.reject(new Error("请先连接后端服务。"));
    if (!processing.current && !queue.current.length)
      return lastSaveError.current
        ? Promise.reject(lastSaveError.current)
        : Promise.resolve();
    return new Promise<void>((resolve, reject) =>
      waiters.current.push({ resolve, reject }),
    );
  }, []);
  const mutate = useCallback(
    async (path: string, options: RequestInit = {}) => {
      await flush();
      generation.current++;
      const response = await api<StateEnvelope>(path, stateRef.current.role, {
        method: "POST",
        ...options,
      });
      accept(response);
      lastError.current = null;
      setConnectionError("");
      return response;
    },
    [accept, flush],
  );
  const guarded = useCallback(
    async (action: () => Promise<unknown>, notify = true) => {
      try {
        await action();
      } catch (error) {
        const failure = error instanceof Error ? error : new Error("请求失败");
        lastError.current = failure;
        setConnectionError(failure.message);
        if (notify)
          message.error({ key: "live-action-error", content: failure.message });
        throw failure;
      }
    },
    [message],
  );
  const importFiles = useCallback(
    async (
      kbId: string,
      files: ImportFile[],
      _fail: boolean,
      autoBuild = true,
      fileStrategies: Record<string,string> = {},
    ) => {
      if (files.some((file) => !(file instanceof File)))
        throw new Error("真实模式需要选择本地文件，不能导入示例文件。");
      await flush();
      const body = new FormData();
      for (const file of files) body.append("files", file as File);
      body.append("autoBuild", String(autoBuild));
      body.append("fileStrategies", JSON.stringify(fileStrategies));
      generation.current++;
      const response = await api<
        StateEnvelope & { added: number; skipped: number }
      >(
        `/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
        stateRef.current.role,
        { method: "POST", body },
      );
      accept(response);
      lastError.current = null;
      setConnectionError("");
      return { added: response.added, skipped: response.skipped };
    },
    [accept, flush],
  );
  const retrieveKnowledge = useCallback(
    async (request: RetrievalRequest) => {
      await flush();
      const response = await api<{ citations: Citation[] }>(
        "/retrieval",
        stateRef.current.role,
        { method: "POST", body: JSON.stringify(request) },
      );
      lastError.current = null;
      setConnectionError("");
      return response.citations;
    },
    [flush],
  );
  const saveSecret = useCallback(
    async (apiKey: string) => {
      await flush();
      await api("/provider/secret", stateRef.current.role, {
        method: "POST",
        body: JSON.stringify({ apiKey }),
      });
      accept(await api<StateEnvelope>("/state", stateRef.current.role));
      lastError.current = null;
      setConnectionError("");
    },
    [accept, flush],
  );
  const downloadDocument = useCallback(async (id: string, name: string) => {
    const response = await fetch(
      `/api/documents/${encodeURIComponent(id)}/file`,
      { headers: requestHeaders(stateRef.current.role) },
    );
    if (!response.ok) throw new Error("下载原文件失败，请检查文件是否仍存在。");
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }, []);
  const loadDocument = useCallback((id: string): Promise<void> => {
    const existing = detailRequests.current.get(id);
    if (existing) return existing;
    const request: Promise<void> = (async () => {
      const requestedInstance = server.current.instance;
      for (let attempt = 0; attempt < 3; attempt++) {
        const result = await api<{ document: KnowledgeDoc; revision: number; instance?: string }>(
          `/documents/${encodeURIComponent(id)}`, stateRef.current.role);
        if (!mounted.current) return;
        if (requestedInstance !== server.current.instance || result.instance && requestedInstance !== result.instance) {
          accept(await api<StateEnvelope>("/state", stateRef.current.role));
          throw new Error("资料已恢复，请重新加载文档详情。");
        }
        const current = server.current.state.documents.find((doc) => doc.id === id);
        if (!current) return;
        if (current.updatedAt !== result.document.updatedAt) {
          // A poll or background publication changed the document while its
          // detail was in flight. Refresh the summary before trying again.
          accept(await api<StateEnvelope>("/state", stateRef.current.role));
          continue;
        }
        documentDetails.current.set(id, result.document);
        server.current = { ...server.current, state: { ...server.current.state,
          documents: server.current.state.documents.map((doc) =>
            doc.id === id ? mergeDocumentDetail(doc, result.document) : doc) } };
        publish();
        return;
      }
      throw new Error("文档刚刚更新，请重新加载详情。");
    })().finally(() => { if (detailRequests.current.get(id) === request) detailRequests.current.delete(id); });
    detailRequests.current.set(id, request);
    return request;
  }, [accept, publish]);
  return (
    <StoreContext.Provider
      value={{
        state,
        update,
        mode: "live",
        setMode,
        loading,
        connected,
        syncing,
        connectionError,
        reconnect,
        storageError: false,
        flush,
        importFiles,
        retrieveKnowledge,
        saveSecret,
        downloadDocument,
        loadDocument,
        extractMetadata: (id, preserveManual = true) =>
          guarded(
            () =>
              mutate(`/documents/${encodeURIComponent(id)}/metadata/extract`, {
                body: JSON.stringify({ preserveManual }),
              }),
            false,
          ),
        retryDoc: (id) =>
          guarded(() =>
            mutate(`/documents/${encodeURIComponent(id)}/build`),
          ).catch(() => {}),
        cancelDoc: (id) =>
          guarded(() =>
            mutate(`/documents/${encodeURIComponent(id)}/cancel`),
          ).catch(() => {}),
        sendQuestion: (id, query, regenerate = false, inputs) =>
          guarded(
            () =>
              mutate(`/conversations/${encodeURIComponent(id)}/messages`, {
                body: JSON.stringify({ query, regenerate, inputs }),
              }),
            false,
          ),
        stopAnswer: (id) =>
          guarded(() =>
            mutate(`/conversations/${encodeURIComponent(id)}/stop`),
          ).catch(() => {}),
        testModel: (capability: Capability) =>
          guarded(() => mutate(`/models/${capability}/test`), false),
        reset: () => {},
      }}
    >
      {children}
    </StoreContext.Provider>
  );
}
