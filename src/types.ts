export type Role = "admin" | "manager" | "user";
export type DocStatus =
  "uploaded" | "ready" | "processing" | "failed" | "review" | "canceled";
export type Capability = "llm" | "embedding" | "rerank" | "ocr" | "vision";
export type RunStatus =
  "completed" | "running" | "failed" | "canceled" | "no_evidence";

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  color: string;
  icon: string;
  embedding: string;
  chunkSize: number;
  overlap: number;
  updatedAt: string;
  parsing?: ParsingConfig;
  parsingVersion?: number;
}
export type ChunkMethod = "auto" | "parent_child" | "semantic" | "general" | "heading" | "qa" | "table" | "book" | "paper" | "laws" | "page" | "fixed" | "delimiter" | "one";
export interface ParsingConfig {
  strategy: ChunkMethod;
  pdfParser: "auto" | "text" | "cloud";
  delimiter: string;
  imageContext: number;
  understandImages: boolean;
  tableHtml: boolean;
  autoMetadata?: boolean;
  metadataFallback?: boolean;
  metadataModel?: string;
}
export interface KnowledgeChunk {
  id: string;
  content: string;
  location: string;
  enabled: boolean;
  keywords: string[];
  questions: string[];
  aiKeywords?: string[];
  aiQuestions?: string[];
  manual?: boolean;
  chunkMethod?: ChunkMethod;
  sectionPath?: string[];
  sourceLocations?: string[];
  splitNote?: string;
  table?: {headers: string[]; rows: string[][]};
}
export interface RetrievalConfig {
  threshold: number;
  vectorWeight: number;
  rerankModel: string;
  fileIds: string[];
  metadataKey: string;
  metadataValue: string;
}
export interface GenerationConfig {
  temperature: number | null;
  topP: number | null;
  maxTokens: number | null;
  presencePenalty: number | null;
  frequencyPenalty: number | null;
}
export interface RegisteredModel {
  id: string;
  model: string;
  capability: Capability;
  enabled: boolean;
}
export interface MetadataFact {
  field: "time" | "version" | "subject" | "owner";
  value: string;
  quote: string;
  location: string;
  unitId: string;
  label?: string;
}
export interface MetadataExtraction {
  status: "processing" | "completed" | "failed" | "disabled" | "not_started";
  model: string;
  extractedAt?: string;
  version: number;
  facts: MetadataFact[];
  keywords: string[];
  questions: string[];
  coverage?: { totalUnits: number; processedUnits: number };
  warnings?: string[];
  error?: string;
  lastSuccess?: MetadataExtraction;
}
export interface KnowledgeDoc {
  detailsOmitted?: boolean;
  id: string;
  kbId: string;
  name: string;
  format: string;
  size: number;
  status: DocStatus;
  enabled: boolean;
  chunks: number;
  enabledChunks?: number;
  embeddingDimension?: number;
  updatedAt: string;
  category: string;
  content: string;
  location: string;
  provenance: string;
  progress: number;
  startedAt?: number;
  failNext?: boolean;
  error?: string;
  previousReady?: boolean;
  segments?: KnowledgeChunk[];
  parsing?: ParsingConfig | null;
  indexParsingVersion?: number;
  chunkSizeOverride?: number | null;
  overlapOverride?: number | null;
  metadata?: Record<string, string>;
  manualMetadata?: Record<string, string>;
  metadataExtraction?: MetadataExtraction;
}
export interface ModelBinding {
  capability: Capability;
  label: string;
  description: string;
  model: string;
  status: "ready" | "unconfigured" | "failed";
  lastTest?: string;
  lastError?: string | null;
}
export interface Assistant {
  id: string;
  name: string;
  description: string;
  kbIds: string[];
  enabled: boolean;
  model: string;
  greeting: string;
  prompt: string;
  topK: number;
  candidateK: number;
  useRerank: boolean;
  configVersion: number;
  retrievalVersion: number;
  color: string;
  retrieval?: RetrievalConfig;
  generation?: GenerationConfig;
  noEvidenceReply?: string;
  showQuote?: boolean;
  multiTurn?: boolean;
  keywordAnalysis?: boolean;
}
export interface Citation {
  id: string;
  docId: string;
  name: string;
  format: string;
  kbName: string;
  location: string;
  content: string;
  provenance: string;
  score: number;
  vectorScore?: number;
  textScore?: number;
  hybridScore?: number;
  rerankScore?: number;
}
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations: Citation[];
  status: "completed" | "generating" | "canceled" | "no_evidence" | "failed";
  runId?: string;
  startedAt?: number;
  fullText?: string;
  error?: string;
}
export interface Conversation {
  delivery?: import("./delivery-types").Delivery;
  inputValues?: Record<string, unknown>;
  id: string;
  assistantId: string;
  title: string;
  messages: ChatMessage[];
  updatedAt: string;
  configVersion: number;
  kbIds: string[];
  assistantSnapshot?: Assistant;
  platformAppId?: string;
  platformAppName?: string;
  purpose?: string;
  platformVersionId?: string;
  platformVersionNumber?: number;
}
export interface AgentTraceEvent {
  id: string; parentId: string; kind: "model" | "tool" | "skill" | "workflow"; name: string; status: string; startedAt: string; durationMs?: number;
  reference?: {appId: string; versionId: string; name: string; number: number};
  input?: {text: string; truncated: boolean}; output?: {text: string; truncated: boolean}; context?: {text: string; truncated: boolean}; usage?: { model?: string; kind?: string; inputTokens?: number | null; outputTokens?: number | null; tokens?: number | null; cachedTokens?: number | null; requestId?: string | null }[]; error?: string; errorCode?: string | null;
}
export interface RunLog {
  id: string;
  knowledgeBaseId?: string;
  kind: "chat" | "ingestion" | "model";
  query: string;
  assistantName: string;
  model: string;
  status: RunStatus;
  inputTokens: number | null;
  outputTokens: number | null;
  tokens: number | null;
  duration: number;
  createdAt: string;
  stages: { name: string; ms: number; status: "ok" | "failed" | "skipped" | "running" | "canceled" | "interrupted"; attempt?: number; startedAt?: string }[];
  attempt?: number;
  executionType?: string;
  feedback?: { value: "positive" | "negative"; updatedAt: string };
  purpose?: string;
  applicationId?: string;
  applicationName?: string;
  applicationVersionNumber?: number;
  traceEvents?: AgentTraceEvent[];
  documentId?: string;
  conversationId?: string;
  messageId?: string;
  output?: string;
  previousIndexAvailable?: boolean;
  indexPublished?: boolean;
  usage?: { model?: string; kind?: string; inputTokens?: number | null; outputTokens?: number | null; tokens?: number | null; cachedTokens?: number | null; requestId?: string | null }[];
  citations: Citation[];
  error?: string;
  errorCode?: string | null;
  configVersion: number;
}
export interface AppState {
  version: number;
  role: Role;
  knowledgeBases: KnowledgeBase[];
  documents: KnowledgeDoc[];
  models: ModelBinding[];
  assistants: Assistant[];
  conversations: Conversation[];
  logs: RunLog[];
  catalog?: RegisteredModel[];
  provider?: {
    name: string;
    region: string;
    endpoint: string;
    secretSaved?: boolean;
  };
}
