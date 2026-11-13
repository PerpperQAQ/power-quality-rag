import { createContext, useContext } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { AppState, Capability, Citation, RetrievalConfig } from "./types";

export type DataMode = "demo" | "live";
export interface RetrievalRequest {
  query: string;
  kbIds: string[];
  topK: number;
  candidateK: number;
  useRerank: boolean;
  retrieval: RetrievalConfig;
}
export type ImportFile = { name: string; size: number } | File;

export interface Store {
  state: AppState;
  update: Dispatch<SetStateAction<AppState>>;
  storageError: boolean;
  mode: DataMode;
  setMode: (mode: DataMode) => void;
  loading: boolean;
  connected: boolean;
  syncing: boolean;
  connectionError: string;
  reconnect: () => void;
  flush: () => Promise<void>;
  retrieveKnowledge: (request: RetrievalRequest) => Promise<Citation[]>;
  saveSecret: (apiKey: string) => Promise<void>;
  testModel: (capability: Capability) => Promise<void>;
  downloadDocument: (id: string, name: string) => Promise<void>;
  loadDocument?: (id: string) => Promise<void>;
  extractMetadata: (id: string, preserveManual?: boolean) => Promise<void>;
  importFiles: (
    kbId: string,
    files: ImportFile[],
    fail: boolean,
    autoBuild?: boolean,
    fileStrategies?: Record<string,string>,
  ) =>
    | { added: number; skipped: number }
    | Promise<{ added: number; skipped: number }>;
  retryDoc: (id: string) => void | Promise<void>;
  cancelDoc: (id: string) => void | Promise<void>;
  sendQuestion: (
    conversationId: string,
    query: string,
    regenerate?: boolean,
    inputs?: Record<string, unknown>,
  ) => void | Promise<void>;
  stopAnswer: (conversationId: string) => void | Promise<void>;
  reset: () => void;
}

export const StoreContext = createContext<Store | null>(null);
export function useStore() {
  const store = useContext(StoreContext);
  if (!store) throw new Error("StoreProvider missing");
  return store;
}
