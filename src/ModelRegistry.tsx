import { useState } from "react";
import {
  Alert,
  App,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
} from "antd";
import { PlusOutlined, SettingOutlined } from "@ant-design/icons";
import { useStore } from "./store-context";
import type { Capability } from "./types";
import { uid } from "./demo";
import { CATALOG_VERSION, modelInfo, TONGYI_MODELS } from "./model-catalog";

const labels: Record<Capability, string> = {
  llm: "对话 LLM",
  embedding: "Embedding",
  rerank: "Rerank",
  ocr: "云端 OCR",
  vision: "视觉理解 VLM",
};
export function ModelRegistry() {
  const { state, update, mode, saveSecret, flush } = useStore();
  const { message } = App.useApp();
  const [adding, setAdding] = useState(false);
  const [connection, setConnection] = useState(false);
  const [savingConnection, setSavingConnection] = useState(false);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [form] = Form.useForm();
  const [providerForm] = Form.useForm();
  const catalog = state.catalog || [];
  const used = (model: string, capability: Capability) =>
    state.models.some(
      (m) => m.capability === capability && m.model === model,
    ) ||
    (capability === "embedding" &&
      state.knowledgeBases.some((k) => k.embedding === model)) ||
    (capability === "llm" &&
      (state.assistants.some((a) => a.model === model) ||
        state.knowledgeBases.some(
          (kb) => kb.parsing?.metadataModel === model,
        ) ||
        state.documents.some((doc) => doc.parsing?.metadataModel === model))) ||
    (capability === "rerank" &&
      state.assistants.some(
        (a) => a.useRerank && a.retrieval?.rerankModel === model,
      ));
  return (
    <section className="panel registry-panel">
      <div className="section-heading">
        <div>
          <h2>通义模型目录</h2>
          <p className="muted">
            已按 RAGFlow 千问页面补齐 {TONGYI_MODELS.length} 个相关模型，支持搜索、能力筛选与自定义添加。
          </p>
        </div>
        <Space wrap>
          <Button
            icon={<SettingOutlined aria-hidden="true" />}
            onClick={() => {
              providerForm.resetFields();
              providerForm.setFieldsValue(state.provider);
              setConnection(true);
            }}
          >
            服务连接
          </Button>
          <Button
            icon={<PlusOutlined aria-hidden="true" />}
            onClick={() => {
              form.resetFields();
              form.setFieldsValue({ capability: "llm" });
              setAdding(true);
            }}
          >
            添加模型
          </Button>
        </Space>
      </div>
      <div className="registry-toolbar">
        <Input
          aria-label="搜索已添加模型"
          placeholder="搜索模型，如 qwen3、vl、ocr"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
        />
        <Select
          aria-label="筛选模型能力"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "全部能力" },
            ...Object.entries(labels).map(([value, label]) => ({
              value,
              label,
            })),
          ]}
        />
      </div>
      <Table
        rowKey="id"
        scroll={{ x: 1000 }}
        pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10,20,50], showTotal: (total) => `共 ${total} 个能力选项` }}
        dataSource={catalog.filter(
          (m) =>
            (filter === "all" || m.capability === filter) &&
            m.model.toLowerCase().includes(search.toLowerCase().trim()),
        )}
        columns={[
          { title: "模型名称", render: (_, m) => <><span>{m.model}</span>{modelInfo(m.model)?.recommendedFor?.includes(m.capability) && <Tag color="blue" style={{marginLeft:8}}>推荐</Tag>}{modelInfo(m.model)?.legacy && <Tag color="gold" style={{marginLeft:8}}>历史版本</Tag>}</> },
          {
            title: "类型 / 调用说明",
            render: (_, m) => {
              const info = modelInfo(m.model);
              return <span className="muted small-text">{info?.disabledReason || (info ? `${info.group}${info.thinking === "always" ? " · 深度思考" : ""}${info.streamOnly ? " · 流式调用" : ""}` : "自定义模型")}</span>;
            },
          },
          {
            title: "能力",
            render: (_, m) => <Tag>{labels[m.capability]}</Tag>,
          },
          {
            title: "使用情况",
            render: (_, m) =>
              used(m.model, m.capability) ? (
                <Tag color="blue">使用中</Tag>
              ) : (
                "未绑定"
              ),
          },
          {
            title: "可选状态",
            render: (_, m) => (
              <Switch
                aria-label={`${m.model}可选状态`}
                checked={m.enabled}
                disabled={used(m.model, m.capability) || !!modelInfo(m.model)?.disabledReason}
                onChange={(enabled) =>
                  update((p) => ({
                    ...p,
                    catalog: p.catalog?.map((x) =>
                      x.id === m.id ? { ...x, enabled } : x,
                    ),
                  }))
                }
              />
            ),
          },
        ]}
      />
      <p className="muted small-text">
        目录更新：{CATALOG_VERSION}。可选状态用于管理下拉选项，实际可用性以当前百炼业务空间的连接测试为准。使用中的模型不能停用。
      </p>
      <Modal
        title="添加通义模型"
        open={adding}
        onCancel={() => setAdding(false)}
        okText="添加模型"
        cancelText="取消"
        onOk={() =>
          form
            .validateFields()
            .then((v) => {
              const model = v.model.trim();
              if (
                catalog.some(
                  (m) => m.capability === v.capability && m.model === model,
                )
              ) {
                form.setFields([
                  { name: "model", errors: ["此能力下已存在同名模型"] },
                ]);
                return;
              }
              update((p) => ({
                ...p,
                catalog: [
                  ...(p.catalog || []),
                  {
                    id: uid("model"),
                    model,
                    capability: v.capability,
                    enabled: true,
                  },
                ],
              }));
              setAdding(false);
              message.success(
                mode === "live"
                  ? "模型登记已提交，请测试真实连接后使用"
                  : "模型已添加，可在默认能力或助手中选择；原型未验证云端可用性",
              );
            })
            .catch(() => {})
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="capability"
            label="模型能力"
            rules={[{ required: true }]}
          >
            <Select
              options={Object.entries(labels).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="model"
            label="模型 ID"
            rules={[
              { required: true, whitespace: true, message: "请输入模型 ID" },
            ]}
          >
            <Input
              maxLength={100}
              placeholder="填写你的百炼业务空间可用模型 ID"
            />
          </Form.Item>
        </Form>
        <Alert
          type="info"
          showIcon
          title={
            mode === "live"
              ? "请填写百炼工作空间可用的准确模型 ID，添加后仍需测试连接。"
              : "模型名可自定义。添加成功表示原型登记完成，正式连接与可用性会在后端接入后验证。"
          }
        />
      </Modal>
      <Modal
        title="通义服务连接"
        open={connection}
        onCancel={() => {
          providerForm.resetFields();
          setConnection(false);
        }}
        confirmLoading={savingConnection}
        okText="保存连接信息"
        cancelText="取消"
        onOk={() =>
          providerForm
            .validateFields()
            .then(async (v) => {
              setSavingConnection(true);
              const { apiKey, ...provider } = v;
              update((p) => ({ ...p, provider }));
              try {
                await flush();
                if (mode === "live" && apiKey?.trim())
                  await saveSecret(apiKey.trim());
                setConnection(false);
                message.success(
                  mode === "live"
                    ? "服务连接已保存，请分别测试模型能力"
                    : "演示连接信息已保存，不包含密钥",
                );
              } catch (error) {
                message.error(
                  error instanceof Error ? error.message : "保存失败",
                );
              } finally {
                providerForm.setFieldValue("apiKey", "");
                setSavingConnection(false);
              }
            })
            .catch(() => {})
        }
      >
        <Form form={providerForm} layout="vertical">
          <Form.Item
            name="name"
            label="连接名称"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={40} />
          </Form.Item>
          <Form.Item name="region" label="地域">
            <Select options={[{ value: "北京", label: "中国内地 · 北京" }]} />
          </Form.Item>
          <Form.Item
            name="endpoint"
            label="服务基础地址"
            rules={[
              { required: true },
              {
                pattern: /^https:\/\/[^\s]+$/,
                message: "请输入 HTTPS 服务地址",
              },
            ]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          {mode === "live" && (
            <Form.Item
              name="apiKey"
              label={
                state.provider?.secretSaved
                  ? "API Key（已配置，留空保留）"
                  : "API Key"
              }
              extra="经本机后端保存，浏览器不持久化密钥。更新密钥后请重新测试模型。"
            >
              <Input.Password
                autoComplete="new-password"
                placeholder={
                  state.provider?.secretSaved
                    ? "输入新密钥以替换"
                    : "请输入通义百炼 API Key"
                }
              />
            </Form.Item>
          )}
        </Form>
        <Alert
          type="info"
          showIcon
          title={
            mode === "live"
              ? "文件识别和模型调用会按阿里云账户计费，API Key 仅发往本机后端。"
              : "原型不需要真实 API Key。请切换真实后端模式后配置凭证。"
          }
        />
        <p className="muted small-text">
          当前连接用于所有
          {mode === "live" ? "通义" : "演示"}模型。
        </p>
      </Modal>
    </section>
  );
}
