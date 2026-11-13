import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { AppState, KnowledgeDoc, RunLog } from "./types";
import {
  inferFixture,
  makeAnswer,
  now,
  retrieve,
  seedState,
  STORAGE_KEY,
  uid,
} from "./demo";

import { StoreContext } from "./store-context";
import type { DataMode } from "./store-context";
import { LiveStoreProvider } from "./LiveStore";
import { demoApplyManualMetadata, simulatedMetadata } from "./metadata";
import {
  DEFAULT_PARSING,
  DEFAULT_RETRIEVAL,
  enrichState,
  makeChunks,
} from "./configuration";

function loadState(): AppState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return seedState();
    const saved = JSON.parse(raw) as AppState;
    if (
      saved.version !== 1 ||
      !["admin", "manager", "user"].includes(saved.role) ||
      ![
        "knowledgeBases",
        "documents",
        "assistants",
        "models",
        "conversations",
        "logs",
      ].every((key) => Array.isArray(saved[key as keyof AppState]))
    )
      return seedState();
    const hydrated = enrichState(saved);
    return {
      ...hydrated,
      documents: hydrated.documents.map((d) =>
        d.status === "processing"
          ? {
              ...d,
              status: "canceled",
              error: "页面已重新加载，演示任务已中断，可重新构建。",
            }
          : d,
      ),
      conversations: saved.conversations.map((c) => ({
        ...c,
        messages: c.messages.map((m) =>
          m.status === "generating"
            ? { ...m, status: "canceled", fullText: undefined }
            : m,
        ),
      })),
      logs: hydrated.logs.map((l) =>
        l.status === "running"
          ? {
              ...l,
              status: "canceled",
              tokens: null,
              error: "页面重新加载，演示运行已中断。",
            }
          : l,
      ),
    };
  } catch {
    return seedState();
  }
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [mode, chooseMode] = useState<DataMode>(() =>
    localStorage.getItem("knowledge-hub-data-mode") === "live"
      ? "live"
      : "demo",
  );
  const setMode = (value: DataMode) => {
    localStorage.setItem("knowledge-hub-data-mode", value);
    chooseMode(value);
  };
  return mode === "live" ? (
    <LiveStoreProvider setMode={setMode}>{children}</LiveStoreProvider>
  ) : (
    <DemoStoreProvider setMode={setMode}>{children}</DemoStoreProvider>
  );
}

function DemoStoreProvider({
  children,
  setMode,
}: {
  children: ReactNode;
  setMode: (mode: DataMode) => void;
}) {
  const [state, update] = useState<AppState>(loadState);
  const [storageError, setStorageError] = useState(false);
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  }, [state]);

  useEffect(() => {
    const timer = window.setInterval(
      () =>
        update((prev) => {
          if (
            !prev.documents.some((d) => d.status === "processing") &&
            !prev.conversations.some((c) =>
              c.messages.some((m) => m.status === "generating"),
            )
          )
            return prev;
          let logs = [...prev.logs];
          const documents = prev.documents.map((d) => {
            if (d.status !== "processing") return d;
            const elapsed = Date.now() - (d.startedAt || Date.now());
            const progress = Math.min(100, Math.round(elapsed / 42));
            if (elapsed < 4200) return { ...d, progress };
            const failed = d.failNext;
            const kb = prev.knowledgeBases.find((k) => k.id === d.kbId);
            const parsing = d.parsing || kb?.parsing || DEFAULT_PARSING;
            const segments = failed
              ? d.segments
              : makeChunks(
                  d,
                  d.chunkSizeOverride || kb?.chunkSize || 600,
                  parsing.delimiter,
                  d.overlapOverride ?? kb?.overlap ?? 0,
                );
            logs = logs.map((l) =>
              l.id === `job-${d.id}`
                ? {
                    ...l,
                    status: failed ? "failed" : "completed",
                    tokens: failed ? null : 620 + d.name.length * 15,
                    inputTokens: failed ? null : 620 + d.name.length * 15,
                    outputTokens: failed ? null : 0,
                    duration: 4.2,
                    error: failed
                      ? "第 8 页 OCR 超时（模拟）。可重试失败页。"
                      : undefined,
                    stages: [
                      { name: "文件校验", ms: 80, status: "ok" },
                      {
                        name: "解析与云端识别",
                        ms: 2700,
                        status: failed ? "failed" : "ok",
                      },
                      {
                        name: "分块与索引",
                        ms: failed ? 0 : 1420,
                        status: failed ? "skipped" : "ok",
                      },
                    ],
                  }
                : l,
            );
            const builtDoc: KnowledgeDoc = {
              ...d,
              status: failed ? ("failed" as const) : ("ready" as const),
              progress: failed ? 65 : 100,
              chunks: segments?.length || 0,
              segments,
              indexParsingVersion: failed
                ? d.indexParsingVersion
                : kb?.parsingVersion || 1,
              previousReady: failed ? d.previousReady : false,
              failNext: false,
              error: failed
                ? "第 8 页识别超时，尚未发布索引。可重试失败页。"
                : undefined,
              updatedAt: now(),
            };
            if (!failed && parsing.autoMetadata) {
              const metadataExtraction = simulatedMetadata(
                builtDoc,
                parsing.metadataModel ||
                  prev.models.find((model) => model.capability === "llm")
                    ?.model ||
                  "模拟 LLM",
              );
              return demoApplyManualMetadata(
                {
                  ...builtDoc,
                  metadataExtraction,
                  segments: builtDoc.segments?.map((segment) => ({
                    ...segment,
                    aiKeywords: metadataExtraction.keywords,
                    aiQuestions: metadataExtraction.questions,
                  })),
                },
                builtDoc.manualMetadata || {},
              );
            }
            return builtDoc;
          });
          const conversations = prev.conversations.map((c) => ({
            ...c,
            messages: c.messages.map((m) => {
              if (m.status !== "generating" || !m.fullText || !m.startedAt)
                return m;
              const length = Math.max(
                0,
                Math.floor((Date.now() - m.startedAt - 700) / 14),
              );
              const text = m.fullText.slice(0, length);
              const done = text.length >= m.fullText.length;
              if (done)
                logs = logs.map((l) =>
                  l.id === m.runId
                    ? {
                        ...l,
                        status: m.citations.length
                          ? "completed"
                          : "no_evidence",
                        duration: Number(
                          ((Date.now() - m.startedAt!) / 1000).toFixed(1),
                        ),
                        tokens: m.citations.length
                          ? 780 + m.fullText!.length * 2
                          : 30,
                        inputTokens: m.citations.length ? 780 : 30,
                        outputTokens: m.citations.length
                          ? m.fullText!.length * 2
                          : 0,
                      }
                    : l,
                );
              return {
                ...m,
                text,
                status: done
                  ? m.citations.length
                    ? ("completed" as const)
                    : ("no_evidence" as const)
                  : ("generating" as const),
                fullText: done ? undefined : m.fullText,
              };
            }),
          }));
          return { ...prev, documents, logs, conversations };
        }),
      140,
    );
    return () => window.clearInterval(timer);
  }, []);

  const importFiles = useCallback(
    (
      kbId: string,
      files: { name: string; size: number }[],
      fail: boolean,
      autoBuild = true,
    ) => {
      if (state.role === "user") return { added: 0, skipped: files.length };
      const existing = new Set(
        state.documents.filter((d) => d.kbId === kbId).map((d) => d.name),
      );
      let skipped = 0;
      const docs: KnowledgeDoc[] = [];
      for (const f of files) {
        if (existing.has(f.name)) {
          skipped++;
          continue;
        }
        existing.add(f.name);
        const format = f.name.split(".").pop()!.toUpperCase();
        const fixture = { ...inferFixture(f.name) };
        if (["DOC", "DOCX"].includes(format))
          fixture.location = `标题 / ${fixture.content.split("\n")[1] || "段落 1"}`;
        if (format === "MD")
          fixture.location = `第 1–${fixture.content.split("\n").length} 行 · 示例内容`;
        if (["PNG", "JPG", "JPEG", "WEBP"].includes(format)) {
          fixture.location = "图片 1 · 示例区域";
          fixture.provenance = "AI 图像说明";
        }
        docs.push({
          id: uid("doc"),
          kbId,
          name: f.name,
          format,
          size: f.size,
          status: autoBuild ? "processing" : "uploaded",
          enabled: true,
          chunks: 0,
          updatedAt: now(),
          progress: 0,
          startedAt: Date.now(),
          failNext: fail && docs.length === 0,
          metadata: {},
          ...fixture,
        });
      }
      update((prev) => ({
        ...prev,
        documents: [...docs, ...prev.documents],
        logs: [
          ...docs
            .filter(() => autoBuild)
            .map((d) => ({
              id: `job-${d.id}`,
              kind: "ingestion" as const,
              knowledgeBaseId: kbId,
              query: d.name,
              assistantName:
                prev.knowledgeBases.find((k) => k.id === kbId)?.name || "",
              model: "演示解析链路",
              status: "running" as const,
              inputTokens: null,
              outputTokens: null,
              tokens: null,
              duration: 0,
              createdAt: now(),
              stages: [],
              citations: [],
              configVersion: 1,
            })),
          ...prev.logs,
        ],
      }));
      return { added: docs.length, skipped };
    },
    [state.documents, state.role],
  );

  const retryDoc = useCallback(
    (id: string) =>
      update((prev) => {
        if (prev.role === "user") return prev;
        const doc = prev.documents.find((d) => d.id === id);
        if (!doc || doc.status === "processing") return prev;
        const parsing =
          doc.parsing ||
          prev.knowledgeBases.find((k) => k.id === doc.kbId)?.parsing ||
          DEFAULT_PARSING;
        const required = [
          "embedding",
          ...(parsing.autoMetadata ? ["llm"] : []),
          ...(["PNG", "JPG", "JPEG", "WEBP"].includes(doc.format) ||
          (doc.format === "PDF" && parsing.pdfParser !== "text")
            ? ["ocr"]
            : []),
          ...(parsing.understandImages &&
          ["PDF", "DOC", "DOCX", "PNG", "JPG", "JPEG", "WEBP"].includes(
            doc.format,
          )
            ? ["vision"]
            : []),
        ];
        const missing = prev.models.filter(
          (m) => required.includes(m.capability) && m.status !== "ready",
        );
        if (missing.length)
          return {
            ...prev,
            documents: prev.documents.map((d) =>
              d.id === id
                ? {
                    ...d,
                    error: `${missing.map((m) => m.label).join("、")}尚未就绪，无法启动构建。`,
                  }
                : d,
            ),
          };
        const log: RunLog = {
          id: `job-${id}`,
          kind: "ingestion",
          knowledgeBaseId: doc.kbId,
          query: doc.name,
          assistantName:
            prev.knowledgeBases.find((k) => k.id === doc.kbId)?.name || "",
          model: "演示解析链路",
          status: "running",
          inputTokens: null,
          outputTokens: null,
          tokens: null,
          duration: 0,
          createdAt: now(),
          stages: [],
          citations: [],
          configVersion: 1,
        };
        return {
          ...prev,
          documents: prev.documents.map((d) =>
            d.id === id
              ? {
                  ...d,
                  previousReady: d.status === "ready" || d.previousReady,
                  status: "processing",
                  startedAt: Date.now(),
                  progress: 0,
                  failNext: false,
                  error: undefined,
                }
              : d,
          ),
          logs: [
            log,
            ...prev.logs.map((l) =>
              l.id === log.id ? { ...l, id: uid("job-history") } : l,
            ),
          ],
        };
      }),
    [],
  );

  const cancelDoc = useCallback(
    (id: string) =>
      update((prev) =>
        prev.role === "user"
          ? prev
          : {
              ...prev,
              documents: prev.documents.map((d) =>
                d.id === id
                  ? {
                      ...d,
                      status: "canceled",
                      error: "演示构建已取消，可重新构建。",
                    }
                  : d,
              ),
              logs: prev.logs.map((l) =>
                l.id === `job-${id}`
                  ? {
                      ...l,
                      status: "canceled",
                      error: "用户取消演示任务，用量未知。",
                    }
                  : l,
              ),
            },
      ),
    [],
  );

  const sendQuestion = useCallback(
    (conversationId: string, query: string, regenerate = false) =>
      update((prev) => {
        const convo = prev.conversations.find((c) => c.id === conversationId);
        const liveAssistant = prev.assistants.find(
          (a) => a.id === convo?.assistantId,
        );
        const assistant = convo?.assistantSnapshot || liveAssistant;
        if (
          !convo ||
          !liveAssistant?.enabled ||
          !assistant ||
          !query.trim() ||
          convo.messages.some((m) => m.status === "generating")
        )
          return prev;
        const historyQuery =
          convo.messages.filter((m) => m.role === "user").at(-1)?.text || "";
        const effectiveQuery =
          assistant.multiTurn !== false &&
          /^(那|这些|它|上述|还|需要谁)/.test(query)
            ? `${historyQuery} ${query}`
            : query;
        const names = Object.fromEntries(
          prev.knowledgeBases.map((k) => [k.id, k.name]),
        );
        const citations = retrieve(
          effectiveQuery,
          prev.documents,
          convo.kbIds,
          names,
          assistant.topK,
          { ...DEFAULT_RETRIEVAL, ...assistant.retrieval },
          assistant.candidateK,
          assistant.useRerank,
        );
        let fullText = citations.length
          ? makeAnswer(effectiveQuery, citations)
          : assistant.noEvidenceReply || makeAnswer(effectiveQuery, citations);
        if (assistant.showQuote === false)
          fullText = fullText
            .replace(/请展开来源核对/g, "请联系知识库管理人员核对原文")
            .replace(/你可以展开来源核对原表/g, "请联系知识库管理人员核对原表");
        const runId = uid("run");
        let previousMessages = convo.messages;
        if (regenerate) {
          const lastUser = previousMessages.findLastIndex(
            (m) => m.role === "user",
          );
          previousMessages = previousMessages.slice(0, Math.max(0, lastUser));
        }
        return {
          ...prev,
          conversations: prev.conversations.map((c) =>
            c.id !== conversationId
              ? c
              : {
                  ...c,
                  title: c.messages.length ? c.title : query.slice(0, 20),
                  updatedAt: now(),
                  messages: [
                    ...previousMessages,
                    {
                      id: uid("msg"),
                      role: "user",
                      text: query,
                      citations: [],
                      status: "completed",
                    },
                    {
                      id: uid("msg"),
                      role: "assistant",
                      text: "",
                      fullText,
                      citations,
                      status: "generating",
                      runId,
                      startedAt: Date.now(),
                    },
                  ],
                },
          ),
          logs: [
            {
              id: runId,
              kind: "chat",
              query,
              assistantName: assistant.name,
              model: assistant.model,
              status: "running",
              inputTokens: null,
              outputTokens: null,
              tokens: null,
              duration: 0,
              createdAt: now(),
              stages: [
                { name: "问题处理", ms: 100, status: "ok" },
                { name: "混合检索", ms: 220, status: "ok" },
                {
                  name: "Rerank 重排",
                  ms: assistant.useRerank ? 380 : 0,
                  status: assistant.useRerank ? "ok" : "skipped",
                },
                {
                  name: "生成回答",
                  ms: citations.length ? 2400 : 0,
                  status: citations.length ? "ok" : "skipped",
                },
              ],
              citations,
              configVersion: convo.configVersion,
            },
            ...prev.logs,
          ],
        };
      }),
    [],
  );

  const stopAnswer = useCallback(
    (conversationId: string) =>
      update((prev) => {
        const active = prev.conversations
          .find((c) => c.id === conversationId)
          ?.messages.find((m) => m.status === "generating");
        if (!active) return prev;
        return {
          ...prev,
          conversations: prev.conversations.map((c) =>
            c.id === conversationId
              ? {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.status === "generating"
                      ? { ...m, status: "canceled", fullText: undefined }
                      : m,
                  ),
                }
              : c,
          ),
          logs: prev.logs.map((l) =>
            l.id === active.runId
              ? {
                  ...l,
                  status: "canceled",
                  tokens: null,
                  inputTokens: null,
                  outputTokens: null,
                  duration: Number(
                    (
                      (Date.now() - (active.startedAt || Date.now())) /
                      1000
                    ).toFixed(1),
                  ),
                  error: "用户停止生成，完整用量未返回（演示）。",
                }
              : l,
          ),
        };
      }),
    [],
  );
  const reset = useCallback(() => update(seedState()), []);
  return (
    <StoreContext.Provider
      value={{
        state,
        update,
        storageError,
        mode: "demo",
        setMode,
        loading: false,
        connected: true,
        syncing: false,
        connectionError: "",
        reconnect: () => {},
        flush: () => Promise.resolve(),
        retrieveKnowledge: async (request) =>
          retrieve(
            request.query,
            state.documents,
            request.kbIds,
            Object.fromEntries(state.knowledgeBases.map((k) => [k.id, k.name])),
            request.topK,
            request.retrieval,
            request.candidateK,
            request.useRerank,
          ),
        saveSecret: async () => {
          throw new Error("请切换真实后端模式后配置凭证。");
        },
        testModel: async () => {
          throw new Error("演示模式请使用情景测试。");
        },
        downloadDocument: async () => {
          throw new Error("示例资料没有上传的原文件。");
        },
        extractMetadata: async (id, preserveManual = true) => {
          const target = state.documents.find((doc) => doc.id === id);
          if (
            !target ||
            !(target.status === "ready" || target.previousReady) ||
            !target.segments?.length ||
            target.status === "processing" ||
            state.role === "user"
          )
            throw new Error("请先构建文档，并使用管理员或知识库管理人员角色。");
          update((prev) => ({
            ...prev,
            documents: prev.documents.map((doc) =>
              doc.id === id
                ? {
                    ...doc,
                    metadataExtraction: {
                      model: "模拟 LLM",
                      version: doc.metadataExtraction?.version || 0,
                      facts: [],
                      keywords: [],
                      questions: [],
                      ...doc.metadataExtraction,
                      status: "processing",
                    },
                  }
                : doc,
            ),
          }));
          await new Promise((resolve) => window.setTimeout(resolve, 700));
          update((prev) => {
            const doc = prev.documents.find((item) => item.id === id);
            if (!doc) return prev;
            const kb = prev.knowledgeBases.find((item) => item.id === doc.kbId);
            const config = doc.parsing || kb?.parsing;
            const extraction = simulatedMetadata(
              doc,
              config?.metadataModel ||
                prev.models.find((model) => model.capability === "llm")
                  ?.model ||
                "模拟 LLM",
            );
            const result = demoApplyManualMetadata(
              {
                ...doc,
                metadataExtraction: extraction,
                segments: doc.segments?.map((segment) => ({
                  ...segment,
                  aiKeywords: extraction.keywords,
                  aiQuestions: extraction.questions,
                })),
              },
              preserveManual ? doc.manualMetadata || {} : {},
            );
            return {
              ...prev,
              documents: prev.documents.map((item) =>
                item.id === id ? result : item,
              ),
              logs: [
                {
                  id: uid("meta-demo"),
                  kind: "ingestion",
                  knowledgeBaseId: doc.kbId,
                  query: `${doc.name} · 元数据抽取（模拟）`,
                  assistantName: kb?.name || "",
                  model: extraction.model,
                  status: "completed",
                  inputTokens: null,
                  outputTokens: null,
                  tokens: null,
                  duration: 0.7,
                  createdAt: now(),
                  stages: [
                    { name: "元数据抽取（模拟）", ms: 700, status: "ok" },
                  ],
                  citations: [],
                  configVersion: extraction.version,
                },
                ...prev.logs,
              ],
            };
          });
        },
        importFiles,
        retryDoc,
        cancelDoc,
        sendQuestion,
        stopAnswer,
        reset,
      }}
    >
      {children}
    </StoreContext.Provider>
  );
}
