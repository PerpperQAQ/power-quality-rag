import { Alert, Form, Input, InputNumber, Select, Switch } from "antd";
import { useStore } from "./store-context";
import { CHUNKING_METHODS } from "./chunking-methods";
import { ChunkPreview } from "./ChunkPreview";

export function ParsingFields({embedding}: {embedding?: string}) {
  const { state, mode } = useStore();
  const autoMetadata = Form.useWatch(["parsing", "autoMetadata"]);
  const method = Form.useWatch(["parsing", "strategy"]) || "general";
  const info = CHUNKING_METHODS.find((m) => m.value === method);
  const defaultModel =
    state.models.find((model) => model.capability === "llm")?.model || "";
  return (
    <>
      <div className="form-two-columns">
        <Form.Item
          label="切分方法"
          name={["parsing", "strategy"]}
          extra="先提取原文和图表，再按所选规则切分，最后生成检索索引。"
        >
          <Select
            aria-label="切分方法"
            showSearch
            optionFilterProp="label"
            options={CHUNKING_METHODS.map(({value,label}) => ({value,label}))}
          />
        </Form.Item>
        <Form.Item
          label="PDF 解析方式"
          name={["parsing", "pdfParser"]}
          extra="自动先提取原生文字，扫描页回退云端 OCR；首版不部署本地 OCR。"
        >
          <Select
            aria-label="PDF 解析方式"
            options={[
              { value: "auto", label: "自动识别 · 原生提取 + 云端 OCR" },
              { value: "text", label: "仅原生文字提取" },
              { value: "cloud", label: "云端 OCR · 扫描件优先" },
            ]}
          />
        </Form.Item>
      </div>
      <Alert type="info" showIcon title={info?.label || "切分方法"} description={info?.description} style={{marginBottom:20}} />
      {mode === "demo" && <p className="muted small-text">当前为演示模式，构建展示样例片段。下方文本预览调用真实切分器；应用到真实文档需切换“真实后端”模式。</p>}
      <div className="form-two-columns">
        <Form.Item
          label={method === "one" ? "整篇最大长度（字符）" : "单块长度上限（字符）"}
          name="chunkSize"
          rules={[{ required: true }]}
          extra="按字符计数，不是 Token。向量输入预算不足时会进一步细分；整篇和表格行会明确报错。"
        >
          <InputNumber min={100} max={8000} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item label="重叠长度（字符）" name="overlap" rules={[{ required: true }]} extra="仅用于同一区域内的长度细分；问答、章节、条款和逐页方法不跨结构边界重叠。整篇和表格单行不使用重叠。">
          <InputNumber min={0} max={7999} disabled={method === "one"} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          label={method === "delimiter" ? "硬边界分隔符" : "辅助文本分隔符"}
          name={["parsing", "delimiter"]}
          rules={[{ required: method === "delimiter", message: "请输入分隔符" }]}
          extra="使用 \n 表示换行，按字面分隔，不执行正则表达式。"
        >
          <Input aria-label="文本分隔符" maxLength={50} disabled={method === "one" || method === "fixed"} />
        </Form.Item>
        <Form.Item
          label="图表上下文段落数"
          name={["parsing", "imageContext"]}
          extra="保留图表前后段落，辅助理解图意。"
        >
          <InputNumber min={0} max={5} />
        </Form.Item>
        <Form.Item
          label="理解图表与流程图"
          name={["parsing", "understandImages"]}
          valuePropName="checked"
          extra="通过云端视觉模型生成说明，并区分原文和 AI 描述。"
        >
          <Switch />
        </Form.Item>
        <Form.Item
          label="保留表格结构"
          name={["parsing", "tableHtml"]}
          valuePropName="checked"
          extra="保留表头、Sheet 和单元格位置；精确统计计算后续提供。"
        >
          <Switch />
        </Form.Item>
      </div>
      <ChunkPreview embedding={embedding} />
      <h3 className="config-section-title">自动提取文档元数据</h3>
      <div className="form-two-columns">
        <Form.Item
          label="构建后自动提取"
          name={["parsing", "autoMetadata"]}
          valuePropName="checked"
          extra="识别日期、版本、主体信息与责任人，生成关键词和可能回答的问题，并保留原文依据。"
        >
          <Switch aria-label="构建后自动提取元数据" />
        </Form.Item>
        <Form.Item label="增强失败时使用原文" name={["parsing","metadataFallback"]} valuePropName="checked" extra="保留原文索引并标记增强失败；关闭时维持构建失败，不发布不完整结果。"><Switch disabled={!autoMetadata}/></Form.Item>
        <Form.Item
          label="元数据提取模型"
          name={["parsing", "metadataModel"]}
          extra="模型 ID 为空时使用默认 LLM；提取结果可在文档元数据中复核。"
        >
          <Select
            aria-label="元数据提取模型"
            disabled={!autoMetadata}
            options={[
              {
                value: "",
                label: `沿用默认 LLM${defaultModel ? ` · ${defaultModel}` : ""}`,
              },
              ...(state.catalog || [])
                .filter((model) => model.capability === "llm" && model.enabled)
                .map((model) => ({ value: model.model, label: model.model })),
            ]}
          />
        </Form.Item>
      </div>
      <Alert
        type="info"
        showIcon
        title={
          mode === "live"
            ? "自动提取会调用所选 LLM 并产生 Token 用量。缺失信息显示未发现；抽取失败不编造字段，可稍后重试。"
            : "演示模式仅模拟元数据抽取，不调用 LLM；真实模式会产生模型用量。"
        }
      />
    </>
  );
}
