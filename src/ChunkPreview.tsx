import { useState } from "react";
import { Alert, Button, Collapse, Form, Input, Space, Tag } from "antd";
import { api } from "./live-api";
import { DEFAULT_PARSING } from "./configuration";
import { CHUNKING_LABELS, chunkingExample } from "./chunking-methods";
import { useStore } from "./store-context";
import type { KnowledgeChunk } from "./types";
import { StructuredTable } from "./StructuredTable";

interface PreviewResult {
  count: number;
  chunks: KnowledgeChunk[];
  warnings: string[];
  truncated: boolean;
}

export function ChunkPreview({embedding}: {embedding?: string}) {
  const form = Form.useFormInstance();
  const { state, mode } = useStore();
  const method = Form.useWatch(["parsing", "strategy"]) || "general";
  const [text, setText] = useState("");
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [previewConfig, setPreviewConfig] = useState("");
  const [previewText, setPreviewText] = useState("");
  // Compare the actual form values at render time. Old results stay visible and
  // are explicitly marked stale when parameters or input change.
  const values = Form.useWatch([], form);
  const config = JSON.stringify({
    parsing: {...DEFAULT_PARSING, ...(values?.parsing || {})},
    chunkSize: values?.chunkSize ?? 600,
    overlap: values?.overlap ?? 60,
    embedding: embedding || values?.embedding || state.models.find((m) => m.capability === "embedding")?.model || "text-embedding-v4",
  });
  const preview = async () => {
    setRunning(true);
    setError("");
    setResult(null);
    const requestedText = text;
    const requestedConfig = config;
    try {
      await form.validateFields(["chunkSize", "overlap", ["parsing", "strategy"], ["parsing", "delimiter"]]);
      const data = await api<PreviewResult>("/chunking/preview", state.role, {
        method: "POST",
        body: JSON.stringify({...JSON.parse(requestedConfig), text: requestedText}),
      });
      setResult(data);
      setPreviewConfig(requestedConfig);
      setPreviewText(requestedText);
    } catch (e) {
      setError(e instanceof Error ? e.message : "请检查切分参数中的必填项后再预览。后端未返回有效结果时不会显示演示分块。");
    } finally {
      setRunning(false);
    }
  };
  return <Collapse className="chunk-preview" items={[{
    key: "preview",
    label: "切分预览 · 使用真实后端规则",
    children: <>
      <p className="muted small-text">粘贴最多 20,000 字符，查看当前配置的真实切分结果。仅发送到本机后端，不调用 OCR、LLM 或 Embedding，也不会修改已保存的索引。预览仅包含粘贴内容，不能代替原文件解析。</p>
      {mode === "demo" && <Alert type="info" showIcon title="演示构建使用样例片段；此预览需要本机后端运行，调用的是真实切分器。" />}
      {method === "page" && <p className="muted">用 ===PAGE=== 独占一行模拟分页。实际构建使用 PDF 真实页码。</p>}
      <Input.TextArea aria-label="切分预览原文" value={text} onChange={(e) => setText(e.target.value)} maxLength={20000} showCount rows={7} placeholder="粘贴原文，或点击下方填入示例" />
      <Space wrap style={{margin: "16px 0"}}>
        <Button htmlType="button" disabled={running} onClick={() => {setText(chunkingExample(method)); setResult(null); setError("");}}>填入当前方法示例</Button>
        <Button htmlType="button" type="primary" loading={running} disabled={!text.trim()} onClick={() => void preview()}>预览切分</Button>
      </Space>
      {error && <Alert type="error" showIcon title={error} />}
      {result && <>
        {(previewConfig !== config || previewText !== text) && <Alert type="warning" showIcon title="配置或文本已变化，以下为上次结果，请重新预览。" />}
        <p><Tag color="blue">{result.count} 个分块</Tag>{result.truncated ? "展示前 30 块" : "展示全部分块"} · 未包含后续元数据增强</p>
        {result.warnings.map((warning) => <Alert key={warning} type="warning" showIcon title={warning} style={{marginBottom:8}} />)}
        <div className="chunk-preview-results">{result.chunks.map((chunk, index) => <article className="chunk-preview-item" key={chunk.id}>
          <Space wrap><strong>分块 {index+1}</strong><Tag>{CHUNKING_LABELS[chunk.chunkMethod || "general"]}</Tag><span className="muted small-text">{Array.from(chunk.content).length} 字符</span></Space>
          {!!chunk.sectionPath?.length && <p className="muted small-text">{chunk.sectionPath.join(" / ")}</p>}
          {chunk.table ? <StructuredTable table={chunk.table} /> : <pre>{chunk.content}</pre>}
          <small className="muted">{chunk.location}</small>
        </article>)}</div>
      </>}
    </>,
  }]} />;
}
