import type { KnowledgeDoc } from "./types";
import { isRetrievable } from "./demo";

/** Recommend actual indexed questions within this assistant's file scope. */
export function knowledgeSuggestions(documents: KnowledgeDoc[], kbIds: string[], fileIds: string[] = []) {
  const result: { title: string; text: string }[] = [];
  const seen = new Set<string>();
  for (const doc of documents) {
    if (!kbIds.includes(doc.kbId) || !isRetrievable(doc) || fileIds.length && !fileIds.includes(doc.id)) continue;
    for (const question of (doc.metadata?.["可能的问题"] || "").split("；")) {
      const text = question.trim();
      if (!text || text.length > 240 || seen.has(text)) continue;
      seen.add(text);
      result.push({ title: doc.name.replace(/\.[^.]+$/, ""), text });
      if (result.length === 3) return result;
    }
  }
  return result.length ? result : [{ title: "了解知识范围", text: "当前知识库主要涵盖哪些内容？请说明资料来源。" }];
}
