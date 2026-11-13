import { useState } from "react";
import type { ReactNode } from "react";
import {
  Alert,
  App as AntApp,
  Button,
  Drawer,
  Empty,
  Modal,
  Progress,
  Segmented,
  Tag,
  Tooltip,
} from "antd";
import {
  ArrowRightOutlined,
  BookOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  CodeOutlined,
  DeploymentUnitOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  FileMarkdownOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  FileWordOutlined,
  FolderOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  ReadOutlined,
  TeamOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type { Citation, DocStatus, KnowledgeDoc, RunStatus } from "./types";
import { sizeText, timeText } from "./demo";
import { useStore } from "./store-context";

export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="heading-actions">{actions}</div>
    </div>
  );
}
export function KBIcon({
  color = "blue",
  icon = "folder",
  small = false,
}: {
  color?: string;
  icon?: string;
  small?: boolean;
}) {
  const Icon =
    icon === "team"
      ? TeamOutlined
      : icon === "code"
        ? CodeOutlined
        : icon === "brand"
          ? ReadOutlined
          : FolderOutlined;
  return (
    <span className={`kb-icon ${color} ${small ? "small" : ""}`}>
      <Icon aria-hidden="true" />
    </span>
  );
}
export function FileIcon({
  format,
  small = false,
}: {
  format: string;
  small?: boolean;
}) {
  const type =
    format === "PDF"
      ? "pdf"
      : ["DOC", "DOCX"].includes(format)
        ? "word"
        : ["XLS", "XLSX", "CSV"].includes(format)
          ? "sheet"
          : ["PNG", "JPG", "JPEG", "WEBP"].includes(format)
            ? "image"
            : "md";
  const Icon =
    type === "pdf"
      ? FilePdfOutlined
      : type === "word"
        ? FileWordOutlined
        : type === "sheet"
          ? FileExcelOutlined
          : type === "image"
            ? FileImageOutlined
            : FileMarkdownOutlined;
  return (
    <span className={`file-icon ${type} ${small ? "small" : ""}`}>
      <Icon aria-hidden="true" />
    </span>
  );
}
export function DocStatusTag({
  status,
  progress,
}: {
  status: DocStatus;
  progress?: number;
}) {
  const labels = {
    uploaded: "待构建",
    ready: "已就绪",
    processing: "构建中",
    failed: "解析失败",
    review: "需检查",
    canceled: "已取消",
  };
  return (
    <span className={`status-label ${status}`}>
      {status === "ready" ? (
        <CheckCircleFilled aria-hidden="true" />
      ) : status === "processing" ? (
        <LoadingOutlined aria-hidden="true" />
      ) : status === "failed" || status === "review" ? (
        <WarningOutlined aria-hidden="true" />
      ) : (
        <ClockCircleOutlined aria-hidden="true" />
      )}
      {labels[status]}
      {status === "processing" && <small>{progress || 0}%</small>}
    </span>
  );
}
export function RunStatusTag({ status }: { status: RunStatus }) {
  const labels = {
    completed: "已完成",
    running: "执行中",
    failed: "失败",
    canceled: "已停止",
    no_evidence: "依据不足",
  };
  const colors = {
    completed: "success",
    running: "processing",
    failed: "error",
    canceled: "default",
    no_evidence: "warning",
  };
  return (
    <Tag color={colors[status]} variant="filled">
      {labels[status]}
    </Tag>
  );
}
export function Blank({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="blank">
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <>
            <h3>{title}</h3>
            <p>{description}</p>
          </>
        }
      />
      {action}
    </div>
  );
}
export function DemoNote({ compact = false }: { compact?: boolean }) {
  const { mode } = useStore();
  return (
    <div className={`demo-note ${compact ? "compact" : ""}`}>
      <InfoCircleOutlined aria-hidden="true" />
      <span>
        {mode === "live"
          ? "结果来自本机后端 · 用量以服务商实际返回为准，缺失记为未知"
          : compact
            ? "演示结果 · 未调用真实模型"
            : "前端交互原型：文件解析、模型响应与用量均为演示数据，不上传文件或调用云端 API。"}
      </span>
    </div>
  );
}

export function SourceDocument({ citation }: { citation: Citation }) {
  const { mode, downloadDocument } = useStore();
  const { message } = AntApp.useApp();
  if (mode === "live")
    return (
      <div className="source-document">
        <div className="paper-top">
          <span>真实解析内容</span>
          <span>{citation.location}</span>
        </div>
        <h2>{citation.name}</h2>
        <div className="paper-rule" />
        <pre className="actual-source-content">
          {citation.content || "尚未完成解析，请等待构建或检查任务错误。"}
        </pre>
        <div className="paper-footer">
          <Button
            onClick={() =>
              downloadDocument(citation.docId, citation.name).catch(
                (error: Error) => message.error(error.message),
              )
            }
          >
            下载原文件
          </Button>
          <p>文字与表格来自实际解析；图片说明需要结合原文件核对。</p>
        </div>
      </div>
    );
  const lines = citation.content.split("\n");
  const isTable = ["XLS", "XLSX", "CSV"].includes(citation.format);
  const isImage = ["PNG", "JPG", "WEBP", "JPEG"].includes(citation.format);
  return (
    <div className="source-document">
      <div className="paper-top">
        <span>企业资料 / 演示原文</span>
        <span>{citation.location}</span>
      </div>
      <h2>{lines[0]}</h2>
      <div className="paper-rule" />
      {isTable ? (
        <table className="source-table">
          <thead>
            <tr>
              <th>城市级别</th>
              <th>住宿上限（元/晚）</th>
              <th>交通标准</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>一线城市</td>
              <td>600</td>
              <td>高铁二等座</td>
            </tr>
            <tr>
              <td>其他城市</td>
              <td>400</td>
              <td>高铁二等座</td>
            </tr>
          </tbody>
        </table>
      ) : isImage ? (
        <div className="flow-preview">
          {[
            "出差申请",
            "直属负责人审批",
            "预订交通与住宿",
            "提交报销材料",
            "财务审核",
          ].map((step, i) => (
            <div key={step}>
              <div className={`flow-node ${i === 1 ? "accent" : ""}`}>
                {step}
              </div>
              {i < 4 && <span className="flow-arrow">↓</span>}
            </div>
          ))}
        </div>
      ) : (
        lines.slice(1).map((line, i) => (
          <p
            key={i}
            className={
              i === 0 ? "paper-subtitle" : i === 2 ? "highlight-line" : ""
            }
          >
            {line}
          </p>
        ))
      )}
      <div className="paper-footer">
        仅用于前端原型的示例资料 · 内容不代表实际企业制度
      </div>
    </div>
  );
}

export function SourceDrawer({
  citations,
  open,
  onClose,
  initialIndex = 0,
}: {
  citations: Citation[];
  open: boolean;
  onClose: () => void;
  initialIndex?: number;
}) {
  const { mode } = useStore();
  const [active, setActive] = useState<number | null>(null);
  const index = active ?? initialIndex;
  const citation = citations[index] || citations[0];
  return (
    <Drawer
      open={open}
      onClose={() => {
        setActive(null);
        onClose();
      }}
      title={
        <span>
          <BookOutlined aria-hidden="true" /> 回答依据
        </span>
      }
      size={620}
      className="source-drawer"
    >
      {citation ? (
        <>
          <div className="source-file-heading">
            <FileIcon format={citation.format} />
            <div>
              <h3>{citation.name}</h3>
              <p>
                {citation.kbName} · {citation.location}
              </p>
            </div>
          </div>
          {citations.length > 1 && (
            <Segmented
              block
              value={index}
              onChange={(v) => setActive(Number(v))}
              options={citations.map((_, i) => ({
                label: `来源 ${i + 1}`,
                value: i,
              }))}
            />
          )}
          <div className="source-meta">
            <Tag variant="filled">{citation.provenance}</Tag>
            <Tag variant="filled">历史引用快照</Tag>
          </div>
          {citation.provenance === "AI 图像说明" && (
            <Alert
              type="warning"
              showIcon
              title={
                mode === "live"
                  ? "图像说明由模型生成，请下载原文件核对。"
                  : "图像说明由模型生成，需结合原图核对。当前展示演示流程。"
              }
            />
          )}
          <div className="citation-excerpt">
            <h3>检索片段快照</h3>
            <p>{citation.content}</p>
          </div>
          <SourceDocument citation={citation} />
          <DemoNote compact />
        </>
      ) : (
        <Blank
          title="暂无引用资料"
          description="只有命中知识库的回答才会显示引用。"
        />
      )}
    </Drawer>
  );
}

export function DocumentPreview({
  doc,
  kbName,
  open,
  onClose,
  retry,
  canManage,
}: {
  doc?: KnowledgeDoc;
  kbName: string;
  open: boolean;
  onClose: () => void;
  retry?: () => void;
  canManage: boolean;
}) {
  const { mode } = useStore();
  const [tab, setTab] = useState("对照预览");
  if (!doc) return null;
  const citation: Citation = {
    id: doc.id,
    docId: doc.id,
    name: doc.name,
    format: doc.format,
    kbName,
    content: doc.content,
    location: doc.location,
    provenance: doc.provenance,
    score: 0.96,
  };
  return (
    <Modal
      title={
        <span className="preview-title">
          <FileIcon format={doc.format} small />
          {doc.name}
        </span>
      }
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭预览</Button>}
      width={1100}
      className="document-modal"
    >
      <div className="preview-toolbar">
        <Segmented
          value={tab}
          onChange={(v) => setTab(String(v))}
          options={["对照预览", "文档信息"]}
        />
        <DocStatusTag status={doc.status} progress={doc.progress} />
      </div>
      {doc.error && (
        <Alert
          showIcon
          type={doc.status === "review" ? "warning" : "error"}
          title={doc.error}
          action={
            canManage && retry ? (
              <Button size="small" onClick={retry}>
                重试构建
              </Button>
            ) : undefined
          }
        />
      )}
      {doc.status === "processing" ? (
        <div className="processing-preview">
          <DeploymentUnitOutlined aria-hidden="true" />
          <h3>
            {(doc.progress || 0) < 30
              ? "正在解析文档"
              : (doc.progress || 0) < 65
                ? "正在识别文字与图片"
                : "正在构建知识索引"}
          </h3>
          <Progress percent={doc.progress} />
          <p>
            {mode === "live"
              ? "后台正在处理，耗时取决于文件规模与模型服务。"
              : "此处为模拟构建，约 4 秒完成。"}
          </p>
        </div>
      ) : tab === "文档信息" ? (
        <div className="metadata-grid">
          {Object.entries({
            文件名称: doc.name,
            文件格式: doc.format,
            文件大小: sizeText(doc.size),
            来源知识库: kbName,
            内容位置: doc.location,
            提取方式: doc.provenance,
            分块数量: `${doc.chunks} 个`,
            最近更新: timeText(doc.updatedAt),
          }).map(([k, v]) => (
            <div key={k}>
              <span>{k}</span>
              <strong>{v}</strong>
            </div>
          ))}
        </div>
      ) : (
        <div className="preview-columns">
          <SourceDocument citation={citation} />
          <div className="chunk-panel">
            <div className="section-label">
              解析结果{" "}
              <Tag variant="filled">
                {mode === "live" ? "真实分块" : "演示分块"}
              </Tag>
            </div>
            <h3>内容片段</h3>
            <div className="chunk-content">{doc.content}</div>
            <div className="chunk-footer">
              <Tag variant="filled">{doc.provenance}</Tag>
              <span>{doc.location}</span>
            </div>
            <Alert
              type="info"
              showIcon
              title={
                mode === "live"
                  ? "当前内容来自上传文件的实际解析，请核对来源位置与原文件。"
                  : "当前内容为内置样例，不是对上传文件的真实解析。"
              }
            />
          </div>
        </div>
      )}
    </Modal>
  );
}
export function TextLink({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="text-link" onClick={onClick}>
      {children}
      <ArrowRightOutlined aria-hidden="true" />
    </button>
  );
}
export function TinyHelp({ text }: { text: string }) {
  return (
    <Tooltip title={text}>
      <InfoCircleOutlined aria-hidden="true" className="muted" />
    </Tooltip>
  );
}
export function FileName({
  doc,
  onClick,
}: {
  doc: KnowledgeDoc;
  onClick: () => void;
}) {
  return (
    <button className="file-name" onClick={onClick}>
      <FileIcon format={doc.format} small />
      <span>
        <strong>{doc.name}</strong>
        <small>{sizeText(doc.size)}</small>
      </span>
    </button>
  );
}
export { FileTextOutlined };
