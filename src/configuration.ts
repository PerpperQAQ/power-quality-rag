import type {
  AppState,
  GenerationConfig,
  KnowledgeChunk,
  KnowledgeDoc,
  ParsingConfig,
  RetrievalConfig,
} from "./types";
import { mergeBuiltinCatalog } from "./model-catalog";
import { CHUNKING_LABELS } from "./chunking-methods";

export const DEFAULT_PARSING: ParsingConfig = {
  strategy: "general",
  pdfParser: "auto",
  delimiter: "\\n",
  imageContext: 1,
  understandImages: true,
  tableHtml: true,
  autoMetadata: true,
  metadataModel: "",
};
export const DEFAULT_RETRIEVAL: RetrievalConfig = {
  threshold: 0.2,
  vectorWeight: 0.3,
  rerankModel: "",
  fileIds: [],
  metadataKey: "",
  metadataValue: "",
};
export const DEFAULT_GENERATION: GenerationConfig = {
  temperature: null,
  topP: null,
  maxTokens: null,
  presencePenalty: null,
  frequencyPenalty: null,
};
export const STRATEGY_LABELS = CHUNKING_LABELS;

export function makeChunks(
  doc: Pick<KnowledgeDoc, "id" | "content" | "location">,
  size = 600,
  delimiter = "\\n",
  overlap = 0,
): KnowledgeChunk[] {
  const separator = delimiter.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
  const parts = separator
    ? doc.content.split(separator).filter(Boolean)
    : [doc.content];
  const texts: string[] = [];
  let current = "";
  for (const part of parts) {
    if (current && current.length + part.length + 1 > size) {
      texts.push(current);
      current = "";
    }
    current += (current ? "\n" : "") + part;
    while (current.length > size) {
      texts.push(current.slice(0, size));
      current = current.slice(Math.max(1, size - Math.min(overlap, size - 1)));
    }
  }
  if (current) texts.push(current);
  return texts.map((content, i) => ({
    id: `${doc.id}:chunk-${i + 1}`,
    content,
    location: doc.location,
    enabled: true,
    keywords: [],
    questions: [],
  }));
}

export function enrichState(state: AppState): AppState {
  return {
    ...state,
    provider: state.provider || {
      name: "通义 · 中国内地",
      region: "北京",
      endpoint: "https://dashscope.aliyuncs.com",
    },
    catalog: mergeBuiltinCatalog(state.catalog || [
      ...state.models.map((m) => ({
        id: `${m.capability}:${m.model}`,
        model: m.model,
        capability: m.capability,
        enabled: true,
      })),
      {
        id: "llm:qwen-plus",
        model: "qwen-plus",
        capability: "llm",
        enabled: true,
      },
      {
        id: "embedding:text-embedding-v3",
        model: "text-embedding-v3",
        capability: "embedding",
        enabled: true,
      },
    ]),
    knowledgeBases: state.knowledgeBases.map((k) => ({
      ...k,
      parsing: {
        ...DEFAULT_PARSING,
        ...k.parsing,
        autoMetadata: k.parsing?.autoMetadata ?? false,
      },
      parsingVersion: k.parsingVersion || 1,
    })),
    documents: state.documents.map((d) => {
      const segments =
        d.segments ||
        (d.status === "ready" || d.previousReady ? makeChunks(d) : []);
      return {
        ...d,
        segments,
        chunks: segments.length,
        metadata: d.metadata || {},
        manualMetadata: d.manualMetadata || { ...(d.metadata || {}) },
        parsing: d.parsing
          ? {
              ...DEFAULT_PARSING,
              ...d.parsing,
              autoMetadata: d.parsing.autoMetadata ?? false,
            }
          : d.parsing,
        indexParsingVersion: d.indexParsingVersion || 1,
      };
    }),
    assistants: state.assistants.map((a) => ({
      ...a,
      retrieval: a.retrieval || { ...DEFAULT_RETRIEVAL },
      generation: a.generation || { ...DEFAULT_GENERATION },
      showQuote: a.showQuote ?? true,
      multiTurn: a.multiTurn ?? true,
      noEvidenceReply:
        a.noEvidenceReply ||
        "未在所选知识库中找到足够依据，请调整问题或补充资料。",
    })),
    logs: state.logs.map((l) => ({
      ...l,
      knowledgeBaseId:
        l.knowledgeBaseId ||
        (l.kind === "ingestion"
          ? state.knowledgeBases.find((k) => k.name === l.assistantName)?.id
          : undefined),
    })),
  };
}
