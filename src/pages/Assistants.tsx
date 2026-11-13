import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  App as AntApp,
  Button,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Switch,
  Tag,
  Tabs,
  Slider,
} from "antd";
import {
  ApartmentOutlined,
  ArrowRightOutlined,
  CommentOutlined,
  EditOutlined,
  EllipsisOutlined,
  PlusOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useStore } from "../store-context";
import type { Assistant } from "../types";
import { isRetrievable, uid } from "../demo";
import { DEFAULT_GENERATION, DEFAULT_RETRIEVAL } from "../configuration";
import { GenerationFields } from "../GenerationFields";
import { metadataFieldNames } from "../metadata";
import { Blank, DemoNote, PageHeading } from "../components";

export function Assistants() {
  const { state, update, mode, retrieveKnowledge, flush } = useStore();
  const { message } = AntApp.useApp();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Assistant | null>(null);
  const [create, setCreate] = useState(false);
  const [form] = Form.useForm();
  const [configTab, setConfigTab] = useState("basic");
  const [testQuery, setTestQuery] = useState("差旅报销需要哪些附件？");
  const [testResult, setTestResult] = useState<string[] | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState("");
  const useRerank = Form.useWatch("useRerank", form);
  const rerankModel =
    state.models.find((m) => m.capability === "rerank")?.model || "";
  const canManage = state.role !== "user";
  const defaultModel =
    state.models.find((m) => m.capability === "llm")?.model || "qwen3.7-plus";
  const open = create || !!editing;
  useEffect(() => {
    if (open) {
      setConfigTab("basic");
      setTestResult(null);
      form.resetFields();
      form.setFieldsValue(
        editing
          ? structuredClone({
              ...editing,
              retrieval: {
                ...DEFAULT_RETRIEVAL,
                rerankModel,
                ...editing.retrieval,
              },
              generation: { ...DEFAULT_GENERATION, ...editing.generation },
            })
          : {
              name: "",
              description: "",
              kbIds: [],
              model: defaultModel,
              greeting: "你好，我会根据所选知识库回答，并为你标注来源。",
              prompt:
                "仅依据知识库回答；找不到足够依据时明确说明，不编造事实。",
              enabled: true,
              topK: 6,
              candidateK: 30,
              useRerank: true,
              retrieval: { ...DEFAULT_RETRIEVAL, rerankModel },
              generation: { ...DEFAULT_GENERATION },
              noEvidenceReply:
                "未在所选知识库中找到足够依据，请调整问题或补充资料。",
              showQuote: true,
              multiTurn: true,
              keywordAnalysis: false,
            },
      );
    }
  }, [open, editing, form, defaultModel, rerankModel]);
  const save = () =>
    form
      .validateFields()
      .then(async (values) => {
        const name = values.name.trim();
        if (values.topK > values.candidateK) {
          setConfigTab("retrieval");
          message.error("返回片段数不能超过候选片段数");
          return;
        }
        if (values.useRerank && !values.retrieval?.rerankModel) {
          setConfigTab("retrieval");
          message.error("请选择重排模型");
          return;
        }
        if (
          state.assistants.some((a) => a.id !== editing?.id && a.name === name)
        ) {
          form.setFields([{ name: "name", errors: ["助手名称已存在"] }]);
          return;
        }
        if (editing)
          update((p) => ({
            ...p,
            assistants: p.assistants.map((a) =>
              a.id === editing.id
                ? { ...a, ...values, name, configVersion: a.configVersion + 1 }
                : a,
            ),
          }));
        else
          update((p) => ({
            ...p,
            assistants: [
              {
                ...values,
                name,
                id: uid("assistant"),
                configVersion: 1,
                retrievalVersion: 1,
                color: "blue",
              },
              ...p.assistants,
            ],
          }));
        await flush();
        setCreate(false);
        setEditing(null);
        message.success(
          editing
            ? "配置已保存，新会话使用更新后的版本"
            : "助手已创建，可以开始对话",
        );
      })
      .catch((error) => {
        if (error instanceof Error) {
          message.error(error.message);
          return;
        }
        setConfigTab("basic");
        message.warning("请补齐助手名称、知识库与必填配置");
      });
  const list = state.assistants.filter(
    (a) =>
      (canManage || a.enabled) && `${a.name}${a.description}`.includes(search),
  );
  return (
    <>
      <PageHeading
        eyebrow="AI ASSISTANTS"
        title="问答助手"
        description="为不同工作场景，配备了解相关知识的专属助手。"
        actions={
          canManage && (
            <Button
              type="primary"
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={() => setCreate(true)}
            >
              创建助手
            </Button>
          )
        }
      />
      <div className="list-toolbar">
        <div className="list-tabs">
          <span className="selected">
            全部助手 <b>{list.length}</b>
          </span>
          <span>知识驱动，回答有据</span>
        </div>
        <Input
          prefix={<SearchOutlined aria-hidden="true" />}
          placeholder="搜索助手"
          aria-label="搜索助手"
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 250 }}
        />
      </div>
      {!list.length ? (
        <Blank
          title={search ? "没有匹配的助手" : "创建第一个问答助手"}
          description="选择知识范围，配置欢迎语，让团队开始提问。"
          action={
            canManage &&
            !search && (
              <Button type="primary" onClick={() => setCreate(true)}>
                创建助手
              </Button>
            )
          }
        />
      ) : (
        <div className="assistant-grid">
          {list.map((a) => {
            const sources = state.knowledgeBases.filter((k) =>
              a.kbIds.includes(k.id),
            );
            const ready = state.documents.some(
              (d) => a.kbIds.includes(d.kbId) && isRetrievable(d),
            );
            return (
              <article className="assistant-card" key={a.id}>
                <div className="assistant-card-top">
                  <span className={`assistant-avatar ${a.color}`}>
                    <ApartmentOutlined aria-hidden="true" />
                  </span>
                  {canManage ? (
                    <Dropdown
                      trigger={["click"]}
                      menu={{
                        items: [
                          {
                            key: "edit",
                            label: "编辑助手",
                            icon: <EditOutlined aria-hidden="true" />,
                          },
                          {
                            key: "toggle",
                            label: a.enabled ? "停用助手" : "启用助手",
                          },
                        ],
                        onClick: ({ key }) =>
                          key === "edit"
                            ? setEditing(a)
                            : update((p) => ({
                                ...p,
                                assistants: p.assistants.map((x) =>
                                  x.id === a.id
                                    ? { ...x, enabled: !x.enabled }
                                    : x,
                                ),
                              })),
                      }}
                    >
                      <Button
                        type="text"
                        aria-label={`${a.name}更多操作`}
                        icon={<EllipsisOutlined aria-hidden="true" />}
                      />
                    </Dropdown>
                  ) : (
                    <Tag color="success" variant="filled">
                      已启用
                    </Tag>
                  )}
                </div>
                <h3>{a.name}</h3>
                <p>{a.description}</p>
                <div className="assistant-sources">
                  <span>知识范围</span>
                  <div>
                    {sources.length ? (
                      sources.map((k) => (
                        <Tag key={k.id} variant="filled">
                          {k.name}
                        </Tag>
                      ))
                    ) : (
                      <Tag color="warning" variant="filled">
                        尚未绑定知识库
                      </Tag>
                    )}
                  </div>
                </div>
                <div className="assistant-model">
                  <span className="status-dot success" />
                  {a.model}
                  <span>配置 v{a.configVersion}</span>
                </div>
                <footer>
                  <span
                    className={`assistant-status ${a.enabled ? "enabled" : ""}`}
                  >
                    {a.enabled ? (ready ? "已启用" : "等待知识就绪") : "已停用"}
                  </span>
                  <Button
                    type={a.enabled ? "primary" : "default"}
                    ghost={a.enabled}
                    disabled={!a.enabled || !ready}
                    icon={<CommentOutlined aria-hidden="true" />}
                    onClick={() => navigate(`/chat?assistant=${a.id}`)}
                  >
                    开始对话
                  </Button>
                </footer>
              </article>
            );
          })}
          {canManage && !search && (
            <button
              className="new-kb-card assistant-create"
              onClick={() => setCreate(true)}
            >
              <span>
                <PlusOutlined aria-hidden="true" />
              </span>
              <strong>创建专属知识助手</strong>
              <p>
                选择知识库，定义回答范围
                <br />
                让知识服务于具体的工作
              </p>
            </button>
          )}
        </div>
      )}
      <div className="assistant-explainer">
        <CommentOutlined aria-hidden="true" />
        <div>
          <h3>同一份知识，可以服务不同的助手</h3>
          <p>
            知识库负责存储与更新资料，助手负责决定知识范围和回答方式。先让资料可检索，再让回答有依据。
          </p>
        </div>
        <Button type="link" onClick={() => navigate("/knowledge")}>
          管理知识库 <ArrowRightOutlined aria-hidden="true" />
        </Button>
      </div>
      <Modal
        title={editing ? "编辑问答助手" : "创建问答助手"}
        open={open && canManage}
        onCancel={() => {
          setCreate(false);
          setEditing(null);
        }}
        onOk={save}
        okText={editing ? "保存配置" : "创建助手"}
        cancelText="取消"
        width={1120}
        className="assistant-config-modal"
        destroyOnHidden
      >
        <div className="assistant-config-workspace">
          <Form
            form={form}
            layout="vertical"
            className="assistant-config-fields"
            onValuesChange={() => setTestResult(null)}
          >
            <Tabs
              activeKey={configTab}
              onChange={setConfigTab}
              items={[
                {
                  key: "basic",
                  label: "基本配置",
                  forceRender: true,
                  children: (
                    <>
                      <Form.Item
                        label="助手名称"
                        name="name"
                        rules={[
                          {
                            required: true,
                            whitespace: true,
                            message: "请输入助手名称",
                          },
                        ]}
                      >
                        <Input
                          maxLength={40}
                          placeholder="例如：员工服务助手"
                        />
                      </Form.Item>
                      <Form.Item label="助手说明" name="description">
                        <Input maxLength={120} />
                      </Form.Item>
                      <Form.Item
                        label="对话模型"
                        name="model"
                        rules={[{ required: true }]}
                      >
                        <Select
                          options={[
                            ...new Set([
                              ...(state.catalog || [])
                                .filter(
                                  (m) => m.capability === "llm" && m.enabled,
                                )
                                .map((m) => m.model),
                              ...(editing ? [editing.model] : []),
                              defaultModel,
                            ]),
                          ].map((model) => ({ value: model, label: model }))}
                        />
                      </Form.Item>
                      <Form.Item
                        label="绑定知识库"
                        name="kbIds"
                        rules={[
                          {
                            required: true,
                            type: "array",
                            min: 1,
                            message: "请至少选择一个知识库",
                          },
                        ]}
                      >
                        <Select
                          mode="multiple"
                          aria-label="绑定知识库"
                          options={state.knowledgeBases.map((k) => ({
                            value: k.id,
                            label: `${k.name} · ${state.documents.filter((d) => d.kbId === k.id && isRetrievable(d)).length} 份可用`,
                          }))}
                        />
                      </Form.Item>
                      <Form.Item
                        label="开场欢迎语"
                        name="greeting"
                        rules={[{ required: true, whitespace: true }]}
                      >
                        <Input.TextArea rows={3} maxLength={300} />
                      </Form.Item>
                      <Form.Item
                        label="启用助手"
                        name="enabled"
                        valuePropName="checked"
                      >
                        <Switch />
                      </Form.Item>
                    </>
                  ),
                },
                {
                  key: "prompt",
                  label: "回答规则",
                  forceRender: true,
                  children: (
                    <>
                      <Form.Item
                        label="回答规则 / 系统提示词"
                        name="prompt"
                        rules={[{ required: true, whitespace: true }]}
                        extra={
                          mode === "live"
                            ? "问答会将检索证据连同此规则发送给配置的 LLM。"
                            : "正式问答会将检索证据连同规则发送给 LLM；原型仅演示配置与证据内容。"
                        }
                      >
                        <Input.TextArea rows={8} maxLength={4000} />
                      </Form.Item>
                      <Form.Item
                        label="空检索回复"
                        name="noEvidenceReply"
                        rules={[{ required: true, whitespace: true }]}
                      >
                        <Input.TextArea rows={3} maxLength={500} />
                      </Form.Item>
                      <Form.Item
                        label="展示来源引用"
                        name="showQuote"
                        valuePropName="checked"
                      >
                        <Switch />
                      </Form.Item>
                      <Form.Item
                        label="多轮问题优化"
                        name="multiTurn"
                        valuePropName="checked"
                        extra={
                          mode === "live"
                            ? "结合历史上下文理解后续追问。"
                            : "演示中将指代性追问与上一轮问题合并检索。"
                        }
                      >
                        <Switch />
                      </Form.Item>
                    </>
                  ),
                },
                {
                  key: "retrieval",
                  label: "知识检索",
                  forceRender: true,
                  children: (
                    <>
                      <div className="form-two-columns">
                        <Form.Item
                          label="相似度阈值"
                          name={["retrieval", "threshold"]}
                        >
                          <InputNumber min={0} max={1} step={0.05} />
                        </Form.Item>
                        <Form.Item label="候选片段数" name="candidateK">
                          <InputNumber min={1} max={60} />
                        </Form.Item>
                      </div>
                      <Form.Item
                        label="向量相似度权重"
                        name={["retrieval", "vectorWeight"]}
                        extra="剩余权重用于全文检索。"
                      >
                        <Slider min={0} max={1} step={0.05} />
                      </Form.Item>
                      <Form.Item
                        label="返回上下文片段数"
                        name="topK"
                        extra="检索后供回答参考的片段上限。"
                      >
                        <InputNumber min={1} max={12} />
                      </Form.Item>
                      <Form.Item
                        label="启用 Rerank 重排"
                        name="useRerank"
                        valuePropName="checked"
                      >
                        <Switch />
                      </Form.Item>
                      <Form.Item
                        label="重排模型"
                        name={["retrieval", "rerankModel"]}
                      >
                        <Select
                          disabled={!useRerank}
                          options={(state.catalog || [])
                            .filter(
                              (m) => m.capability === "rerank" && m.enabled,
                            )
                            .map((m) => ({ value: m.model, label: m.model }))}
                        />
                      </Form.Item>
                      <Form.Item
                        label="文档范围"
                        name={["retrieval", "fileIds"]}
                        extra="留空时检索所绑定知识库中的全部可用文档。"
                      >
                        <Select
                          mode="multiple"
                          options={state.documents.map((d) => ({
                            value: d.id,
                            label: d.name,
                          }))}
                        />
                      </Form.Item>
                      <div className="form-two-columns">
                        <Form.Item
                          label="元数据字段"
                          name={["retrieval", "metadataKey"]}
                        >
                          <Select
                            allowClear
                            options={metadataFieldNames(state.documents).map(
                              (v) => ({
                                value: v,
                                label: v,
                              }),
                            )}
                          />
                        </Form.Item>
                        <Form.Item
                          label="精确匹配值"
                          name={["retrieval", "metadataValue"]}
                        >
                          <Input />
                        </Form.Item>
                      </div>
                    </>
                  ),
                },
                {
                  key: "generation",
                  label: "生成参数",
                  forceRender: true,
                  children: <GenerationFields />,
                },
              ]}
            />
          </Form>
          <aside className="draft-test-panel">
            <Tag color="blue">草稿检索预览</Tag>
            <h3>边配置，边检查知识范围</h3>
            <p className="muted">
              {mode === "live"
                ? "使用当前未保存配置检索实际知识分块，不调用 LLM 生成回答。"
                : "使用当前未保存配置检索示例分块，不生成真实模型回答。"}
            </p>
            <Input.TextArea
              aria-label="助手测试问题"
              rows={3}
              value={testQuery}
              onChange={(e) => {
                setTestQuery(e.target.value);
                setTestResult(null);
              }}
            />
            <Button
              block
              disabled={!testQuery.trim()}
              loading={testing}
              onClick={async () => {
                const v = form.getFieldsValue(true);
                setTesting(true);
                setTestError("");
                try {
                  const results = await retrieveKnowledge({
                    query: testQuery,
                    kbIds: v.kbIds || [],
                    topK: v.topK,
                    retrieval: { ...DEFAULT_RETRIEVAL, ...v.retrieval },
                    candidateK: v.candidateK,
                    useRerank: v.useRerank,
                  });
                  setTestResult(
                    results.map(
                      (c) => `${c.name} · ${c.location}\n${c.content}`,
                    ),
                  );
                } catch (error) {
                  setTestError(
                    error instanceof Error ? error.message : "检索失败",
                  );
                  setTestResult(null);
                } finally {
                  setTesting(false);
                }
              }}
            >
              测试草稿检索
            </Button>
            {testError && <Alert type="error" showIcon title={testError} />}
            {testResult === null ? (
              <div className="draft-empty">
                选择知识库后，输入工作中会遇到的问题。
              </div>
            ) : testResult.length ? (
              testResult.map((r, i) => (
                <div key={i} className="draft-result">
                  {r}
                </div>
              ))
            ) : (
              <Alert
                type="warning"
                showIcon
                title={
                  mode === "live"
                    ? "当前配置没有命中知识分块"
                    : "当前配置没有命中示例分块"
                }
                description="检查知识范围、阈值及文档是否启用。"
              />
            )}
          </aside>
        </div>
        <Alert
          type="info"
          showIcon
          title="修改知识范围与模型后，新会话生效；已有会话保留配置快照。"
        />
        <DemoNote compact />
      </Modal>
    </>
  );
}
