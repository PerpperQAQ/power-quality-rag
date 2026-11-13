import { DEFAULT_RETRIEVAL, enrichState, makeChunks } from "./configuration";
import type {
  AppState,
  Citation,
  KnowledgeDoc,
  ModelBinding,
  RetrievalConfig,
} from "./types";

export const STORAGE_KEY = "knowledge-hub-prototype-v1";
export const ROLE_LABELS = {
  admin: "管理员",
  manager: "知识库管理人员",
  user: "使用者",
};
export const uid = (prefix: string) =>
  `${prefix}_${crypto.randomUUID().slice(0, 10)}`;
export const now = () => new Date().toISOString();
export const timeText = (date: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(date));
export const sizeText = (bytes: number) =>
  bytes >= 1048576
    ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
export const numberText = (n: number) =>
  new Intl.NumberFormat("zh-CN").format(n);
export const isRetrievable = (doc: KnowledgeDoc) =>
  doc.enabled &&
  (doc.status === "ready" || !!doc.previousReady) &&
  (doc.detailsOmitted
    ? (doc.enabledChunks ?? 0) > 0
    : doc.segments ? doc.segments.some((c) => c.enabled) : doc.chunks > 0);

export const fixtures = {
  expense: {
    category: "expense",
    content:
      "差旅报销管理办法\n第三章 · 报销材料与审批\n员工应在出差结束后 15 个工作日内提交报销申请。\n必需附件：合规发票、出差审批单、行程及付款凭证。\n审批流程：直属负责人审批 → 部门负责人复核 → 财务审核。\n住宿标准：一线城市每晚 600 元，其他城市每晚 400 元。\n交通费按实际发生额报销，高铁限二等座。",
    location: "第 3 页 · 第三章",
    provenance: "原生提取",
  },
  handbook: {
    category: "handbook",
    content:
      "员工入职手册\n新员工入职清单\n入职首日请携带身份证、学历证明及银行卡信息。\n行政同事办理门禁和工位；IT 同事开通企业邮箱和办公账号。\n入职第一周需完成信息安全培训和制度学习。\n试用期结束前两周，由直属负责人组织转正评估。",
    location: "第一节 · 入职准备",
    provenance: "原生提取",
  },
  leave: {
    category: "leave",
    content:
      "请假与考勤说明\n年假申请\n员工连续工作满一年可享受带薪年假，具体天数按累计工龄核定。\n请提前 3 个工作日在办公系统中提交申请，由直属负责人审批。\n紧急请假可先告知负责人，并在返岗后补齐申请。\n病假需提供医疗机构开具的有效证明。",
    location: "年假申请 · 第 12–24 行",
    provenance: "原生提取",
  },
  process: {
    category: "process",
    content:
      "差旅审批流程图\n出差申请 → 直属负责人审批 → 预订交通住宿 → 完成出差 → 提交报销 → 财务审核。\n审批驳回时，申请人补充说明后重新提交。\n图片文字与流程关系均为演示样本，请以实际接入后的原图核验结果为准。",
    location: "图片 1 · 完整流程",
    provenance: "AI 图像说明",
  },
  table: {
    category: "expense",
    content:
      "差旅费用标准表\n城市级别 | 住宿上限（元/晚）| 交通标准\n一线城市 | 600 | 高铁二等座\n其他城市 | 400 | 高铁二等座\n表中金额为演示数据，不是实际企业报销制度。",
    location: "差旅标准 · A1:C8",
    provenance: "表格提取",
  },
  product: {
    category: "product",
    content:
      "产品接入指南\nAPI 接入流程\n在管理控制台创建项目并生成服务端 API Key。\n请求使用 Bearer Token 进行身份校验，密钥只能保存在服务端。\n接口返回 request_id，可用于排查异常。\n遇到 429 响应时，遵循 Retry-After 提示进行退避重试。",
    location: "第二章 · API 接入",
    provenance: "原生提取",
  },
  brand: {
    category: "brand",
    content:
      "品牌使用规范\n视觉与文案原则\n主要品牌色为蓝色，正文优先使用深灰色。\n品牌标志周围保留不小于标志高度四分之一的安全空间。\n面向客户的文案应清晰、具体，避免夸大和难以验证的表达。",
    location: "第 6 页 · 视觉原则",
    provenance: "原生提取",
  },
};

export function inferFixture(name: string) {
  if (/流程|审批.*图/.test(name)) return fixtures.process;
  if (/标准|xlsx?|csv/.test(name)) return fixtures.table;
  if (/请假|考勤|年假/.test(name)) return fixtures.leave;
  if (/入职|员工|手册/.test(name)) return fixtures.handbook;
  if (/品牌|市场/.test(name)) return fixtures.brand;
  if (/API|接口|产品|接入/i.test(name)) return fixtures.product;
  if (/差旅|报销|费用/.test(name)) return fixtures.expense;
  return {
    category: "custom",
    content: `${name}\n这是前端原型的演示解析内容。原型只读取文件名和大小，不会上传或解析实际文件。\n正式版本将在后端接入后展示原文、表格和图片识别结果。`,
    location: "演示片段 1",
    provenance: "演示内容",
  };
}

const modelSeed: ModelBinding[] = [
  {
    capability: "llm",
    label: "对话模型",
    description: "理解问题，组织有依据的回答",
    model: "qwen3.7-plus",
    status: "ready",
  },
  {
    capability: "embedding",
    label: "Embedding 模型",
    description: "把知识与问题转换为可检索的向量",
    model: "text-embedding-v4",
    status: "ready",
  },
  {
    capability: "rerank",
    label: "Rerank 模型",
    description: "对召回片段重新排序，优先选择相关资料",
    model: "qwen3-rerank",
    status: "ready",
  },
  {
    capability: "ocr",
    label: "OCR 模型",
    description: "提取扫描件和图片中的文字、表格",
    model: "qwen-vl-ocr",
    status: "ready",
  },
  {
    capability: "vision",
    label: "视觉理解模型",
    description: "理解图表、流程图，并核验原图依据",
    model: "qwen3.7-plus",
    status: "ready",
  },
];

export function seedState(): AppState {
  const base = "2026-10-26T02:38:00Z";
  const spec: [
    string,
    string,
    string,
    keyof typeof fixtures,
    KnowledgeDoc["status"],
  ][] = [
    ["d-expense", "kb-policy", "差旅报销管理办法.pdf", "expense", "ready"],
    ["d-handbook", "kb-policy", "员工入职手册.docx", "handbook", "ready"],
    ["d-leave", "kb-policy", "请假与考勤说明.md", "leave", "ready"],
    ["d-process", "kb-policy", "差旅审批流程.png", "process", "ready"],
    ["d-table", "kb-policy", "差旅费用标准.xlsx", "table", "ready"],
    ["d-scan", "kb-policy", "历史制度扫描件.pdf", "expense", "failed"],
    ["d-review", "kb-policy", "供应商入驻须知.pdf", "handbook", "review"],
    ["d-api", "kb-product", "产品 API 接入指南.pdf", "product", "ready"],
    ["d-dev", "kb-product", "开发环境配置.md", "product", "ready"],
    ["d-error", "kb-product", "常见错误码.csv", "product", "ready"],
    ["d-brand", "kb-brand", "品牌使用规范.pdf", "brand", "ready"],
    ["d-copy", "kb-brand", "对外文案检查清单.docx", "brand", "ready"],
  ];
  const documents: KnowledgeDoc[] = spec.map(
    ([id, kbId, name, fixture, status], i) => ({
      id,
      kbId,
      name,
      format: name.split(".").pop()!.toUpperCase(),
      size: (i + 1) * 125400,
      ...fixtures[fixture],
      status,
      enabled: true,
      chunks: status === "ready" ? 8 + i * 3 : 0,
      updatedAt: base,
      progress: status === "ready" ? 100 : 0,
      error:
        status === "failed"
          ? "第 8 页识别超时，尚未发布索引。可重试失败页。"
          : status === "review"
            ? "第 2 页表头识别不完整，请检查解析结果。"
            : undefined,
    }),
  );
  const assistants = [
    {
      id: "as-policy",
      name: "企业知识助手",
      description: "报销、考勤、入职制度，随时找到可靠答案。",
      kbIds: ["kb-policy"],
      enabled: true,
      model: "qwen3.7-plus",
      greeting:
        "你好，我是企业知识助手。\n我会根据员工制度库中的资料回答，并为你标注来源。",
      prompt: "仅根据所选知识库回答。依据不足时明确说明，不编造企业制度。",
      topK: 6,
      candidateK: 30,
      useRerank: true,
      configVersion: 1,
      retrievalVersion: 1,
      color: "blue",
    },
    {
      id: "as-product",
      name: "研发接入助手",
      description: "查阅接入文档，解决产品与接口使用问题。",
      kbIds: ["kb-product"],
      enabled: true,
      model: "qwen3.7-plus",
      greeting: "你好，我可以协助查阅产品与研发资料。",
      prompt: "根据产品文档回答，涉及代码和接口时提供清晰步骤。",
      topK: 6,
      candidateK: 30,
      useRerank: true,
      configVersion: 1,
      retrievalVersion: 1,
      color: "green",
    },
    {
      id: "as-brand",
      name: "品牌内容助手",
      description: "帮助团队保持一致的品牌表达。",
      kbIds: ["kb-brand"],
      enabled: false,
      model: "qwen3.7-plus",
      greeting: "一起写出更清晰的品牌内容。",
      prompt: "以品牌规范为依据给出建议。",
      topK: 6,
      candidateK: 30,
      useRerank: true,
      configVersion: 1,
      retrievalVersion: 1,
      color: "orange",
    },
  ];
  return enrichState({
    version: 1,
    role: "admin",
    knowledgeBases: [
      {
        id: "kb-policy",
        name: "员工制度库",
        description: "企业制度、日常办公与员工服务，给每个问题一个可靠的出处。",
        color: "blue",
        icon: "team",
        embedding: "text-embedding-v4",
        chunkSize: 600,
        overlap: 100,
        updatedAt: base,
      },
      {
        id: "kb-product",
        name: "产品与研发",
        description: "产品说明、技术文档与接口指南，让团队知识持续积累。",
        color: "green",
        icon: "code",
        embedding: "text-embedding-v4",
        chunkSize: 600,
        overlap: 100,
        updatedAt: base,
      },
      {
        id: "kb-brand",
        name: "品牌与市场",
        description: "统一的品牌规范与内容资产，保持对外表达的一致性。",
        color: "orange",
        icon: "brand",
        embedding: "text-embedding-v4",
        chunkSize: 600,
        overlap: 100,
        updatedAt: base,
      },
      {
        id: "kb-project",
        name: "项目协作",
        description: "收录项目文档、复盘与经验，让有价值的实践被再次使用。",
        color: "purple",
        icon: "folder",
        embedding: "text-embedding-v4",
        chunkSize: 600,
        overlap: 100,
        updatedAt: base,
      },
    ],
    documents,
    models: modelSeed.map((m) => ({ ...m })),
    assistants,
    conversations: [],
    logs: [
      {
        id: "run-demo-001",
        kind: "chat",
        query: "差旅报销需要哪些附件？",
        assistantName: "企业知识助手",
        model: "qwen3.7-plus",
        status: "completed",
        inputTokens: 1024,
        outputTokens: 260,
        tokens: 1284,
        duration: 4.8,
        createdAt: base,
        stages: [
          { name: "问题处理", ms: 120, status: "ok" },
          { name: "混合检索", ms: 260, status: "ok" },
          { name: "Rerank 重排", ms: 350, status: "ok" },
          { name: "生成回答", ms: 4070, status: "ok" },
        ],
        citations: [],
        configVersion: 1,
      },
      {
        id: "run-demo-002",
        kind: "chat",
        query: "API 返回 429 应该怎么处理？",
        assistantName: "研发接入助手",
        model: "qwen3.7-plus",
        status: "completed",
        inputTokens: 890,
        outputTokens: 196,
        tokens: 1086,
        duration: 3.2,
        createdAt: "2026-10-26T02:20:00Z",
        stages: [
          { name: "混合检索", ms: 210, status: "ok" },
          { name: "Rerank 重排", ms: 290, status: "ok" },
          { name: "生成回答", ms: 2700, status: "ok" },
        ],
        citations: [],
        configVersion: 1,
      },
      {
        id: "job-demo-003",
        kind: "ingestion",
        query: "历史制度扫描件.pdf",
        assistantName: "员工制度库",
        model: "qwen-vl-ocr",
        status: "failed",
        inputTokens: null,
        outputTokens: null,
        tokens: null,
        duration: 30,
        createdAt: "2026-10-26T02:08:00Z",
        stages: [
          { name: "文件校验", ms: 80, status: "ok" },
          { name: "原生提取", ms: 120, status: "ok" },
          { name: "云端 OCR", ms: 29800, status: "failed" },
        ],
        citations: [],
        error: "第 8 页 OCR 请求超时（演示情景），用量未返回。",
        configVersion: 1,
      },
      {
        id: "run-demo-004",
        kind: "chat",
        query: "今年公司的营收是多少？",
        assistantName: "企业知识助手",
        model: "qwen3.7-plus",
        status: "no_evidence",
        inputTokens: 24,
        outputTokens: 0,
        tokens: 24,
        duration: 0.6,
        createdAt: "2026-10-25T08:42:00Z",
        stages: [
          { name: "混合检索", ms: 600, status: "ok" },
          { name: "生成回答", ms: 0, status: "skipped" },
        ],
        citations: [],
        configVersion: 1,
      },
    ],
  });
}

export function retrieve(
  query: string,
  docs: KnowledgeDoc[],
  kbIds: string[],
  kbNames: Record<string, string>,
  topK = 6,
  config: RetrievalConfig = DEFAULT_RETRIEVAL,
  candidateK = 30,
  useRerank = true,
): Citation[] {
  const categories: string[] = [];
  if (/报销|差旅|发票|出差|住宿|交通|费用|附件/.test(query))
    categories.push("expense", "process");
  if (/流程|审批/.test(query)) categories.push("process", "expense");
  if (/请假|考勤|年假|病假/.test(query)) categories.push("leave");
  if (/入职|转正|新员工|培训|门禁/.test(query)) categories.push("handbook");
  if (/api|接口|429|开发|接入|密钥|request/i.test(query))
    categories.push("product");
  if (/品牌|市场|文案|标志|视觉/.test(query)) categories.push("brand");
  const terms =
    query.toLowerCase().match(/[a-z0-9]+|[\u4e00-\u9fff]{2}/g) || [];
  const candidates: Citation[] = [];
  for (const d of docs) {
    if (!kbIds.includes(d.kbId) || !isRetrievable(d)) continue;
    if (config.fileIds.length && !config.fileIds.includes(d.id)) continue;
    if (
      config.metadataKey &&
      !(d.metadata?.[config.metadataKey] || "")
        .split("；")
        .map((value) => value.trim())
        .filter(Boolean)
        .includes(config.metadataValue.trim())
    )
      continue;
    for (const chunk of d.segments || makeChunks(d)) {
      if (!chunk.enabled) continue;
      const haystack =
        `${d.name} ${chunk.content} ${chunk.keywords.join(" ")} ${chunk.questions.join(" ")} ${(chunk.aiKeywords || []).join(" ")} ${(chunk.aiQuestions || []).join(" ")} ${Object.values(d.metadata || {}).join(" ")}`.toLowerCase();
      const matches = terms.filter((t) => haystack.includes(t)).length;
      const categoryRank = categories.indexOf(d.category);
      if (categoryRank < 0 && !matches) continue;
      const vectorScore =
        categoryRank >= 0
          ? Math.max(0.55, 0.96 - categoryRank * 0.08)
          : 0.45 + Math.min(matches * 0.08, 0.45);
      const textScore = matches ? Math.min(0.98, 0.4 + matches * 0.13) : 0.12;
      const hybridScore =
        vectorScore * config.vectorWeight +
        textScore * (1 - config.vectorWeight);
      if (hybridScore < config.threshold) continue;
      const rerankScore = Math.min(
        0.99,
        hybridScore * 0.65 + vectorScore * 0.35,
      );
      candidates.push({
        id: chunk.id,
        docId: d.id,
        name: d.name,
        format: d.format,
        kbName: kbNames[d.kbId] || "知识库",
        location: chunk.location,
        content: chunk.content,
        provenance: d.provenance,
        vectorScore,
        textScore,
        hybridScore,
        rerankScore: useRerank ? rerankScore : undefined,
        score: useRerank ? rerankScore : hybridScore,
      });
    }
  }
  return candidates
    .sort((a, b) => (b.hybridScore || 0) - (a.hybridScore || 0))
    .slice(0, candidateK)
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);
}

export function makeAnswer(_query: string, citations: Citation[]): string {
  if (!citations.length)
    return "未在所选知识库中找到足够依据。\n你可以换一种问法、检查知识范围，或请知识库管理人员补充资料。\n原型仅匹配内置示例内容，不会调用模型生成真实回答。";
  const evidence = [...new Set(citations.map((c) => c.content))].slice(0, 3);
  return `根据当前命中的知识片段：\n${evidence.join("\n\n")}\n\n以上为演示检索内容，请展开来源核对。`;
}
