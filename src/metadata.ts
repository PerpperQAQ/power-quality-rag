import type { KnowledgeDoc, MetadataExtraction } from "./types";

export const METADATA_FIELDS = [
  "时间",
  "发布日期",
  "生效日期",
  "修订日期",
  "版本",
  "主体信息",
  "负责人",
  "关键词",
  "可能的问题",
  "部门",
];
export const FACT_LABELS = {
  time: "时间",
  version: "版本",
  subject: "主体信息",
  owner: "负责人",
};
export function metadataFieldNames(
  documents: Pick<KnowledgeDoc, "metadata">[],
) {
  return [
    ...new Set([
      ...METADATA_FIELDS,
      ...documents.flatMap((doc) => Object.keys(doc.metadata || {})),
    ]),
  ];
}
export function automaticMetadata(
  extraction?: MetadataExtraction,
): Record<string, string> {
  if (!extraction) return {};
  if (extraction.status !== "completed" && extraction.lastSuccess)
    return automaticMetadata(extraction.lastSuccess);
  const values: Record<string, string[]> = {};
  for (const fact of extraction.facts || []) {
    const key =
      fact.label && METADATA_FIELDS.includes(fact.label)
        ? fact.label
        : FACT_LABELS[fact.field];
    if (!key || !fact.value) continue;
    values[key] = [...new Set([...(values[key] || []), fact.value])];
  }
  if (extraction.keywords?.length) values["关键词"] = extraction.keywords;
  if (extraction.questions?.length) values["可能的问题"] = extraction.questions;
  return Object.fromEntries(
    Object.entries(values).map(([key, items]) => [
      key,
      [...new Set(items)].join("；"),
    ]),
  );
}
export function revisedManualMetadata(
  doc: KnowledgeDoc,
  values: Record<string, string>,
) {
  const overrides = { ...(doc.manualMetadata || {}) };
  for (const [key, value] of Object.entries(values)) {
    const next = (value || "").trim();
    if (next !== (doc.metadata?.[key] || "").trim()) overrides[key] = next;
  }
  return overrides;
}
export function demoApplyManualMetadata(
  doc: KnowledgeDoc,
  manualMetadata: Record<string, string>,
) {
  const effective = {
    ...automaticMetadata(doc.metadataExtraction),
    ...manualMetadata,
  };
  return {
    ...doc,
    manualMetadata,
    metadata: Object.fromEntries(
      Object.entries(effective).filter(([, value]) => value.trim()),
    ),
  };
}
export function simulatedMetadata(
  doc: KnowledgeDoc,
  model: string,
): MetadataExtraction {
  const lines = doc.content
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const subject = lines[0] || doc.name;
  const facts: MetadataExtraction["facts"] = [];
  for (const line of lines) {
    const entity = line.match(
      /(?:主体信息|主体|适用对象|适用范围|公司|组织|部门|项目|产品)[：:\s]+([^，,。；;\n]{1,60})/,
    );
    if (entity && !facts.some((fact) => fact.field === "subject"))
      facts.push({
        field: "subject",
        value: entity[1],
        quote: line.slice(0, 200),
        location: doc.location,
        unitId: "demo-unit-1",
      });
    const date = line.match(/(?:20\d{2})[年/.-]\d{1,2}[月/.-]\d{1,2}日?/);
    if (date && !facts.some((fact) => fact.field === "time"))
      facts.push({
        field: "time",
        value: date[0],
        quote: line.slice(0, 200),
        location: doc.location,
        unitId: "demo-unit-1",
      });
    const version = line.match(/版本[：:\s]*([A-Za-z0-9_.-]+)/);
    if (version && !facts.some((fact) => fact.field === "version"))
      facts.push({
        field: "version",
        value: version[1],
        quote: line.slice(0, 200),
        location: doc.location,
        unitId: "demo-unit-1",
      });
    const owner = line.match(/(?:负责人|责任人)[：:\s]+([^，,。；;\n]{1,30})/);
    if (owner && !facts.some((fact) => fact.field === "owner"))
      facts.push({
        field: "owner",
        value: owner[1],
        quote: line.slice(0, 200),
        location: doc.location,
        unitId: "demo-unit-1",
      });
  }
  return {
    status: "completed",
    model,
    extractedAt: new Date().toISOString(),
    version: (doc.metadataExtraction?.version || 0) + 1,
    facts,
    keywords: [subject.slice(0, 30)],
    questions: [`${subject.slice(0, 40)}有哪些要点？`],
    coverage: { totalUnits: 1, processedUnits: 1 },
    warnings: [
      "此结果为前端模拟抽取，不代表云端 LLM 效果。未在示例正文发现的字段保持空白。",
    ],
  };
}
