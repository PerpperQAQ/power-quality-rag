import { useEffect, useRef, useState } from "react";
import {
  Alert,
  App as AntApp,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Tag,
} from "antd";
import {
  ApiOutlined,
  CheckCircleFilled,
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  FileSearchOutlined,
  FontSizeOutlined,
  InfoCircleOutlined,
  MessageOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { ProviderHub } from "../ProviderHub";
import { ModelRegistry } from "../ModelRegistry";
import type { Capability, ModelBinding } from "../types";
import { now, uid } from "../demo";
import { DemoNote, PageHeading } from "../components";
import { useStore } from "../store-context";
import { modelInfo, modelOptions } from "../model-catalog";

const icons = {
  llm: MessageOutlined,
  embedding: ApiOutlined,
  rerank: ThunderboltOutlined,
  ocr: FontSizeOutlined,
  vision: EyeOutlined,
};

export function Models() {
  const { state, update, mode, testModel, flush } = useStore();
  const { message } = AntApp.useApp();
  const [editing, setEditing] = useState<ModelBinding | null>(null);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState<Capability[]>([]);
  const [scenario, setScenario] = useState("success");
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  const test = (binding: ModelBinding, outcome = scenario) => {
    if (loading.includes(binding.capability)) return;
    setLoading((p) => [...p, binding.capability]);
    if (mode === "live") {
      void testModel(binding.capability)
        .then(() =>
          message.info(`${binding.label}连接测试已完成，请查看结果状态`),
        )
        .catch((error: Error) => message.error(error.message))
        .finally(() =>
          setLoading((p) => p.filter((x) => x !== binding.capability)),
        );
      return;
    }
    const timer = setTimeout(() => {
      setLoading((p) => p.filter((x) => x !== binding.capability));
      const failed = outcome !== "success";
      update((p) => ({
        ...p,
        models: p.models.map((m) =>
          m.capability === binding.capability
            ? { ...m, status: failed ? "failed" : "ready", lastTest: now() }
            : m,
        ),
        logs: [
          {
            id: uid("model-test"),
            kind: "model",
            query: `${binding.label}连接测试`,
            assistantName: "模型服务",
            model: binding.model,
            status: failed ? "failed" : "completed",
            inputTokens: failed ? null : 12,
            outputTokens: failed ? null : 8,
            tokens: failed ? null : 20,
            duration: 0.9,
            createdAt: now(),
            stages: [
              {
                name: "能力连接测试",
                ms: 900,
                status: failed ? "failed" : "ok",
              },
            ],
            citations: [],
            error: failed
              ? outcome === "auth"
                ? "演示鉴权失败：密钥无效或模型权限不足。"
                : "演示请求超时：请检查地域、Endpoint 与网络。"
              : undefined,
            configVersion: 1,
          },
          ...p.logs,
        ],
      }));
      if (failed)
        message.error(
          outcome === "auth"
            ? "模拟鉴权失败，请检查配置后重试"
            : "模拟连接超时，可调整情景后重试",
        );
      else message.success(`${binding.label}演示测试通过，未发起真实 API 调用`);
    }, 900);
    timers.current.push(timer);
  };
  const edit = (binding: ModelBinding) => {
    setEditing(binding);
    setScenario("success");
    form.setFieldsValue({
      model: binding.model,
      region: "beijing",
      endpoint: "",
      demoCredential: "",
    });
  };
  const save = () =>
    form
      .validateFields()
      .then(async (values) => {
        if (!editing) return;
        update((p) => ({
          ...p,
          catalog: p.catalog?.some(
            (m) =>
              m.capability === editing.capability &&
              m.model === values.model.trim(),
          )
            ? p.catalog
            : [
                ...(p.catalog || []),
                {
                  id: uid("model"),
                  model: values.model.trim(),
                  capability: editing.capability,
                  enabled: true,
                },
              ],
          models: p.models.map((m) =>
            m.capability === editing.capability
              ? { ...m, model: values.model.trim(), status: "unconfigured", lastTest: undefined, lastError: undefined }
              : m,
          ),
        }));
        await flush();
        setEditing(null);
        form.resetFields();
        message.success(
          mode === "live"
            ? "模型名称变更已提交，请测试真实连接"
            : "已保存模型名称，请进行演示测试；凭证未保存",
        );
      })
      .catch((error) => {
        if (error instanceof Error) message.error(error.message);
      });
  const ready = state.models.filter((m) => m.status === "ready").length;
  return (
    <>
      {mode === "live" && <ProviderHub/>}
      <PageHeading
        eyebrow="MODEL SERVICES"
        title="模型服务"
        description="一次配置，连接知识处理与在线问答需要的每一种能力。"
        actions={
          <Tag
            className="header-neutral-tag"
            icon={<SafetyCertificateOutlined aria-hidden="true" />}
          >
            {mode === "live"
              ? state.provider?.secretSaved
                ? "服务端已保存凭证"
                : "尚未配置凭证"
              : "演示模式不保存凭证"}
          </Tag>
        }
      />
      <section className="provider-banner">
        <div className="provider-logo">
          通义<span>QWEN</span>
        </div>
        <div className="provider-description">
          <div>
            <h2>阿里云百炼 · 通义模型</h2>
            <Tag variant="filled">中国内地地域</Tag>
          </div>
          <p>
            {state.provider?.name || "通义 · 中国内地"} ·
            为知识处理与问答提供模型能力。
          </p>
        </div>
        <div className="provider-state">
          <span
            className={`status-dot ${ready === 5 ? "success" : "warning"}`}
          />
          <strong>
            {ready} / 5 项{mode === "live" ? "测试就绪" : "演示就绪"}
          </strong>
        </div>
      </section>
      <div className="section-heading model-section-heading">
        <div>
          <h2>默认能力配置</h2>
          <p>不同能力可以共用服务商和密钥，但需要分别验证。</p>
        </div>
        {mode === "demo" && (
          <Select
            aria-label="模型测试情景"
            value={scenario}
            onChange={setScenario}
            style={{ width: 195 }}
            options={[
              { value: "success", label: "演示情景：测试成功" },
              { value: "auth", label: "演示情景：鉴权失败" },
              { value: "timeout", label: "演示情景：请求超时" },
            ]}
          />
        )}
      </div>
      <div className="model-list">
        {state.models.map((binding) => {
          const Icon = icons[binding.capability];
          const isLoading = loading.includes(binding.capability);
          return (
            <article className="model-row" key={binding.capability}>
              <div className={`model-capability-icon ${binding.capability}`}>
                <Icon aria-hidden="true" />
              </div>
              <div className="model-row-description">
                <h3>
                  {binding.label}
                  <span>
                    {binding.capability === "vision" ||
                    binding.capability === "ocr"
                      ? "文档理解"
                      : "核心能力"}
                  </span>
                </h3>
                <p>{binding.description}</p>
              </div>
              <div className="model-identifier">
                <Select
                  aria-label={`默认${binding.label}`}
                  value={binding.model}
                  style={{ width: "100%", minWidth: 225 }}
                  showSearch
                  optionFilterProp="label"
                  popupMatchSelectWidth={400}
                  options={modelOptions(state.catalog || [], binding.capability)}
                  onChange={(model) =>
                    update((p) => ({
                      ...p,
                      models: p.models.map((m) =>
                        m.capability === binding.capability
                          ? { ...m, model, status: "unconfigured", lastTest: undefined, lastError: undefined }
                          : m,
                      ),
                    }))
                  }
                />

                <small>
                  {binding.capability === "embedding"
                    ? "构建时记录向量维度"
                    : binding.capability === "vision"
                      ? "可复用支持图像输入的 LLM"
                      : binding.capability === "rerank"
                        ? "DashScope 原生重排协议"
                        : binding.capability === "ocr"
                          ? "云端 API · 无本地模型"
                          : "流式输出 · 带引用回答"}
                </small>
                {modelInfo(binding.model)?.legacy && <small style={{color: "#ad6800"}}>历史视觉模型，新接入建议选择 qwen3.6-flash 或 qwen3-vl-plus。</small>}
                {binding.status === "failed" && <small style={{color: "#cf1322"}}>{binding.lastError || state.logs.find((l) => l.kind === "model" && l.model === binding.model && l.status === "failed")?.error || "请查看日志中的模型连接失败原因。"}</small>}
              </div>
              <div className="model-state">
                {isLoading ? (
                  <Tag color="processing" variant="filled">
                    测试中
                  </Tag>
                ) : binding.status === "ready" ? (
                  <span className="status-label ready">
                    <CheckCircleFilled aria-hidden="true" />
                    {mode === "live" ? "连接通过" : "演示通过"}
                  </span>
                ) : binding.status === "failed" ? (
                  <Tag color="error" variant="filled">
                    测试失败
                  </Tag>
                ) : (
                  <Tag variant="filled">待测试</Tag>
                )}
              </div>
              <div className="model-actions">
                <Button
                  icon={<ReloadOutlined aria-hidden="true" />}
                  loading={isLoading}
                  onClick={() => test(binding)}
                >
                  测试连接
                </Button>
                <Button
                  type="text"
                  icon={<EditOutlined aria-hidden="true" />}
                  aria-label={`配置${binding.label}`}
                  onClick={() => edit(binding)}
                />
              </div>
            </article>
          );
        })}
      </div>
      <ModelRegistry />
      <div className="model-notes">
        <section className="panel">
          <SafetyCertificateOutlined aria-hidden="true" />
          <h3>密钥只属于服务端</h3>
          <p>
            {mode === "live"
              ? "在服务连接中配置 API Key，凭证由服务端保存，浏览器只显示配置状态。"
              : "原型不需要真实 API Key，也不会保存输入的凭证。切换真实后端模式后由后端保存。"}
          </p>
        </section>
        <section className="panel">
          <FileSearchOutlined aria-hidden="true" />
          <h3>用量与调用可追踪</h3>
          <p>
            对话、Embedding、OCR
            与重排分别记录用量和耗时，失败与取消也会保留。缺失用量明确标记未知。
          </p>
        </section>
        <section className="panel">
          <CheckCircleOutlined aria-hidden="true" />
          <h3>先验证，再用于知识构建</h3>
          <p>
            模型测试相互独立。某一项通过，不代表其他能力可用；已有知识库更换
            Embedding 需要重新构建索引。
          </p>
        </section>
      </div>
      <DemoNote />
      <Modal
        title={`配置${editing?.label || "模型"}`}
        open={!!editing}
        onCancel={() => {
          setEditing(null);
          form.resetFields();
        }}
        onOk={save}
        okText={mode === "live" ? "保存模型配置" : "保存演示配置"}
        cancelText="取消"
        width={600}
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          title={
            mode === "live"
              ? "在此设置能力使用的模型 ID，API Key 统一在服务连接中配置。"
              : "请勿输入真实密钥。当前只保存模型名称与演示状态，不保存凭证或调用云端。"
          }
        />
        <Form form={form} layout="vertical" className="model-form">
          <Form.Item
            label="模型名称 / Model ID"
            name="model"
            rules={[
              { required: true, whitespace: true, message: "请输入模型名称" },
            ]}
          >
            <Input maxLength={100} />
          </Form.Item>
          {mode === "demo" && (
            <Form.Item label="演示凭证（可留空）" name="demoCredential">
              <Input.Password
                placeholder="无需真实 API Key"
                autoComplete="off"
              />
            </Form.Item>
          )}
        </Form>
        <div className="import-notice">
          <InfoCircleOutlined aria-hidden="true" />
          服务配置与五类模型能力分开，后续可复用同一连接。
        </div>
      </Modal>
    </>
  );
}
