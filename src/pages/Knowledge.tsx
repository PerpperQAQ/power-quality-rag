import { FeishuImport } from "../FeishuImport";
import { useEffect, useRef, useState } from "react";
import { api } from "../live-api";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Alert,
  App as AntApp,
  Button,
  Checkbox,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Segmented,
  Select,
  Slider,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
} from "antd";
import type { TableColumnsType } from "antd";
import {
  AppstoreOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  EllipsisOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  UnorderedListOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { Citation, KnowledgeBase, KnowledgeDoc } from "../types";
import { isRetrievable, now, sizeText, timeText, uid } from "../demo";
import {
  Blank,
  DemoNote,
  DocStatusTag,
  FileIcon,
  FileName,
  KBIcon,
  PageHeading,
  SourceDrawer,
} from "../components";
import { useStore } from "../store-context";

import { DocumentWorkspace } from "../DocumentWorkspace";
import { DEFAULT_PARSING, DEFAULT_RETRIEVAL } from "../configuration";
import { ParsingFields } from "../ParsingFields";
import { CHUNKING_METHODS, CHUNKING_LABELS } from "../chunking-methods";
import { metadataFieldNames } from "../metadata";

const COLORS = ["blue", "green", "orange", "purple"];

export function KnowledgeList() {
  const { state, update, mode, flush } = useStore();
  const { message, modal } = AntApp.useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [layout, setLayout] = useState("grid");
  const [edit, setEdit] = useState<KnowledgeBase | null>(null);
  const [create, setCreate] = useState(false);
  const canManage = state.role !== "user";
  const embeddingModel =
    state.models.find((m) => m.capability === "embedding")?.model ||
    "text-embedding-v4";
  const [form] = Form.useForm();
  const showForm = create || params.get("create") === "1" || !!edit;
  useEffect(() => {
    if (showForm)
      form.setFieldsValue(
        edit || {
          name: "",
          description: "",
          embedding: embeddingModel,
          parsing: {...DEFAULT_PARSING},
          color: "blue",
        },
      );
  }, [showForm, edit, form, embeddingModel]);
  const close = () => {
    setCreate(false);
    setEdit(null);
    setParams({});
  };
  const filtered = state.knowledgeBases.filter(
    (k) =>
      k.name.toLowerCase().includes(search.toLowerCase()) ||
      k.description.includes(search),
  );
  const removeKB = (k: KnowledgeBase) =>
    modal.confirm({
      title: `移除“${k.name}”？`,
      content: "库内文档也将移除，助手将失去此知识来源。历史对话引用快照保留。",
      okText: "移除知识库",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => {
        update((p) => ({
          ...p,
          knowledgeBases: p.knowledgeBases.filter((x) => x.id !== k.id),
          documents:
            mode === "live"
              ? p.documents
              : p.documents.filter((d) => d.kbId !== k.id),
          assistants:
            mode === "live"
              ? p.assistants
              : p.assistants.map((a) => ({
                  ...a,
                  kbIds: a.kbIds.filter((id) => id !== k.id),
                })),
        }));
        message.success(
          mode === "live" ? "删除知识库已提交" : "已移除演示知识库",
        );
      },
    });
  const save = () =>
    form
      .validateFields()
      .then(async (values) => {
        const name = values.name.trim();
        if (
          state.knowledgeBases.some((k) => k.id !== edit?.id && k.name === name)
        ) {
          form.setFields([
            { name: "name", errors: ["已存在同名知识库，请换一个名称"] },
          ]);
          return;
        }
        if (edit)
          update((p) => ({
            ...p,
            knowledgeBases: p.knowledgeBases.map((k) =>
              k.id === edit.id
                ? { ...k, ...values, name, updatedAt: now() }
                : k,
            ),
          }));
        else {
          const kb: KnowledgeBase = {
            ...values,
            name,
            id: uid("kb"),
            icon: "folder",
            parsing: { ...DEFAULT_PARSING, ...values.parsing },
            parsingVersion: 1,
            chunkSize: 600,
            overlap: 100,
            updatedAt: now(),
          };
          update((p) => ({ ...p, knowledgeBases: [kb, ...p.knowledgeBases] }));
          await flush();
          close();
          navigate(`/knowledge/${kb.id}`);
          message.success("知识库已创建，导入文件开始构建");
          return;
        }
        await flush();
        close();
        message.success("知识库信息已保存");
      })
      .catch((error) => {
        if (error instanceof Error) message.error(error.message);
      });
  const columns: TableColumnsType<KnowledgeBase> = [
    {
      title: "知识库名称",
      dataIndex: "name",
      render: (_, k) => (
        <button
          className="table-kb-name"
          onClick={() => navigate(`/knowledge/${k.id}`)}
        >
          <KBIcon color={k.color} icon={k.icon} small />
          <strong>{k.name}</strong>
        </button>
      ),
    },
    { title: "说明", dataIndex: "description", ellipsis: true },
    {
      title: "文档",
      render: (_, k) =>
        `${state.documents.filter((d) => d.kbId === k.id).length} 份`,
    },
    { title: "最近更新", render: (_, k) => timeText(k.updatedAt) },
    {
      title: "操作",
      render: (_, k) => (
        <Button type="link" onClick={() => navigate(`/knowledge/${k.id}`)}>
          进入知识库
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeading
        eyebrow="KNOWLEDGE BASES"
        title="知识库"
        description="把分散的资料，整理成团队共享的知识资产。"
        actions={
          canManage && (
            <Button
              type="primary"
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={() => setCreate(true)}
            >
              新建知识库
            </Button>
          )
        }
      />
      <div className="list-toolbar">
        <div className="list-tabs">
          <span className="selected">
            全部知识库 <b>{state.knowledgeBases.length}</b>
          </span>
          <span>
            <TeamLabel />
          </span>
        </div>
        <Space>
          <Input
            allowClear
            prefix={<SearchOutlined aria-hidden="true" />}
            placeholder="搜索知识库"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 240 }}
            aria-label="搜索知识库"
          />
          <Segmented
            aria-label="知识库视图"
            value={layout}
            onChange={(v) => setLayout(String(v))}
            options={[
              {
                value: "grid",
                label: "卡片",
                icon: <AppstoreOutlined aria-hidden="true" />,
              },
              {
                value: "list",
                label: "列表",
                icon: <UnorderedListOutlined aria-hidden="true" />,
              },
            ]}
          />
        </Space>
      </div>
      {!filtered.length ? (
        <Blank
          title={search ? "没有找到相关知识库" : "从第一个知识库开始"}
          description={
            search
              ? "试试其他名称或清空搜索条件。"
              : "导入文档，让团队的知识开始积累。"
          }
          action={
            canManage &&
            !search && (
              <Button type="primary" onClick={() => setCreate(true)}>
                新建知识库
              </Button>
            )
          }
        />
      ) : layout === "list" ? (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={filtered}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
        />
      ) : (
        <div className="kb-grid full-kbs">
          {filtered.map((k) => {
            const docs = state.documents.filter((d) => d.kbId === k.id);
            const ready = docs.filter((d) => isRetrievable(d)).length;
            const issues = docs.filter(
              (d) => d.status === "failed" || d.status === "review",
            ).length;
            return (
              <article className="kb-card" key={k.id}>
                <div className="kb-card-top">
                  <button
                    className="icon-button"
                    aria-label={`进入${k.name}`}
                    onClick={() => navigate(`/knowledge/${k.id}`)}
                  >
                    <KBIcon color={k.color} icon={k.icon} />
                  </button>
                  {canManage ? (
                    <Dropdown
                      menu={{
                        items: [
                          { key: "edit", label: "编辑信息" },
                          { key: "delete", label: "移除知识库", danger: true },
                        ],
                        onClick: ({ key }) =>
                          key === "edit" ? setEdit(k) : removeKB(k),
                      }}
                      trigger={["click"]}
                    >
                      <Button
                        type="text"
                        aria-label={`${k.name}更多操作`}
                        icon={<EllipsisOutlined aria-hidden="true" />}
                      />
                    </Dropdown>
                  ) : (
                    <Tag variant="filled">共享</Tag>
                  )}
                </div>
                <button
                  className="kb-card-link"
                  onClick={() => navigate(`/knowledge/${k.id}`)}
                >
                  <h3>{k.name}</h3>
                  <p>{k.description}</p>
                </button>
                <div className="kb-card-meta">
                  <span>
                    <FileTextOutlined aria-hidden="true" />
                    {docs.length} 份文档
                  </span>
                  <span>{docs.reduce((n, d) => n + d.chunks, 0)} 个分块</span>
                </div>
                <div className="kb-readiness">
                  <span>
                    {docs.length ? `${ready} 份已就绪` : "尚未导入文档"}
                  </span>
                  {issues > 0 && (
                    <span className="warning-text">{issues} 份需处理</span>
                  )}
                </div>
                <Progress
                  percent={
                    docs.length ? Math.round((ready / docs.length) * 100) : 0
                  }
                  showInfo={false}
                  strokeColor="#789bf4"
                  size="small"
                />
                <div className="kb-card-footer">
                  <span>{timeText(k.updatedAt)}</span>
                  <button onClick={() => navigate(`/knowledge/${k.id}`)}>
                    进入知识库 <ArrowRightOutlined aria-hidden="true" />
                  </button>
                </div>
              </article>
            );
          })}
          {canManage && !search && (
            <button className="new-kb-card" onClick={() => setCreate(true)}>
              <span>
                <PlusOutlined aria-hidden="true" />
              </span>
              <strong>创建新的知识库</strong>
              <p>
                为一个团队、项目或主题
                <br />
                建立有序的知识空间
              </p>
            </button>
          )}
        </div>
      )}
      <div className="bottom-tip">
        <InfoCircleOutlined aria-hidden="true" />{" "}
        知识库之间相互独立，你可以在问答助手中组合多个知识来源。
      </div>
      <Modal
        open={showForm && canManage}
        title={edit ? "编辑知识库" : "新建知识库"}
        onCancel={close}
        onOk={save}
        okText={edit ? "保存修改" : "创建知识库"}
        cancelText="取消"
        destroyOnHidden
      >
        <p className="modal-description">
          用一个清晰的名称，描述这份知识的范围。
        </p>
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="知识库名称"
            rules={[
              { required: true, whitespace: true, message: "请输入知识库名称" },
              { max: 40 },
            ]}
          >
            <Input placeholder="例如：员工制度库" maxLength={40} showCount />
          </Form.Item>
          <Form.Item name="description" label="知识库说明">
            <Input.TextArea
              placeholder="说明收录哪些资料、适合回答什么问题"
              rows={3}
              maxLength={160}
              showCount
            />
          </Form.Item>
          <Form.Item name="embedding" label="Embedding 模型">
            <Select
              disabled={
                !!edit &&
                state.documents.some(
                  (d) => d.kbId === edit.id && d.chunks > 0,
                )
              }
              options={[
                ...new Set([
                  ...(state.catalog || [])
                    .filter((m) => m.capability === "embedding" && m.enabled)
                    .map((m) => m.model),
                  embeddingModel,
                  ...(edit ? [edit.embedding] : []),
                ]),
              ].map((model) => ({
                value: model,
                label: `${model}${model === embeddingModel ? " · 当前默认" : " · 已绑定"}`,
              }))}
            />
          </Form.Item>
          {!edit && <Form.Item name={["parsing", "strategy"]} label="默认切分方法" extra="创建后可在知识库设置中调整长度、重叠并预览；单份文档也可覆盖默认方法。">
            <Select showSearch optionFilterProp="label" options={CHUNKING_METHODS.map(({value,label}) => ({value,label}))} />
          </Form.Item>}
          {!edit && (
            <Alert
              type="info"
              showIcon
              title={
                mode === "live"
                  ? "新知识库默认启用 LLM 自动提取日期、版本、主体信息、责任人、关键词及关联问题，会产生模型用量；可在知识库设置中关闭或选择模型。"
                  : "新知识库默认启用元数据抽取演示；真实模式会调用 LLM，可在知识库设置中调整。"
              }
            />
          )}
          <Form.Item name="color" label="标识颜色">
            <Select
              options={COLORS.map((c) => ({
                value: c,
                label: {
                  blue: "知识蓝",
                  green: "松石绿",
                  orange: "琥珀橙",
                  purple: "鸢尾紫",
                }[c],
              }))}
            />
          </Form.Item>
        </Form>
        <DemoNote compact />
      </Modal>
    </>
  );
}
function TeamLabel() {
  return <>共享工作空间</>;
}

export function KnowledgeDetail() {
  const { id } = useParams();
  const { state, update, retryDoc, cancelDoc, mode } = useStore();
  const { message, modal } = AntApp.useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState("documents");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState(params.get("status") || "all");
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const kb = state.knowledgeBases.find((k) => k.id === id);
  const canManage = state.role !== "user";
  const docs = state.documents.filter((d) => d.kbId === id);
  const dimensions = [...new Set(docs.filter(d => d.chunks > 0).map(d => d.embeddingDimension).filter((value): value is number => typeof value === "number" && value > 0))];
  const filtered = docs.filter(
    (d) =>
      d.name.toLowerCase().includes(search.toLowerCase()) &&
      (status === "all" ||
        (status === "attention"
          ? ["failed", "review"].includes(d.status)
          : status === "disabled"
            ? !d.enabled
            : d.status === status)) &&
      (canManage || isRetrievable(d)),
  );
  const paramDoc = params.get("doc");
  useEffect(() => {
    setSelected([]);
    setSearch("");
    setStatus(params.get("status") || "all");
    setTab(params.get("tab") || "documents");
  }, [id]);
  useEffect(() => {
    if (params.get("tab")) setTab(params.get("tab")!);
  }, [params]);
  const preview = docs.find(
    (d) => d.id === (previewId || paramDoc) && (canManage || isRetrievable(d)),
  );
  const closePreview = () => {
    setPreviewId(null);
    const next = new URLSearchParams(params);
    next.delete("doc");
    setParams(next, { replace: true });
  };
  if (!kb)
    return (
      <Blank
        title="这个知识库已不存在"
        description="它可能已被移除，请返回知识库列表。"
        action={
          <Button onClick={() => navigate("/knowledge")}>返回知识库</Button>
        }
      />
    );
  const removeDoc = (doc: KnowledgeDoc) =>
    modal.confirm({
      title: `从知识库移除“${doc.name}”？`,
      content: "此文档不再参与新检索，历史对话中的引用快照仍保留。",
      okText: "移除文档",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => {
        update((p) => ({
          ...p,
          documents: p.documents.filter((d) => d.id !== doc.id),
        }));
        message.success(mode === "live" ? "移除文档已提交" : "文档已移除");
      },
    });
  const batch = (enabled: boolean) => {
    update((p) => ({
      ...p,
      documents: p.documents.map((d) =>
        selected.includes(d.id) ? { ...d, enabled } : d,
      ),
    }));
    setSelected([]);
    message.success(
      mode === "live"
        ? "文档启停变更已提交"
        : `已${enabled ? "启用" : "停用"}所选文档`,
    );
  };
  const columns: TableColumnsType<KnowledgeDoc> = [
    {
      title: "文档名称",
      key: "name",
      width: "33%",
      render: (_, d) => <FileName doc={d} onClick={() => setPreviewId(d.id)} />,
    },
    {
      title: "类型",
      dataIndex: "format",
      width: 85,
      render: (value: string) => <span className="format-label">{value}</span>,
    },
    {
      title: "分块",
      dataIndex: "chunks",
      width: 80,
      render: (v: number) => v || "—",
    },
    {
      title: "状态",
      width: 150,
      render: (_, d) => (
        <DocStatusTag status={d.status} progress={d.progress} />
      ),
    },
    {
      title: "最近更新",
      width: 145,
      render: (_, d) => (
        <span className="muted small-text">{timeText(d.updatedAt)}</span>
      ),
    },
    ...(canManage
      ? [
          {
            title: "参与问答",
            width: 85,
            render: (_: unknown, d: KnowledgeDoc) => (
              <Switch
                size="small"
                checked={d.enabled}
                aria-label={`${d.name}参与问答`}
                onChange={(checked) =>
                  update((p) => ({
                    ...p,
                    documents: p.documents.map((x) =>
                      x.id === d.id ? { ...x, enabled: checked } : x,
                    ),
                  }))
                }
              />
            ),
          },
        ]
      : []),
    {
      title: "操作",
      width: 120,
      render: (_, d) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => setPreviewId(d.id)}>
            {d.status === "failed" ? "查看原因" : "查看"}
          </Button>
          {canManage && (
            <Dropdown
              trigger={["click"]}
              menu={{
                items: [
                  {
                    key: "retry",
                    label:
                      d.status === "ready"
                        ? "重新构建"
                        : d.status === "uploaded"
                          ? "开始构建"
                          : "重试构建",
                    icon: <ReloadOutlined aria-hidden="true" />,
                  },
                  ...(d.status === "processing"
                    ? [{ key: "cancel", label: "取消构建" }]
                    : []),
                  {
                    key: "log",
                    label: "查看任务日志",
                    icon: <FileSearchOutlined aria-hidden="true" />,
                  },
                  {
                    key: "delete",
                    label: "移除文档",
                    danger: true,
                    icon: <DeleteOutlined aria-hidden="true" />,
                  },
                ],
                onClick: ({ key }) => {
                  if (key === "retry") {
                    retryDoc(d.id);
                  } else if (key === "cancel") cancelDoc(d.id);
                  else if (key === "log")
                    navigate(
                      `/logs?type=ingestion&search=${encodeURIComponent(d.name)}`,
                    );
                  else removeDoc(d);
                },
              }}
            >
              <Button
                type="text"
                size="small"
                icon={<EllipsisOutlined aria-hidden="true" />}
                aria-label={`${d.name}操作`}
              />
            </Dropdown>
          )}
        </Space>
      ),
    },
  ];
  const ready = docs.filter((d) => isRetrievable(d)).length;
  const issues = docs.filter((d) => ["failed", "review"].includes(d.status));
  return (
    <>
      <button className="back-link" onClick={() => navigate("/knowledge")}>
        <ArrowLeftOutlined aria-hidden="true" />
        返回知识库
      </button>
      <div className="kb-detail-heading">
        <KBIcon color={kb.color} icon={kb.icon} />
        <div>
          <h1>
            {kb.name}
            <Tag variant="filled">共享知识库</Tag>
          </h1>
          <p>{kb.description}</p>
        </div>
        {canManage && (
          <Button
            type="primary"
            icon={<UploadOutlined aria-hidden="true" />}
            onClick={() => setImportOpen(true)}
          >
            导入文件
          </Button>
        )}
      </div>
      {canManage &&
        docs.some(
          (d) =>
            d.status === "ready" &&
            d.indexParsingVersion !== (kb.parsingVersion || 1),
        ) && (
          <Alert
            type="warning"
            showIcon
            title="解析规则已有更新，部分文档仍在使用原索引；重新构建后应用新规则。"
          />
        )}
      <div className="kb-stat-line">
        <span>
          <FileTextOutlined aria-hidden="true" />
          {docs.length} 份文档
        </span>
        <span>
          <CheckCircleOutlined aria-hidden="true" />
          {ready} 份可用于问答
        </span>
        <span>Embedding · {kb.embedding}</span>
        <Tag variant="filled">{dimensions.length ? `索引维度 · ${dimensions.join(" / ")}` : "索引维度尚未记录"}</Tag>
      </div>
      <Tabs
        activeKey={canManage ? tab : "documents"}
        onChange={setTab}
        items={[
          {
            key: "documents",
            label: "知识文档",
            children: (
              <>
                {canManage && issues.length > 0 && (
                  <div className="attention-inline">
                    <InfoCircleOutlined aria-hidden="true" />
                    <span>
                      {issues.length} 份文档需要检查，完成处理后才能用于问答。
                    </span>
                    <button onClick={() => setStatus("attention")}>
                      查看待处理文档
                      <ArrowRightOutlined aria-hidden="true" />
                    </button>
                  </div>
                )}
                <div className="document-toolbar">
                  <Space>
                    <Input
                      prefix={<SearchOutlined aria-hidden="true" />}
                      placeholder="搜索文档名称"
                      aria-label="搜索文档名称"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      allowClear
                      style={{ width: 260 }}
                    />
                    <Select
                      aria-label="筛选文档状态"
                      value={status}
                      onChange={setStatus}
                      style={{ width: 150 }}
                      options={[
                        { value: "all", label: "全部状态" },
                        { value: "uploaded", label: "待构建" },
                        { value: "ready", label: "已就绪" },
                        { value: "processing", label: "构建中" },
                        { value: "attention", label: "需要处理" },
                        { value: "canceled", label: "已取消" },
                        { value: "disabled", label: "已停用" },
                      ]}
                    />
                  </Space>
                  <span className="muted">共 {filtered.length} 份文档</span>
                </div>
                {selected.length > 0 && canManage && (
                  <div className="batch-bar">
                    已选择 {selected.length} 份文档
                    <Button
                      size="small"
                      onClick={() =>
                        modal.confirm({
                          title: "批量构建所选文档？",
                          content: "将使用当前解析规则重建，手工分块会被覆盖。",
                          okText: "确认批量构建",
                          cancelText: "取消",
                          onOk: () => {
                            selected.forEach((id) => retryDoc(String(id)));
                            setSelected([]);
                          },
                        })
                      }
                    >
                      批量构建
                    </Button>
                    <Button size="small" onClick={() => batch(true)}>
                      批量启用
                    </Button>
                    <Button size="small" onClick={() => batch(false)}>
                      批量停用
                    </Button>
                    <Button
                      size="small"
                      type="text"
                      onClick={() => setSelected([])}
                    >
                      取消选择
                    </Button>
                  </div>
                )}
                <Table
                  rowKey="id"
                  columns={columns}
                  dataSource={filtered}
                  rowSelection={
                    canManage
                      ? { selectedRowKeys: selected, onChange: setSelected }
                      : undefined
                  }
                  pagination={{
                    pageSize: 7,
                    showSizeChanger: false,
                    hideOnSinglePage: true,
                    showTotal: (total) => `共 ${total} 份文档`,
                  }}
                  scroll={{ x: 900 }}
                  locale={{
                    emptyText: (
                      <Blank
                        title={
                          docs.length
                            ? "没有匹配的文档"
                            : "知识，从第一份文档开始"
                        }
                        description={
                          docs.length
                            ? "调整搜索或筛选条件后再试。"
                            : "支持 Word、PDF、Markdown、图片和表格，构建完成后即可提问。"
                        }
                        action={
                          !docs.length &&
                          canManage && (
                            <Button
                              type="primary"
                              icon={<UploadOutlined aria-hidden="true" />}
                              onClick={() => setImportOpen(true)}
                            >
                              导入文件
                            </Button>
                          )
                        }
                      />
                    ),
                  }}
                />
                <div className="bottom-tip">
                  <InfoCircleOutlined aria-hidden="true" />
                  已上传不等于可问答；只有“已就绪”且已启用的文档会参与检索。
                </div>
              </>
            ),
          },
          ...(canManage
            ? [
                {
                  key: "retrieval",
                  label: "检索测试",
                  children: <RetrievalTest key={kb.id} kb={kb} />,
                },
                {
                  key: "logs",
                  label: "任务日志",
                  children: (
                    <section className="panel">
                      <h2>知识库构建任务</h2>
                      <p className="muted">
                        只展示当前知识库的导入与构建记录。
                      </p>
                      <Table
                        rowKey="id"
                        dataSource={state.logs.filter(
                          (l) =>
                            l.kind === "ingestion" &&
                            (l.knowledgeBaseId === kb.id ||
                              (!l.knowledgeBaseId &&
                                l.assistantName === kb.name)),
                        )}
                        columns={[
                          { title: "文档", dataIndex: "query" },
                          {
                            title: "状态",
                            render: (_, l) =>
                              l.status === "completed"
                                ? "已完成"
                                : l.status === "failed"
                                  ? "失败"
                                  : l.status === "running"
                                    ? "执行中"
                                    : "已取消",
                          },
                          { title: "耗时", render: (_, l) => `${l.duration}s` },
                          { title: "异常说明", dataIndex: "error" },
                        ]}
                        locale={{ emptyText: "暂无构建任务" }}
                      />
                      <Button
                        onClick={() =>
                          navigate(
                            `/logs?type=ingestion&search=${encodeURIComponent(kb.name)}`,
                          )
                        }
                      >
                        查看全局任务监测
                      </Button>
                    </section>
                  ),
                },
                {
                  key: "settings",
                  label: "知识库设置",
                  children: <KnowledgeSettings key={kb.id} kb={kb} />,
                },
              ]
            : []),
        ]}
      />
      <ImportModal
        open={importOpen && canManage}
        onClose={() => setImportOpen(false)}
        kb={kb}
      />
      <DocumentWorkspace
        key={preview?.id}
        doc={preview}
        kbName={kb.name}
        open={!!preview}
        onClose={closePreview}
        canManage={canManage}
        retry={preview ? () => retryDoc(preview.id) : undefined}
      />
    </>
  );
}

interface ImportFile {
  name: string;
  size: number;
}
const SAMPLE_FILES = [
  { name: "差旅报销补充说明.pdf", size: 425000 },
  { name: "出差审批流程.png", size: 820000 },
  { name: "差旅费用明细.xlsx", size: 165000 },
  { name: "办公考勤说明.md", size: 12000 },
];
const ALLOWED = new Set([
  "doc",
  "docx",
  "pdf",
  "md",
  "txt",
  "html",
  "htm",
  "png",
  "jpg",
  "jpeg",
  "webp",
  "xls",
  "xlsx",
  "csv",
]);

function ImportModal({
  open,
  onClose,
  kb,
}: {
  open: boolean;
  onClose: () => void;
  kb: KnowledgeBase;
}) {
  const { importFiles, state, mode } = useStore();
  const { message } = AntApp.useApp();
  const [files, setFiles] = useState<ImportFile[]>([]);
  const [error, setError] = useState("");
  const [fail, setFail] = useState(false);
  const [autoBuild, setAutoBuild] = useState(true);
  const [fileStrategies,setFileStrategies]=useState<Record<string,string>>({});const [filePreview,setFilePreview]=useState<{name:string;strategy:string;totalChunks:number;chunks:{id:string;content:string;location:string}[]}>();const [previewBusy,setPreviewBusy]=useState(false);
  const previewFile=async(file:ImportFile)=>{if(!(file instanceof File))return;setPreviewBusy(true);try{const body=new FormData();body.append("file",file);body.append("strategy",fileStrategies[file.name]||kb.parsing?.strategy||"general");setFilePreview(await api(`/delivery/knowledge/${kb.id}/file-preview`,state.role,{method:"POST",body}))}catch(e){setError(e instanceof Error?e.message:"预览失败")}finally{setPreviewBusy(false)}};
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [sourceType,setSourceType]=useState("file");const [textName,setTextName]=useState("");const [manualText,setManualText]=useState("");const [webUrl,setWebUrl]=useState("");const [fetching,setFetching]=useState(false);
  const input = useRef<HTMLInputElement>(null);
  const close = () => {
    onClose();
    setFiles([]);setFileStrategies({});setFilePreview(undefined);
    setError("");
    setFail(false);
    setAutoBuild(true);setSourceType("file");setTextName("");setManualText("");setWebUrl("");
  };
  const addFiles = (incoming: ImportFile[]) => {
    const valid: ImportFile[] = [];
    const errors: string[] = [];
    incoming.forEach((f) => {
      const ext = f.name.split(".").pop()?.toLowerCase() || "";
      if (!ALLOWED.has(ext)) errors.push(`${f.name}：暂不支持该格式`);
      else if (f.size > 50 * 1024 * 1024)
        errors.push(`${f.name}：超过 50 MB 上限`);
      else if (f.size === 0) errors.push(`${f.name}：文件为空`);
      else valid.push(f);
    });
    setError(errors.join("；"));
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [
        ...prev,
        ...valid.filter((f) => {
          if (names.has(f.name)) return false;
          names.add(f.name);
          return true;
        }),
      ];
    });
  };
  const addText=()=>{if(!textName.trim()||!manualText.trim()){setError("请填写标题和文本内容。");return}const name=textName.trim().replace(/[\\/:*?"<>|]/g,"_").slice(0,120);addFiles([new File([manualText],`${name}.md`,{type:"text/markdown"})]);setManualText("");setTextName("");message.success("文本已加入待导入列表")};
  const addWeb=async()=>{if(!webUrl.trim())return;setFetching(true);setError("");try{const result=await api<{filename:string;content:string}>(`/knowledge-bases/${kb.id}/web-preview`,state.role,{method:"POST",body:JSON.stringify({url:webUrl.trim()})});addFiles([new File([result.content],`${Date.now()}-${result.filename}`,{type:"text/markdown"})]);setWebUrl("");message.success("网页文本已加入待导入列表")}catch(e){setError(e instanceof Error?e.message:"抓取网页失败")}finally{setFetching(false)}};
  const submit = async () => {
    const parsing = kb.parsing || DEFAULT_PARSING;
    const hasImage = files.some((f) => /\.(png|jpe?g|webp)$/i.test(f.name));
    const needsOcr =
      hasImage ||
      (parsing.pdfParser !== "text" &&
        files.some((f) => /\.pdf$/i.test(f.name)));
    const needsVision =
      parsing.understandImages &&
      files.some((f) => /\.(pdf|docx?|png|jpe?g|webp)$/i.test(f.name));
    const required = [
      "embedding",
      ...(parsing.autoMetadata ? ["llm"] : []),
      ...(needsOcr ? ["ocr"] : []),
      ...(needsVision ? ["vision"] : []),
    ];
    const missing = state.models.filter(
      (m) => required.includes(m.capability) && m.status !== "ready",
    );
    if (autoBuild && missing.length) {
      setError(
        `${missing.map((m) => m.label).join("、")}尚未就绪，请管理员先完成模型连接测试。`,
      );
      return;
    }
    setUploading(true);
    try {
      const result = await importFiles(kb.id, files, fail, autoBuild,fileStrategies);
      close();
      if (result.added)
        message.success(
          `已添加 ${result.added} 份文件${autoBuild ? (mode === "live" ? "，已提交后台构建" : "，开始模拟构建") : "，等待手动构建"}${result.skipped ? `；${result.skipped} 份${mode === "live" ? "重复内容" : "同名文件"}已跳过` : ""}`,
        );
      else message.info("所选文件已存在，没有重复导入");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "导入失败");
    } finally {
      setUploading(false);
    }
  };
  return (
    <Modal
      title="导入知识文档"
      open={open}
      onCancel={() => {
        if (!uploading) close();
      }}
      confirmLoading={uploading}
      onOk={submit}
      okText={files.length ? `导入 ${files.length} 份文件` : "导入并构建"}
      okButtonProps={{ disabled: !files.length }}
      cancelText="取消"
      width={670}
    >
      <p className="modal-description">
        导入到 <strong>{kb.name}</strong>，构建完成后用于知识检索与在线问答。
      </p>
      <Segmented value={sourceType} onChange={value=>setSourceType(String(value))} options={[{value:"file",label:"文件"},{value:"text",label:"文本 / 问答"},{value:"web",label:"网页"},{value:"feishu",label:"飞书"}]} style={{marginBottom:16}}/>
      {sourceType==="text"&&<div style={{marginBottom:16}}><p className="muted">粘贴文本或按“问题 / 答案”格式输入，加入列表后作为 Markdown 文档导入。</p><Input aria-label="文本标题" placeholder="文本标题" value={textName} onChange={e=>setTextName(e.target.value)}/><Input.TextArea aria-label="文本内容" style={{marginTop:8}} rows={5} maxLength={200000} placeholder="输入知识内容或问答对" value={manualText} onChange={e=>setManualText(e.target.value)}/><Button style={{marginTop:8}} onClick={addText}>加入待导入列表</Button></div>}
      {sourceType==="web"&&<div style={{marginBottom:16}}><p className="muted">只抓取公网 HTTPS 页面的静态文本，不执行网页脚本。需要登录的页面可在浏览器保存为 HTML 文件后导入；只读取保存时已有的正文，不加载附带资源。</p><Input aria-label="网页地址" placeholder="https://example.com/page" value={webUrl} onChange={e=>setWebUrl(e.target.value)}/><Button style={{marginTop:8}} loading={fetching} disabled={mode!=="live"||!webUrl.trim()} onClick={()=>void addWeb()}>抓取并加入列表</Button></div>}
      {sourceType==="feishu"&&(mode==="live"?<FeishuImport kbId={kb.id} role={state.role} onAdd={addFiles}/>:<Alert type="info" title="请切换到真实后端后使用飞书导入"/>)}
      {sourceType==="file"&&<div
        className={`upload-zone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles(Array.from(e.dataTransfer.files));
        }}
      >
        <CloudUploadOutlined aria-hidden="true" />
        <h3>将文件拖到这里，或选择本地文件</h3>
        <p>
          Word、PDF、Markdown、TXT、HTML、图片、Excel / CSV
          <br />
          单文件不超过 50 MB
        </p>
        <Button
          icon={<UploadOutlined aria-hidden="true" />}
          onClick={() => input.current?.click()}
        >
          选择本地文件
        </Button>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          accept=".doc,.docx,.pdf,.md,.txt,.html,.htm,.png,.jpg,.jpeg,.webp,.xls,.xlsx,.csv"
          onChange={(e) => {
            addFiles(Array.from(e.target.files || []));
            e.target.value = "";
          }}
        />
      </div>
      }
      {mode === "demo" && sourceType === "file" && (
        <div className="sample-file-line">
          <span>还没有准备好文件？</span>
          <Button type="link" onClick={() => addFiles(SAMPLE_FILES)}>
            使用 4 份示例文件 <ArrowRightOutlined aria-hidden="true" />
          </Button>
        </div>
      )}
      {error && <Alert showIcon type="error" title={error} />}
      <div className="import-file-list">
        {files.map((f) => (
          <div key={f.name}>
            <FileIcon format={f.name.split(".").pop()!.toUpperCase()} small />
            <strong>{f.name}</strong>
            <span>{sizeText(f.size)}</span>
            {mode==="live"&&<><Select aria-label={`切分方法 ${f.name}`} style={{width:180}} value={fileStrategies[f.name]||kb.parsing?.strategy||"general"} options={CHUNKING_METHODS.map(m=>({value:m.value,label:m.label}))} onChange={v=>setFileStrategies({...fileStrategies,[f.name]:v})}/><Button loading={previewBusy} onClick={()=>void previewFile(f)}>分块预览</Button></>}
            <Button
              type="text"
              size="small"
              aria-label={`移除待导入文件${f.name}`}
              icon={<DeleteOutlined aria-hidden="true" />}
              onClick={() => setFiles(files.filter((x) => x.name !== f.name))}
            />
          </div>
        ))}
      </div>
      <Modal open={!!filePreview} width={800} title={`${filePreview?.name||""} · ${filePreview?.totalChunks||0} 个分块`} onCancel={()=>setFilePreview(undefined)} footer={null}><p>本地解析预览，未入库；最多展示 30 块。扫描件和图片需要在正式构建中执行 OCR。</p>{filePreview?.chunks.map(c=><article key={c.id}><strong>{c.location}</strong><pre className="delivery-json">{c.content}</pre></article>)}</Modal>
      <div className="import-rule-summary">
        <strong>当前解析规则</strong>
        <span>
          {kb.parsing?.pdfParser === "text"
            ? "原生提取"
            : "原生提取 / 云端 OCR"}{" "}
          · {CHUNKING_LABELS[(kb.parsing || DEFAULT_PARSING).strategy]} · 每块上限 {kb.chunkSize} 字符 · 重叠 {kb.overlap} 字符
        </span>
        <span>
          {mode === "live"
            ? "原文件将上传本机后端，图片识别按配置使用云端 API。"
            : "正式解析将保留原文、图表与来源位置；当前仅演示配置。"}
        </span>
        <span>
          {kb.parsing?.autoMetadata
            ? `自动提取元数据：开启 · ${kb.parsing.metadataModel || state.models.find((model) => model.capability === "llm")?.model || "默认 LLM"}${mode === "live" ? "（产生 LLM Token 用量）" : "（模拟）"}`
            : "自动提取元数据：关闭，可在知识库设置中开启"}
        </span>
      </div>
      <div style={{ marginBottom: 12 }}>
        <Checkbox
          checked={autoBuild}
          onChange={(e) => setAutoBuild(e.target.checked)}
        >
          导入后自动构建
        </Checkbox>
        <span className="muted small-text">
          关闭后可先检查规则，再手动构建。
        </span>
      </div>
      {mode === "demo" && (
        <Checkbox
          disabled={!autoBuild}
          checked={fail}
          onChange={(e) => setFail(e.target.checked)}
        >
          演示失败情景：让首份文件识别超时，随后可重试
        </Checkbox>
      )}
      <div className="import-notice">
        <InfoCircleOutlined aria-hidden="true" />
        {mode === "live"
          ? "上传的是所选文件的实际内容。云端解析和构建会使用配置的通义模型，并产生相应用量。"
          : "原型只读取文件名与大小，不上传或解析文件。预览内容与用量为示例。"}
      </div>
    </Modal>
  );
}

function RetrievalTest({ kb }: { kb: KnowledgeBase }) {
  const { state, update, mode, retrieveKnowledge } = useStore();
  const { message } = AntApp.useApp();
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(6);
  const [candidateK, setCandidateK] = useState(30);
  const [rerank, setRerank] = useState(true);
  const [retrieval, setRetrieval] = useState({
    ...DEFAULT_RETRIEVAL,
    rerankModel:
      state.models.find((m) => m.capability === "rerank")?.model || "",
  });
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<Citation[] | null>(null);
  const [testError, setTestError] = useState("");
  const [source, setSource] = useState<number | null>(null);
  const [apply, setApply] = useState(false);
  const [assistantId, setAssistantId] = useState<string>();
  const [version, setVersion] = useState(1);
  const [testedVersion, setTestedVersion] = useState(0);
  const [duration, setDuration] = useState(0);
  const run = async () => {
    if (!query.trim()) {
      message.warning("请输入一个测试问题");
      return;
    }
    if (topK > candidateK) {
      message.warning("返回片段数不能超过候选片段数");
      return;
    }
    setLoading(true);
    setTestError("");
    const started = performance.now();
    try {
      if (
        rerank &&
        state.models.find((m) => m.capability === "rerank")?.status !== "ready"
      )
        throw new Error(
          "Rerank 模型尚未通过连接测试。请先配置，或关闭重排后测试。",
        );
      const result = await retrieveKnowledge({
        query,
        kbIds: [kb.id],
        topK,
        candidateK,
        useRerank: rerank,
        retrieval,
      });
      setResults(result);
      setTestedVersion(version);
      setDuration((performance.now() - started) / 1000);
    } catch (caught) {
      setTestError(caught instanceof Error ? caught.message : "检索失败");
      setResults(null);
    } finally {
      setLoading(false);
    }
  };
  const change = () => setVersion((v) => v + 1);
  const targets = state.assistants.filter((a) => a.kbIds.includes(kb.id));
  const save = () => {
    if (!assistantId) {
      message.warning("请选择要应用的助手");
      return;
    }
    update((p) => ({
      ...p,
      assistants: p.assistants.map((a) =>
        a.id === assistantId
          ? {
              ...a,
              topK,
              candidateK,
              useRerank: rerank,
              retrieval: { ...retrieval },
              configVersion: a.configVersion + 1,
              retrievalVersion: version,
            }
          : a,
      ),
    }));
    setApply(false);
    message.success(
      mode === "live"
        ? "助手配置变更已提交，新会话将使用此配置"
        : "已应用到助手，新会话将使用此配置",
    );
  };
  return (
    <div className="retrieval-layout">
      <section className="panel retrieval-config">
        <div className="panel-heading">
          <h2>测试一个真实问题</h2>
          <FileSearchOutlined aria-hidden="true" />
        </div>
        <p className="muted">先确认能找到正确资料，再检查回答效果。</p>
        <label className="field-label" htmlFor="retrieval-question">
          测试问题
        </label>
        <Input.TextArea
          id="retrieval-question"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            change();
          }}
          placeholder="例如：差旅报销需要哪些附件？"
          rows={4}
          maxLength={500}
        />
        <div className="query-suggestions">
          {(kb.id === "kb-product"
            ? ["API 返回 429 怎么处理？", "如何配置接口密钥？"]
            : kb.id === "kb-brand"
              ? ["品牌文案有哪些要求？"]
              : ["差旅报销需要哪些附件？", "年假应该怎么申请？"]
          ).map((q) => (
            <button
              key={q}
              onClick={() => {
                setQuery(q);
                change();
              }}
            >
              {q}
            </button>
          ))}
        </div>
        <div className="advanced-config">
          <h3>
            检索参数<Tag variant="filled">测试版本 v{version}</Tag>
          </h3>
          <label>
            相似度阈值{" "}
            <InputNumber
              aria-label="相似度阈值"
              min={0}
              max={1}
              step={0.05}
              value={retrieval.threshold}
              onChange={(v) => {
                setRetrieval((p) => ({ ...p, threshold: v ?? 0.2 }));
                change();
              }}
            />
          </label>
          <label>
            向量相似度权重 <strong>{retrieval.vectorWeight.toFixed(2)}</strong>
          </label>
          <Slider
            aria-label="向量相似度权重"
            min={0}
            max={1}
            step={0.05}
            value={retrieval.vectorWeight}
            onChange={(v) => {
              setRetrieval((p) => ({ ...p, vectorWeight: v }));
              change();
            }}
          />
          <p>
            向量 {(retrieval.vectorWeight * 100).toFixed(0)}% / 全文{" "}
            {((1 - retrieval.vectorWeight) * 100).toFixed(0)}% ·{" "}
            {mode === "live" ? "评分来自实际检索" : "评分为演示值"}
          </p>
          <label>
            候选片段数{" "}
            <InputNumber
              aria-label="候选片段数"
              min={6}
              max={60}
              value={candidateK}
              onChange={(v) => {
                setCandidateK(v || 30);
                change();
              }}
            />
          </label>
          <label>
            返回片段数 <strong>{topK}</strong>
          </label>
          <Slider
            aria-label="返回片段数"
            min={1}
            max={12}
            value={topK}
            onChange={(v) => {
              setTopK(v);
              change();
            }}
          />
          <label>
            Rerank 重排
            <Switch
              aria-label="启用重排"
              checked={rerank}
              onChange={(v) => {
                setRerank(v);
                change();
              }}
            />
          </label>
          {rerank && (
            <Select
              aria-label="重排模型"
              style={{ width: "100%" }}
              value={retrieval.rerankModel}
              onChange={(v) => {
                setRetrieval((p) => ({ ...p, rerankModel: v }));
                change();
              }}
              options={(state.catalog || [])
                .filter((m) => m.capability === "rerank" && m.enabled)
                .map((m) => ({ value: m.model, label: m.model }))}
            />
          )}
          <label>文档范围</label>
          <Select
            mode="multiple"
            aria-label="检索文档范围"
            style={{ width: "100%" }}
            value={retrieval.fileIds}
            placeholder="全部已就绪文档"
            onChange={(v) => {
              setRetrieval((p) => ({ ...p, fileIds: v }));
              change();
            }}
            options={state.documents
              .filter((d) => d.kbId === kb.id && isRetrievable(d))
              .map((d) => ({ value: d.id, label: d.name }))}
          />
          <label>元数据筛选</label>
          <Select
            aria-label="元数据字段"
            allowClear
            placeholder="不限字段"
            value={retrieval.metadataKey || undefined}
            style={{ width: "100%", marginBottom: 8 }}
            onChange={(v) => {
              setRetrieval((p) => ({
                ...p,
                metadataKey: v || "",
                metadataValue: "",
              }));
              change();
            }}
            options={metadataFieldNames(
              state.documents.filter((doc) => doc.kbId === kb.id),
            ).map((v) => ({
              value: v,
              label: v,
            }))}
          />
          {retrieval.metadataKey && (
            <Input
              aria-label="元数据匹配值"
              placeholder="精确匹配的值"
              value={retrieval.metadataValue}
              onChange={(e) => {
                setRetrieval((p) => ({ ...p, metadataValue: e.target.value }));
                change();
              }}
            />
          )}
          <p>
            使用{" "}
            {rerank
              ? state.models.find((m) => m.capability === "rerank")?.model
              : "混合召回排序"}
          </p>
        </div>
        <Button
          type="primary"
          block
          icon={<SearchOutlined aria-hidden="true" />}
          loading={loading}
          onClick={run}
        >
          运行检索测试
        </Button>
        {mode === "live" && (
          <p className="muted small-text">
            阈值筛选向量相似度或关键词覆盖度，再融合排序；融合分数与阈值不直接比较。
          </p>
        )}
        <DemoNote compact />
      </section>
      <section className="panel retrieval-results">
        <div className="panel-heading">
          <h2>检索结果 {results && <span>{results.length}</span>}</h2>
          {results && (
            <span className="muted small-text">
              {mode === "live" ? "实际耗时" : "本地演示耗时"}{" "}
              {duration.toFixed(2)}s
            </span>
          )}
        </div>
        {testError ? (
          <Alert type="error" showIcon title={testError} />
        ) : loading ? (
          <div className="retrieval-loading">
            <Progress percent={60} status="active" showInfo={false} />
            <p>正在查找相关资料并排序…</p>
          </div>
        ) : results === null ? (
          <Blank
            title="检查知识，是否找得到"
            description="输入一个问题，查看命中的内容片段、来源和排序。"
          />
        ) : !results.length ? (
          <Blank
            title="没有找到足够依据"
            description={
              mode === "live"
                ? "请检查资料是否已就绪并启用，或调整检索阈值与问题。"
                : "请检查资料是否已就绪并启用，或调整问题。原型仅匹配内置示例资料。"
            }
          />
        ) : (
          <>
            <div className="test-result-note">
              <CheckCircleOutlined aria-hidden="true" />
              已找到 {results.length} 个相关片段
              <Tag variant="filled">
                {mode === "live" ? "真实检索" : "演示匹配"}
              </Tag>
            </div>
            {results.map((r, i) => (
              <article className="retrieval-result" key={r.id}>
                <div>
                  <span className="result-rank">{i + 1}</span>
                  <FileIcon format={r.format} small />
                  <strong>{r.name}</strong>
                  <Tag color="blue" variant="filled">
                    相关性 {r.score.toFixed(2)}
                  </Tag>
                </div>
                <div className="score-line">
                  <span>
                    {mode === "live" ? "融合" : "混合"}{" "}
                    {r.hybridScore?.toFixed(3) ?? "—"}
                  </span>
                  <span>向量 {r.vectorScore?.toFixed(2) ?? "—"}</span>
                  <span>全文 {r.textScore?.toFixed(2) ?? "—"}</span>
                  {r.rerankScore !== undefined && (
                    <span>重排 {r.rerankScore.toFixed(2)}</span>
                  )}
                </div>
                <p>{r.content}</p>
                <footer>
                  <span>{r.location}</span>
                  <button onClick={() => setSource(i)}>
                    查看来源 <ArrowRightOutlined aria-hidden="true" />
                  </button>
                </footer>
              </article>
            ))}
            <div className="apply-retrieval">
              <p>测试参数仅用于当前测试，应用后才会影响助手。</p>
              <Button
                disabled={testedVersion !== version || !targets.length}
                onClick={() => {
                  setAssistantId(targets[0]?.id);
                  setApply(true);
                }}
              >
                应用参数到助手
              </Button>
              {testedVersion !== version && (
                <small>参数已修改，请重新测试</small>
              )}
              {!targets.length && <small>尚无助手绑定此知识库</small>}
            </div>
          </>
        )}
      </section>
      <SourceDrawer
        citations={results || []}
        open={source !== null}
        initialIndex={source || 0}
        onClose={() => setSource(null)}
      />
      <Modal
        open={apply}
        title="应用检索参数"
        onCancel={() => setApply(false)}
        onOk={save}
        okText="应用到助手"
        cancelText="取消"
      >
        <p className="modal-description">
          现有会话保留其配置快照，新会话使用更新后的参数。
        </p>
        <Select
          style={{ width: "100%" }}
          aria-label="选择目标助手"
          value={assistantId}
          onChange={setAssistantId}
          options={targets.map((a) => ({ value: a.id, label: a.name }))}
        />
      </Modal>
    </div>
  );
}

function KnowledgeSettings({ kb }: { kb: KnowledgeBase }) {
  const { update, state, mode } = useStore();
  const { message } = AntApp.useApp();
  const [form] = Form.useForm();
  return (
    <div className="panel settings-panel">
      <h2>知识库设置</h2>
      <p className="muted">基础信息与解析默认值。模型空间与索引需保持一致。</p>
      <Form
        form={form}
        layout="vertical"
        initialValues={{ ...kb, parsing: kb.parsing || DEFAULT_PARSING }}
        onFinish={(values) => {
          if (values.overlap >= values.chunkSize) {
            message.error("重叠长度必须小于分块长度");
            return;
          }
          if (
            state.knowledgeBases.some(
              (k) => k.id !== kb.id && k.name === values.name.trim(),
            )
          ) {
            message.error("已存在同名知识库");
            return;
          }
          update((p) => ({
            ...p,
            knowledgeBases: p.knowledgeBases.map((k) =>
              k.id === kb.id
                ? {
                    ...k,
                    ...values,
                    name: values.name.trim(),
                    parsingVersion:
                      (k.parsingVersion || 1) +
                      (k.chunkSize !== values.chunkSize ||
                      k.overlap !== values.overlap ||
                      JSON.stringify(k.parsing) !==
                        JSON.stringify(values.parsing)
                        ? 1
                        : 0),
                    updatedAt: now(),
                  }
                : k,
            ),
          }));
          message.success(
            "默认规则变更已提交，新导入使用此规则；已有文档需重新构建后生效",
          );
        }}
      >
        <Form.Item
          label="知识库名称"
          name="name"
          rules={[{ required: true, whitespace: true, message: "请输入名称" }]}
        >
          <Input maxLength={40} />
        </Form.Item>
        <Form.Item label="说明" name="description">
          <Input.TextArea rows={3} maxLength={160} />
        </Form.Item>
        <Form.Item label="Embedding 模型" name="embedding">
          <Input disabled />
        </Form.Item>
        <Alert
          type="info"
          showIcon
          title={
            mode === "live"
              ? "已有索引的知识库已锁定 Embedding 模型。需要更换模型时，请新建知识库并重新导入构建，避免混用不同模型的向量。"
              : "已有索引时不能混用不同模型的向量。当前原型展示配置关系。"
          }
        />
        <h3 className="config-section-title">解析与分块规则</h3>
        <ParsingFields embedding={kb.embedding} />
        <Alert
          type="warning"
          showIcon
          title="修改规则后需重新构建已有文档。手工分块会在重新构建时被覆盖，请先核对。"
        />
        <Button
          htmlType="submit"
          type="primary"
          disabled={state.documents.some(
            (d) => d.kbId === kb.id && d.status === "processing",
          )}
        >
          保存设置
        </Button>
      </Form>
    </div>
  );
}
