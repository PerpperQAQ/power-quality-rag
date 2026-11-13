import { useEffect, useRef, useState } from "react";
import { Alert, App, Button, Checkbox, Form, Input, Space, Tag } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { KnowledgeDoc } from "./types";
import { useStore } from "./store-context";
import {
  automaticMetadata,
  demoApplyManualMetadata,
  FACT_LABELS,
  metadataFieldNames,
  revisedManualMetadata,
} from "./metadata";
import { timeText } from "./demo";

const statusLabels = {
  processing: "抽取中",
  completed: "抽取成功",
  failed: "抽取失败",
  disabled: "自动抽取已关闭",
  not_started: "尚未抽取",
};
export function MetadataWorkspace({
  doc,
  canManage,
}: {
  doc: KnowledgeDoc;
  canManage: boolean;
}) {
  const { state, mode, update, flush, extractMetadata, syncing } = useStore();
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();
  const [dirty, setDirty] = useState(false);
  const [preserveManual, setPreserveManual] = useState(true);
  const [saving, setSaving] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const changed = useRef(new Set<string>());
  const extraction = doc.metadataExtraction;
  const successful =
    extraction?.status !== "completed" && extraction?.lastSuccess
      ? extraction.lastSuccess
      : extraction;
  const kb = state.knowledgeBases.find((item) => item.id === doc.kbId);
  const config = doc.parsing || kb?.parsing;
  const status =
    extraction?.status || (config?.autoMetadata ? "not_started" : "disabled");
  const busy = status === "processing";
  const signature = JSON.stringify([doc.metadata, doc.manualMetadata]);
  const fields = [
    ...new Set([
      ...metadataFieldNames([doc]),
      ...Object.keys(doc.manualMetadata || {}),
    ]),
  ];
  const automatic = automaticMetadata(extraction);
  const manual = doc.manualMetadata || {};
  useEffect(() => {
    if (dirty) return;
    form.resetFields();
    form.setFieldsValue(doc.metadata || {});
    changed.current.clear();
  }, [doc.id, signature, dirty, form]);

  const commitManual = async (manualMetadata: Record<string, string>) => {
    update((prev) => ({
      ...prev,
      documents: prev.documents.map((item) =>
        item.id === doc.id
          ? mode === "live"
            ? { ...item, manualMetadata }
            : demoApplyManualMetadata(item, manualMetadata)
          : item,
      ),
    }));
    await flush();
  };
  const save = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      const changedValues = Object.fromEntries(
        [...changed.current].map((key) => [key, String(values[key] || "")]),
      );
      await commitManual(revisedManualMetadata(doc, changedValues));
      setDirty(false);
      changed.current.clear();
      message.success(
        mode === "live"
          ? "人工修订已保存，检索关键词与问题已同步更新"
          : "模拟人工修订已保存",
      );
    } catch (error) {
      message.error(
        error instanceof Error ? error.message : "保存失败，请检查字段长度",
      );
    } finally {
      setSaving(false);
    }
  };
  const clearManual = (key?: string) =>
    modal.confirm({
      title: key ? `清除“${key}”的人工覆盖？` : "清除所有人工覆盖？",
      content: `清除后恢复最近一次 AI 抽取值；原文保留。${dirty ? "当前尚未保存的表单修改也会放弃。" : ""}`,
      okText: "恢复 AI 值",
      cancelText: "保留人工修订",
      onOk: async () => {
        const next = { ...manual };
        if (key) delete next[key];
        else Object.keys(next).forEach((field) => delete next[field]);
        try {
          await commitManual(next);
          setDirty(false);
          changed.current.clear();
          message.success("已清除人工覆盖");
        } catch (error) {
          message.error(error instanceof Error ? error.message : "更新失败");
        }
      },
    });
  const regenerate = () =>
    modal.confirm({
      title: successful?.facts?.length
        ? "重新抽取文档元数据？"
        : "抽取文档元数据？",
      content: `${mode === "live" ? "将调用所选 LLM 并产生 Token 用量，结果直接用于元数据筛选与知识检索。" : "将模拟提取示例正文，不调用真实 LLM。"}${preserveManual ? "已保存的人工修订会保留。" : "本次会清除已保存的人工修订，使用新抽取结果覆盖；请确认后继续。"}${dirty ? "未保存的表单修改会放弃。" : ""}`,
      okText: preserveManual ? "保留人工修订并抽取" : "确认覆盖人工修订并抽取",
      cancelText: "取消",
      onOk: async () => {
        setRequesting(true);
        try {
          await extractMetadata(doc.id, preserveManual);
          setDirty(false);
          changed.current.clear();
          message.info(
            mode === "live"
              ? "元数据任务已提交，结果会自动更新"
              : "模拟元数据抽取完成",
          );
        } catch (error) {
          message.error(error instanceof Error ? error.message : "提交失败");
        } finally {
          setRequesting(false);
        }
      },
    });
  return (
    <section className="metadata-workspace">
      <div className="section-heading">
        <div>
          <h3>文档元数据</h3>
          <p className="muted">
            查看抽取结果与原文依据，复核后可人工修订。有效值参与筛选，关键词与问题参与检索。
          </p>
        </div>
        <Space wrap>
          <Tag
            color={
              status === "failed"
                ? "error"
                : status === "completed"
                  ? "success"
                  : busy
                    ? "processing"
                    : "default"
            }
          >
            {statusLabels[status]}
          </Tag>
          {mode === "demo" && <Tag>模拟抽取</Tag>}
          {extraction?.version ? (
            <Tag>提取版本 v{extraction.version}</Tag>
          ) : null}
        </Space>
      </div>
      <div className="metadata-run-info">
        <span>
          模型：
          {extraction?.model ||
            config?.metadataModel ||
            state.models.find((item) => item.capability === "llm")?.model ||
            "尚未选择"}
        </span>
        <span>
          最近抽取：
          {extraction?.extractedAt
            ? timeText(extraction.extractedAt)
            : "尚无成功记录"}
        </span>
        {extraction?.coverage && (
          <span>
            来源覆盖：{extraction.coverage.processedUnits} /{" "}
            {extraction.coverage.totalUnits} 个单元
          </span>
        )}
      </div>
      {busy && (
        <Alert
          type="info"
          showIcon
          title="正在后台抽取元数据"
          description="结果完成后自动更新。上次已生效的元数据与索引继续保留。"
        />
      )}
      {status === "failed" && (
        <Alert
          type="error"
          showIcon
          title={extraction?.error || "元数据抽取失败"}
          description="若已有成功结果，会继续使用。检查模型配置或原文件后可重新抽取。"
        />
      )}
      {extraction?.warnings?.map((warning, index) => (
        <Alert key={index} type="warning" showIcon title={warning} />
      ))}
      {!successful?.facts?.length && !busy && (
        <Alert
          type="info"
          showIcon
          title={
            doc.status === "processing"
              ? "等待文档构建完成"
              : "暂无带原文依据的自动抽取结果"
          }
          description={
            config?.autoMetadata
              ? "开启自动提取的文档会在构建时调用 LLM；已就绪文档也可手动发起抽取。"
              : "可在知识库或文档解析规则中开启构建后自动提取，也可以手动抽取已就绪文档。"
          }
        />
      )}
      {canManage && (
        <div className="metadata-actions">
          <Checkbox
            checked={preserveManual}
            onChange={(event) => setPreserveManual(event.target.checked)}
            disabled={busy || requesting}
          >
            重新抽取时保留人工修订
          </Checkbox>
          <Button
            icon={<ReloadOutlined aria-hidden="true" />}
            onClick={regenerate}
            loading={requesting || busy}
            disabled={
              !(
                (doc.status === "ready" || doc.previousReady) &&
                (doc.segments?.length || 0) > 0
              ) ||
              doc.status === "processing" ||
              busy ||
              requesting ||
              syncing
            }
          >
            重新抽取元数据
          </Button>
          <span className="muted small-text">
            {mode === "live"
              ? "调用 LLM 会产生模型用量，任务与用量记录在入库日志。"
              : "当前仅模拟，不会调用云端 API。"}
          </span>
        </div>
      )}
      <div className="metadata-columns">
        <section className="metadata-effective">
          <div className="section-heading">
            <h3>当前生效值</h3>
            {canManage && (
              <Button
                size="small"
                disabled={!Object.keys(manual).length || busy || syncing}
                onClick={() => clearManual()}
              >
                清除所有人工覆盖
              </Button>
            )}
          </div>
          <p className="muted small-text">
            只将实际修改的字段设为人工覆盖；留空会隐藏对应 AI
            值，清除人工覆盖后可恢复自动值。
          </p>
          <Form
            form={form}
            layout="vertical"
            disabled={!canManage || busy || doc.status === "processing"}
            onValuesChange={(values) => {
              Object.keys(values).forEach((key) => changed.current.add(key));
              setDirty(true);
            }}
          >
            {fields.map((key) => (
              <div className="metadata-field" key={key}>
                <Form.Item
                  name={key}
                  label={
                    <Space>
                      <span>{key}</span>
                      {Object.hasOwn(manual, key) ? (
                        <Tag>
                          人工修订{manual[key] === "" ? " · 已隐藏" : ""}
                        </Tag>
                      ) : automatic[key] ? (
                        <Tag color="blue">AI 提取</Tag>
                      ) : null}
                    </Space>
                  }
                  rules={[
                    {
                      max: key === "可能的问题" ? 3000 : 1000,
                      message: "内容过长，请缩短后保存",
                    },
                  ]}
                >
                  {key === "可能的问题" || key === "关键词" ? (
                    <Input.TextArea
                      rows={key === "可能的问题" ? 3 : 2}
                      placeholder={
                        canManage ? "多项请使用中文分号“；”分隔" : "未发现"
                      }
                    />
                  ) : (
                    <Input placeholder="未发现" />
                  )}
                </Form.Item>
                {canManage && Object.hasOwn(manual, key) && (
                  <Button
                    type="link"
                    size="small"
                    disabled={busy || syncing}
                    onClick={() => clearManual(key)}
                  >
                    清除人工覆盖
                  </Button>
                )}
              </div>
            ))}
            {canManage && (
              <Button
                type="primary"
                onClick={save}
                loading={saving}
                disabled={!dirty || busy || syncing}
              >
                复核并保存人工修订
              </Button>
            )}
          </Form>
        </section>
        <section className="metadata-evidence">
          <h3>AI 抽取结果与依据</h3>
          <p className="muted small-text">
            以下保留 AI 原始提取值。人工覆盖不会改写来源内容或这份证据。
          </p>
          {successful?.facts?.length ? (
            successful.facts.map((fact, index) => (
              <article className="metadata-fact" key={`${fact.field}-${index}`}>
                <div>
                  <Tag color="blue">AI 提取</Tag>
                  <strong>
                    {fact.field === "subject"
                      ? "主体信息"
                      : fact.label || FACT_LABELS[fact.field]}
                  </strong>
                </div>
                <p className="metadata-fact-value">{fact.value}</p>
                <blockquote>{fact.quote || "未返回引用原文"}</blockquote>
                <small>
                  {fact.location || "位置未返回"}
                  {fact.unitId ? ` · ${fact.unitId}` : ""}
                </small>
              </article>
            ))
          ) : (
            <p className="metadata-empty">
              尚无提取事实。没有在原文中发现的日期、版本、主体信息或责任人会保持空白。
            </p>
          )}
          <h4>
            生成关键词 <Tag color="blue">AI 生成</Tag>
          </h4>
          <div className="metadata-generated-tags">
            {successful?.keywords?.length ? (
              successful.keywords.map((keyword, index) => (
                <Tag key={index} color="blue">
                  {keyword}
                </Tag>
              ))
            ) : (
              <span className="muted">尚无生成关键词</span>
            )}
          </div>
          <h4>
            可能回答的问题 <Tag color="blue">AI 生成</Tag>
          </h4>
          {successful?.questions?.length ? (
            <ol className="metadata-generated-questions">
              {successful.questions.map((question, index) => (
                <li key={index}>{question}</li>
              ))}
            </ol>
          ) : (
            <p className="muted">尚无生成问题</p>
          )}
        </section>
      </div>
    </section>
  );
}
