import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Tabs,
  Tag,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import type { Citation, KnowledgeChunk, KnowledgeDoc } from "./types";
import { DemoNote, DocStatusTag, FileIcon, SourceDocument } from "./components";
import { useStore } from "./store-context";
import { DEFAULT_PARSING, STRATEGY_LABELS } from "./configuration";
import { now, uid } from "./demo";
import { ParsingFields } from "./ParsingFields";
import { StructuredTable } from "./StructuredTable";
import { MetadataWorkspace } from "./MetadataWorkspace";

export function DocumentWorkspace({
  doc,
  kbName,
  open,
  onClose,
  canManage,
  retry,
}: {
  doc?: KnowledgeDoc;
  kbName: string;
  open: boolean;
  onClose: () => void;
  canManage: boolean;
  retry?: () => void;
}) {
  const { state, update, mode, flush, loadDocument } = useStore();
  const { message, modal } = App.useApp();
  const [tab, setTab] = useState("chunks");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<KnowledgeChunk | null>(null);
  const [adding, setAdding] = useState(false);
  const [chunkForm] = Form.useForm();
  const [parseForm] = Form.useForm();
  const [detailError, setDetailError] = useState("");
  useEffect(() => {
    if (!open || !doc?.detailsOmitted || !loadDocument) return;
    let active = true;
    setDetailError("");
    loadDocument(doc.id).catch((error: unknown) => {
      if (active) setDetailError(error instanceof Error ? error.message : "文档加载失败，请重试");
    });
    return () => { active = false; };
  }, [open, doc?.id, doc?.updatedAt, doc?.detailsOmitted, loadDocument]);
  if (!doc) return null;
  const kb = state.knowledgeBases.find((k) => k.id === doc.kbId)!;
  const chunks = doc.segments || [];
  const list = chunks.filter(
    (c) =>
      (filter === "all" || (filter === "enabled" ? c.enabled : !c.enabled)) &&
      `${c.content} ${c.keywords.join(" ")} ${c.questions.join(" ")} ${(c.aiKeywords || []).join(" ")} ${(c.aiQuestions || []).join(" ")}`.includes(
        search,
      ),
  );
  const citation: Citation = {
    id: doc.id,
    docId: doc.id,
    name: doc.name,
    format: doc.format,
    kbName,
    content: doc.content,
    location: doc.location,
    provenance: doc.provenance,
    score: 1,
  };
  const change = (fn: (d: KnowledgeDoc) => KnowledgeDoc) =>
    update((p) => ({
      ...p,
      documents: p.documents.map((d) => (d.id === doc.id ? fn(d) : d)),
    }));
  const saveChunk = () =>
    chunkForm
      .validateFields()
      .then(async (values) => {
        const item: KnowledgeChunk = {
          id: editing?.id || uid("chunk"),
          content: values.content.trim(),
          location: values.location.trim(),
          enabled: editing?.enabled ?? true,
          keywords: values.keywords || [],
          questions: values.questions || [],
          aiKeywords: editing?.aiKeywords || [],
          aiQuestions: editing?.aiQuestions || [],
          manual: true,
        };
        change((d) => {
          const segments = editing
            ? (d.segments || []).map((c) => (c.id === editing.id ? item : c))
            : [...(d.segments || []), item];
          return { ...d, segments, chunks: segments.length, updatedAt: now() };
        });
        await flush();
        setEditing(null);
        setAdding(false);
        message.success(
          mode === "live"
            ? "分块变更已提交，索引更新期间请等待保存完成"
            : "演示分块已保存，后续检索使用更新后的内容",
        );
      })
      .catch((error) => {
        if (error instanceof Error) message.error(error.message);
      });
  const openChunk = (c?: KnowledgeChunk) => {
    chunkForm.resetFields();
    chunkForm.setFieldsValue(
      c || { content: "", location: doc.location, keywords: [], questions: [] },
    );
    setEditing(c || null);
    setAdding(!c);
  };
  const batch = (enabled: boolean) => {
    change((d) => ({
      ...d,
      segments: (d.segments || []).map((c) =>
        selected.includes(c.id) ? { ...c, enabled } : c,
      ),
    }));
    setSelected([]);
  };
  const rebuild = () =>
    modal.confirm({
      title:
        doc.status === "uploaded" ? "开始构建此文档？" : "重新构建此文档？",
      content:
        doc.status === "uploaded"
          ? "按当前解析规则处理上传文件并建立知识索引，完成后可用于检索与问答。"
          : "分块会按当前规则重新生成，手工新增或编辑的分块会被覆盖。构建成功前保留原索引。",
      okText: doc.status === "uploaded" ? "开始构建" : "确认重新构建",
      cancelText: "取消",
      onOk: retry,
    });
  return (
    <Modal
      open={open}
      onCancel={onClose}
      width="calc(100vw - 64px)"
      style={{ top: 24 }}
      className="chunk-workspace-modal"
      footer={<Button onClick={onClose}>关闭文档工作区</Button>}
      title={
        <Space>
          <FileIcon format={doc.format} small />
          {doc.name}
          <DocStatusTag status={doc.status} progress={doc.progress} />
        </Space>
      }
    >
      <div className="workspace-summary">
        <span>
          {kbName} / {doc.location}
        </span>
        <span>
          {doc.detailsOmitted ? `${doc.chunks} 个分块 · 正在加载详情` : `${chunks.length} 个分块 · ${chunks.filter((c) => c.enabled).length} 个启用`}
        </span>
        {canManage && (
          <Button
            size="small"
            disabled={doc.status === "processing" || doc.detailsOmitted}
            onClick={rebuild}
          >
            {doc.status === "uploaded" ? "开始构建" : "重新构建"}
          </Button>
        )}
      </div>
      {doc.error && <Alert type="warning" showIcon title={doc.error} />}
      {doc.detailsOmitted && (detailError ? <Alert type="error" showIcon title={detailError} action={<Button onClick={() => { setDetailError(""); void loadDocument?.(doc.id).catch((error: Error) => setDetailError(error.message)); }}>重新加载</Button>} /> : <div style={{padding:48,textAlign:"center"}}><Spin description="正在加载原文、分块和元数据" /></div>)}
      {!doc.detailsOmitted && <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: "chunks",
            label: "原文与分块",
            children: (
              <div className="document-workbench">
                <section className="original-pane">
                  <div className="section-heading">
                    <h3>原文对照</h3>
                    <Tag>{mode === "live" ? "上传原文" : "样例原文"}</Tag>
                  </div>
                  <SourceDocument citation={citation} />
                </section>
                <section className="segments-pane">
                  <div className="section-heading">
                    <h3>分块管理</h3>
                    {canManage && (
                      <Button
                        icon={<PlusOutlined aria-hidden="true" />}
                        disabled={doc.status !== "ready"}
                        onClick={() => openChunk()}
                      >
                        新增分块
                      </Button>
                    )}
                  </div>
                  <div className="chunk-toolbar">
                    <Input
                      aria-label="搜索分块"
                      placeholder="搜索正文、关键词或关联问题"
                      prefix={<SearchOutlined aria-hidden="true" />}
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      allowClear
                    />
                    <Select
                      aria-label="分块状态"
                      value={filter}
                      onChange={setFilter}
                      options={[
                        { value: "all", label: "全部分块" },
                        { value: "enabled", label: "已启用" },
                        { value: "disabled", label: "已停用" },
                      ]}
                    />
                  </div>
                  {canManage && !!list.length && (
                    <div className="chunk-batch">
                      <Checkbox
                        checked={
                          list.length > 0 &&
                          list.every((c) => selected.includes(c.id))
                        }
                        onChange={(e) =>
                          setSelected(
                            e.target.checked ? list.map((c) => c.id) : [],
                          )
                        }
                      >
                        选择当前结果
                      </Checkbox>
                      {selected.length > 0 && (
                        <>
                          <span>已选 {selected.length}</span>
                          <Button size="small" onClick={() => batch(true)}>
                            启用所选
                          </Button>
                          <Button size="small" onClick={() => batch(false)}>
                            停用所选
                          </Button>
                        </>
                      )}
                    </div>
                  )}
                  {!list.length && (
                    <div className="blank">
                      <h3>
                        {chunks.length ? "没有匹配的分块" : "暂无可检索分块"}
                      </h3>
                      <p>
                        {doc.status === "ready"
                          ? "可新增分块，或调整搜索条件。"
                          : "请先完成构建，再检查解析内容。"}
                      </p>
                    </div>
                  )}
                  {list.map((c, i) => (
                    <article
                      key={c.id}
                      className={`managed-chunk ${c.enabled ? "" : "disabled"}`}
                    >
                      <header>
                        <Space>
                          {canManage && (
                            <Checkbox
                              aria-label={`选择分块 ${i + 1}`}
                              checked={selected.includes(c.id)}
                              onChange={(e) =>
                                setSelected((p) =>
                                  e.target.checked
                                    ? [...p, c.id]
                                    : p.filter((x) => x !== c.id),
                                )
                              }
                            />
                          )}
                          <strong>分块 {i + 1}</strong>
                          <Tag>{c.manual ? "手工修订" : "解析生成"}</Tag>
                        </Space>
                        {canManage && (
                          <Switch
                            size="small"
                            aria-label={`分块 ${i + 1}参与检索`}
                            checked={c.enabled}
                            disabled={doc.status === "processing"}
                            onChange={(enabled) =>
                              change((d) => ({
                                ...d,
                                segments: (d.segments || []).map((x) =>
                                  x.id === c.id ? { ...x, enabled } : x,
                                ),
                              }))
                            }
                          />
                        )}
                      </header>
                      {c.table ? <><p className="muted small-text">{c.sectionPath?.join(" / ") || "原文表格"}</p><StructuredTable table={c.table} /></> : <p>{c.content}</p>}
                      <div>
                        {c.keywords.map((k) => (
                          <Tag key={k}>{k}</Tag>
                        ))}
                      </div>
                      {!!c.questions.length && (
                        <div className="muted small-text">
                          关联问题：{c.questions.join("；")}
                        </div>
                      )}
                      {!!c.aiKeywords?.length && (
                        <div className="metadata-generated-tags">
                          <Tag color="blue">AI 关键词</Tag>
                          {c.aiKeywords.map((keyword, index) => (
                            <Tag key={index} color="blue">
                              {keyword}
                            </Tag>
                          ))}
                        </div>
                      )}
                      {!!c.aiQuestions?.length && (
                        <div className="muted small-text">
                          <Tag color="blue">AI 生成问题</Tag>
                          {c.aiQuestions.join("；")}
                        </div>
                      )}
                      <footer>
                        <span>{c.chunkMethod && <Tag>{STRATEGY_LABELS[c.chunkMethod]}</Tag>}{c.sectionPath?.length ? `${c.sectionPath.join(" / ")} · ` : ""}{c.location}{c.splitNote && <span className="muted small-text" style={{display:"block"}}>{c.splitNote}</span>}</span>
                        {canManage && (
                          <Space>
                            <Button
                              size="small"
                              type="text"
                              disabled={doc.status === "processing"}
                              icon={<EditOutlined aria-hidden="true" />}
                              onClick={() => openChunk(c)}
                            >
                              编辑分块
                            </Button>
                            <Button
                              size="small"
                              type="text"
                              danger
                              disabled={doc.status === "processing"}
                              icon={<DeleteOutlined aria-hidden="true" />}
                              onClick={() =>
                                modal.confirm({
                                  title:
                                    mode === "live"
                                      ? "删除这个分块？"
                                      : "删除这个演示分块？",
                                  content:
                                    "文档原文保留，后续检索不再包含此分块。",
                                  okText: "删除分块",
                                  cancelText: "取消",
                                  onOk: () =>
                                    change((d) => {
                                      const segments = (
                                        d.segments || []
                                      ).filter((x) => x.id !== c.id);
                                      return {
                                        ...d,
                                        segments,
                                        chunks: segments.length,
                                      };
                                    }),
                                })
                              }
                            >
                              删除
                            </Button>
                          </Space>
                        )}
                      </footer>
                    </article>
                  ))}
                  <DemoNote compact />
                </section>
              </div>
            ),
          },
          {
            key: "metadata",
            label: "文档元数据",
            children: <MetadataWorkspace doc={doc} canManage={canManage} />,
          },
          ...(canManage
            ? [
                {
                  key: "parsing",
                  label: "文档解析规则",
                  children: (
                    <section className="settings-panel">
                      <h3>文档解析规则</h3>
                      <p className="muted">
                        当前使用：
                        {doc.parsing
                          ? "文档自定义规则"
                          : "知识库默认规则"} ·{" "}
                        {
                          STRATEGY_LABELS[
                            (doc.parsing || kb.parsing || DEFAULT_PARSING)
                              .strategy
                          ]
                        }
                      </p>
                      <Form
                        form={parseForm}
                        layout="vertical"
                        initialValues={{
                          parsing: doc.parsing || kb.parsing || DEFAULT_PARSING,
                          chunkSize: doc.chunkSizeOverride || kb.chunkSize,
                          overlap: doc.overlapOverride ?? kb.overlap,
                        }}
                        disabled={doc.status === "processing"}
                        onFinish={(values) => {
                          if (values.overlap >= values.chunkSize) {
                            message.error("重叠长度必须小于分块长度");
                            return;
                          }
                          change((d) => ({
                            ...d,
                            parsing: values.parsing,
                            chunkSizeOverride: values.chunkSize,
                            overlapOverride: values.overlap,
                            indexParsingVersion: 0,
                          }));
                          message.success("文档规则变更已提交，重新构建后生效");
                        }}
                      >
                        <Alert
                          type="info"
                          showIcon
                          title="保存自定义规则不会自动重建或覆盖手工分块；重新构建后生效。"
                        />
                        <ParsingFields embedding={kb.embedding} />
                        <Space>
                          <Button type="primary" htmlType="submit">
                            保存文档规则
                          </Button>
                          <Button
                            onClick={() => {
                              change((d) => ({
                                ...d,
                                parsing: mode === "live" ? null : undefined,
                                chunkSizeOverride:
                                  mode === "live" ? null : undefined,
                                overlapOverride:
                                  mode === "live" ? null : undefined,
                                indexParsingVersion: 0,
                              }));
                              parseForm.setFieldsValue({
                                parsing: kb.parsing || DEFAULT_PARSING,
                                chunkSize: kb.chunkSize,
                                overlap: kb.overlap,
                              });
                              message.success("恢复继承已提交，重新构建后生效");
                            }}
                          >
                            恢复继承知识库
                          </Button>
                        </Space>
                      </Form>
                    </section>
                  ),
                },
              ]
            : []),
        ]}
      />}
      <Modal
        title={editing ? "编辑分块" : "新增分块"}
        open={!!editing || adding}
        onCancel={() => {
          setEditing(null);
          setAdding(false);
        }}
        onOk={saveChunk}
        okText="保存分块"
        cancelText="取消"
        width={680}
        destroyOnHidden
      >
        <Form form={chunkForm} layout="vertical">
          <Form.Item
            name="content"
            label="分块正文"
            rules={[
              { required: true, whitespace: true, message: "请输入分块正文" },
            ]}
          >
            <Input.TextArea rows={7} maxLength={4000} />
          </Form.Item>
          <Form.Item
            name="location"
            label="来源位置"
            rules={[
              {
                required: true,
                whitespace: true,
                message: "请填写可核对的来源位置",
              },
            ]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="keywords" label="关键词">
            <Select mode="tags" tokenSeparators={[",", "，"]} />
          </Form.Item>
          <Form.Item name="questions" label="关联问题">
            <Select mode="tags" />
          </Form.Item>
        </Form>
        <Alert
          type="info"
          showIcon
          title={
            mode === "live"
              ? "修改分块后，后端会更新向量索引；保存期间请等待完成。不改写原文件。"
              : "仅修改演示分块。正文、关键词和关联问题参与演示检索；不改写原文件。"
          }
        />
      </Modal>
    </Modal>
  );
}
