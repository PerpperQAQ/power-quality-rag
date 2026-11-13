import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  App as AntApp,
  Avatar,
  Button,
  Dropdown,
  Input,
  Modal,
  Select,
  Tag,
  Tooltip,
} from "antd";
import {
  ArrowRightOutlined,
  BookOutlined,
  CheckOutlined,
  CommentOutlined,
  CopyOutlined,
  DeleteOutlined,
  DeploymentUnitOutlined,
  EditOutlined,
  EllipsisOutlined,
  FileTextOutlined,
  HistoryOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  StopOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useStore } from "../store-context";
import type { Citation, Conversation } from "../types";
import { isRetrievable, now, uid } from "../demo";
import { Blank, FileIcon, SourceDrawer } from "../components";
import { knowledgeSuggestions } from "../chat-suggestions";
import { AnswerMarkdown } from "../AnswerMarkdown";
import { AnswerFeedback } from "../AnswerFeedback";

export function Chat() {
  const { state, update, sendQuestion, stopAnswer, mode } = useStore();
  const { message, modal } = AntApp.useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [selectedAssistant, setSelectedAssistant] = useState(
    params.get("assistant") ||
      state.assistants.find((a) => a.enabled)?.id ||
      "",
  );
  const activeId = params.get("conversation");
  const setActiveId = (id: string | null, assistantId = selectedAssistant) => {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (id) next.set("conversation", id); else next.delete("conversation");
      if (assistantId) next.set("assistant", assistantId);
      return next;
    }, { replace: true });
  };
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [sources, setSources] = useState<Citation[]>([]);
  const [sourceIndex, setSourceIndex] = useState(0);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [rename, setRename] = useState<{ id: string; title: string } | null>(
    null,
  );
  const end = useRef<HTMLDivElement>(null);
  const composing = useRef(false);
  const active = state.conversations.find((c) => c.id === activeId && !c.platformAppId && c.purpose !== "evaluation" && c.purpose !== "debug");
  const visibleConversations = state.conversations.filter((c) => !c.platformAppId && c.purpose !== "evaluation" && c.purpose !== "debug");
  const liveAssistant = state.assistants.find(
    (a) => a.id === (active?.assistantId || selectedAssistant),
  );
  const assistant = active?.assistantSnapshot || liveAssistant;
  const isAssistantEnabled = !!liveAssistant?.enabled;
  const kbIds = active?.kbIds || assistant?.kbIds || [];
  const kbs = state.knowledgeBases.filter((k) => kbIds.includes(k.id));
  const ready = state.documents.some(
    (d) => kbIds.includes(d.kbId) && isRetrievable(d),
  );
  const generating =
    active?.messages.some((m) => m.status === "generating") || false;
  const paramAssistant = params.get("assistant");
  useEffect(() => {
    if (paramAssistant) {
      setSelectedAssistant(paramAssistant);
    }
  }, [paramAssistant]);
  const lastText = active?.messages.at(-1)?.text;
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [lastText, activeId]);
  const newChat = () => {
    setActiveId(null);
    setQuestion("");
  };
  const ask = async (text = question, regenerate = false) => {
    if (!text.trim() || generating || sending) return;
    if (!assistant || !isAssistantEnabled) {
      message.warning("请选择已启用的助手");
      return;
    }
    if (!ready) {
      message.warning("当前知识范围还没有可用资料，请先导入并构建");
      return;
    }
    const needed = [
      "llm",
      "embedding",
      ...(assistant.useRerank ? ["rerank"] : []),
    ];
    if (
      state.models.some(
        (m) => needed.includes(m.capability) && m.status !== "ready",
      )
    ) {
      message.error("所需模型能力尚未就绪，请管理员先完成模型连接测试");
      return;
    }
    let target = active?.id;
    if (!target) {
      target = uid("conversation");
      const conversation: Conversation = {
        id: target,
        assistantId: assistant.id,
        title: text.slice(0, 20),
        messages: [],
        updatedAt: now(),
        configVersion: assistant.configVersion,
        kbIds: [...assistant.kbIds],
        assistantSnapshot: structuredClone(assistant),
      };
      update((p) => ({
        ...p,
        conversations: [conversation, ...p.conversations],
      }));
      setActiveId(target);
    }
    setSending(true);
    try {
      await sendQuestion(target, text.trim(), regenerate);
      setQuestion("");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "发送失败");
    } finally {
      setSending(false);
    }
  };
  const deleteConversation = (c: Conversation) =>
    modal.confirm({
      title: mode === "live" ? "删除这段会话？" : "删除这段演示会话？",
      content: "会话将从列表移除，对应的调用日志保留。",
      okText: "删除会话",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => {
        if (c.id === activeId) setActiveId(null);
        update((p) => ({
          ...p,
          conversations: p.conversations.filter((x) => x.id !== c.id),
        }));
      },
    });
  const suggestions =
    mode === "live" ? knowledgeSuggestions(state.documents, kbIds, assistant?.retrieval?.fileIds).map((s) => ({ ...s, icon: FileTextOutlined })) : assistant?.id === "as-product"
      ? [
          {
            title: "接入产品 API",
            text: "如何配置 API 接口密钥？",
            icon: FileTextOutlined,
          },
          {
            title: "排查限流问题",
            text: "API 返回 429 应该怎么处理？",
            icon: BookOutlined,
          },
        ]
      : assistant?.id === "as-brand"
        ? [
            {
              title: "品牌表达",
              text: "品牌文案有哪些要求？",
              icon: FileTextOutlined,
            },
          ]
        : [
            {
              title: "差旅与报销",
              text: "差旅报销需要哪些附件？",
              icon: FileTextOutlined,
            },
            {
              title: "请假与考勤",
              text: "年假应该怎么申请？",
              icon: BookOutlined,
            },
            {
              title: "新员工入职",
              text: "新员工入职要准备哪些资料？",
              icon: UserOutlined,
            },
          ];
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success("回答已复制");
    } catch {
      message.info("浏览器暂不支持复制，请选择文本复制");
    }
  };
  return (
    <div className="chat-workspace">
      <aside className="conversation-sidebar">
        <Button
          type="primary"
          ghost
          block
          icon={<PlusOutlined aria-hidden="true" />}
          onClick={newChat}
        >
          新建对话
        </Button>
        <div className="conversation-section-label">最近对话</div>
        {!visibleConversations.length ? (
          <div className="empty-conversations">
            <HistoryOutlined aria-hidden="true" />
            <p>你的对话将保存在这里</p>
            <small>从一个问题开始，慢慢积累。</small>
          </div>
        ) : (
          <div className="conversation-list">
            {visibleConversations.map((c) => (
              <div
                key={c.id}
                className={`conversation-item ${activeId === c.id ? "active" : ""}`}
              >
                <button
                  onClick={() => {
                    setActiveId(c.id, c.assistantId);
                    setSelectedAssistant(c.assistantId);
                    setQuestion("");
                  }}
                >
                  <CommentOutlined aria-hidden="true" />
                  <span>{c.title}</span>
                </button>
                <Dropdown
                  trigger={["click"]}
                  menu={{
                    items: [
                      {
                        key: "rename",
                        label: "重命名",
                        icon: <EditOutlined aria-hidden="true" />,
                      },
                      {
                        key: "delete",
                        label: "删除会话",
                        danger: true,
                        icon: <DeleteOutlined aria-hidden="true" />,
                      },
                    ],
                    onClick: ({ key }) =>
                      key === "rename"
                        ? setRename({ id: c.id, title: c.title })
                        : deleteConversation(c),
                  }}
                >
                  <Button
                    type="text"
                    size="small"
                    icon={<EllipsisOutlined aria-hidden="true" />}
                    aria-label={`${c.title}会话操作`}
                  />
                </Dropdown>
              </div>
            ))}
          </div>
        )}
        <div className="conversation-tip">
          <BookOutlined aria-hidden="true" />
          <p>
            每一个答案
            <br />
            都能找到知识的出处。
          </p>
        </div>
      </aside>
      <section className="chat-main">
        <header className="chat-header">
          <div>
            <span className="mini-assistant-mark">
              <DeploymentUnitOutlined aria-hidden="true" />
            </span>
            <Select
              aria-label="选择问答助手"
              variant="borderless"
              value={assistant?.id || undefined}
              placeholder="选择问答助手"
              onChange={(value) => {
                setSelectedAssistant(value);
                setActiveId(null, value);
                setQuestion("");
              }}
              options={state.assistants
                .filter((a) => a.enabled || a.id === active?.assistantId)
                .map((a) => ({
                  value: a.id,
                  label: a.name,
                  disabled: !a.enabled,
                }))}
            />
            <Tag variant="filled">知识库问答</Tag>
          </div>
          <Tooltip
            title={
              mode === "live"
                ? "回答使用所选通义模型，请核对引用来源"
                : "当前为演示回答，不调用云端模型"
            }
          >
            <span className="chat-model">
              <span className="status-dot success" />
              {assistant?.model || "尚未选择模型"}
            </span>
          </Tooltip>
        </header>
        {!assistant ? (
          <Blank
            title="先选择一个知识助手"
            description="助手配置好知识范围后，就可以开始对话。"
            action={
              state.role !== "user" && (
                <Button onClick={() => navigate("/assistants")}>
                  管理问答助手
                </Button>
              )
            }
          />
        ) : (
          <>
            <div className="chat-knowledge-scope">
              <BookOutlined aria-hidden="true" />
              <span>知识范围</span>
              {kbs.length ? (
                kbs.map((k) => (
                  <Tag key={k.id} variant="filled">
                    {k.name}
                  </Tag>
                ))
              ) : (
                <Tag color="warning" variant="filled">
                  暂无知识来源
                </Tag>
              )}
              <span className="scope-version">
                配置 v{active?.configVersion || assistant.configVersion}
                {active && active.configVersion !== assistant.configVersion
                  ? " · 历史会话"
                  : ""}
              </span>
            </div>
            {!isAssistantEnabled && (
              <Alert
                type="warning"
                showIcon
                title="此助手已停用，历史对话仍可查看，请选择其他助手继续。"
              />
            )}
            {!ready && isAssistantEnabled && (
              <Alert
                type="warning"
                showIcon
                title="所选知识库尚无可用资料，请先完成文档导入与构建。"
                action={
                  state.role !== "user" && (
                    <Button
                      size="small"
                      onClick={() =>
                        navigate(
                          kbs[0] ? `/knowledge/${kbs[0].id}` : "/knowledge",
                        )
                      }
                    >
                      查看知识库
                    </Button>
                  )
                }
              />
            )}
            <div
              className={`chat-scroll ${active?.messages.length ? "with-messages" : ""}`}
            >
              {!active?.messages.length ? (
                <div className="chat-welcome">
                  <div className="welcome-logo">
                    <DeploymentUnitOutlined aria-hidden="true" />
                  </div>
                  <div className="eyebrow">YOUR KNOWLEDGE, CONNECTED.</div>
                  <h1>你好，有什么想了解的？</h1>
                  <p>{assistant.greeting}</p>
                  <div className="chat-suggestions">
                    {suggestions.map(({ title, text, icon: Icon }) => (
                      <button
                        key={title}
                        onClick={() => ask(text)}
                        disabled={!isAssistantEnabled || !ready}
                      >
                        <Icon aria-hidden="true" />
                        <strong>{title}</strong>
                        <span>{text}</span>
                        <ArrowRightOutlined aria-hidden="true" />
                      </button>
                    ))}
                  </div>
                  <div className="welcome-note">
                    <CheckOutlined aria-hidden="true" />
                    依据企业资料回答
                    <BookOutlined aria-hidden="true" />
                    来源清晰可追溯
                  </div>
                </div>
              ) : (
                <div className="messages">
                  {active.messages.map((m, i) => (
                    <article className={`message ${m.role}`} key={m.id}>
                      <Avatar
                        size={32}
                        className={
                          m.role === "assistant"
                            ? "assistant-message-avatar"
                            : "human-message-avatar"
                        }
                        icon={
                          m.role === "assistant" ? (
                            <DeploymentUnitOutlined aria-hidden="true" />
                          ) : (
                            <UserOutlined aria-hidden="true" />
                          )
                        }
                      />
                      <div className="message-body">
                        <div className="message-sender">
                          {m.role === "user" ? "你" : assistant.name}
                          {m.role === "assistant" && (
                            <span>
                              {mode === "live" ? "模型回答" : "演示回答"}
                            </span>
                          )}
                        </div>
                        {m.text ? (
                          <div className="answer-prose">
                            {m.role === "assistant" ? <AnswerMarkdown text={m.text} /> : m.text.split("\n").map((line, index) => (
                              <p key={index}>{line || "\u00a0"}</p>
                            ))}
                            {m.status === "generating" && (
                              <span className="typing-caret" />
                            )}
                          </div>
                        ) : m.status === "generating" ? (
                          <div className="thinking">
                            <span />
                            <span />
                            <span />
                            <small>正在查找资料…</small>
                          </div>
                        ) : (
                          <p className="muted">回答已停止，尚未收到内容。</p>
                        )}
                        {m.role === "assistant" &&
                          m.citations.length > 0 &&
                          assistant?.showQuote !== false &&
                          m.status !== "generating" && (
                            <div className="message-citations">
                              <div className="citation-label">
                                参考了{" "}
                                {new Set(m.citations.map((c) => c.docId)).size}{" "}
                                份知识资料
                              </div>
                              {m.citations.map((c, index) => (
                                <button
                                  key={c.id}
                                  onClick={() => {
                                    setSources(m.citations);
                                    setSourceIndex(index);
                                    setSourceOpen(true);
                                  }}
                                >
                                  <span>{index + 1}</span>
                                  <FileIcon format={c.format} small />
                                  <strong>{c.name}</strong>
                                  <ArrowRightOutlined aria-hidden="true" />
                                </button>
                              ))}
                            </div>
                          )}
                        {m.status === "canceled" && (
                          <span className="stopped-label">
                            <StopOutlined aria-hidden="true" />
                            已停止生成，内容未完成
                          </span>
                        )}
                        {m.status === "failed" && (
                          <Alert
                            type="error"
                            showIcon
                            title={m.error || "回答未完成，请检查调用日志后重试。"}
                          />
                        )}
                        {m.role === "assistant" &&
                          m.status !== "generating" && (
                            <div className="message-actions">
                              <Tooltip title="复制回答">
                                <Button
                                  type="text"
                                  size="small"
                                  icon={<CopyOutlined aria-hidden="true" />}
                                  aria-label="复制回答"
                                  onClick={() => copy(m.text)}
                                  disabled={!m.text}
                                />
                              </Tooltip>
                              {i === active.messages.length - 1 && (
                                <Button
                                  type="text"
                                  size="small"
                                  icon={<ReloadOutlined aria-hidden="true" />}
                                  disabled={
                                    generating || !isAssistantEnabled || !ready
                                  }
                                  onClick={() => {
                                    const q = active.messages
                                      .filter((msg) => msg.role === "user")
                                      .at(-1)?.text;
                                    if (q) ask(q, true);
                                  }}
                                >
                                  重新生成
                                </Button>
                              )}
                              {m.runId && (m.status === "completed" || m.status === "no_evidence") && <AnswerFeedback runId={m.runId} />}
                              <span>
                                {m.status === "no_evidence"
                                  ? "未找到足够依据"
                                  : mode === "live" ? "依据知识库原文回答" : "依据示例知识回答"}
                              </span>
                            </div>
                          )}
                      </div>
                    </article>
                  ))}
                </div>
              )}
              <div ref={end} />
            </div>
            <div className="composer-container">
              <div
                className={`chat-composer ${generating ? "generating" : ""}`}
              >
                <Input.TextArea
                  aria-label="输入问题"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onCompositionStart={() => {
                    composing.current = true;
                  }}
                  onCompositionEnd={() => {
                    composing.current = false;
                  }}
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing &&
                      !composing.current
                    ) {
                      e.preventDefault();
                      ask();
                    }
                  }}
                  placeholder={
                    ready
                      ? "向所选知识库提问，回答会附上资料来源"
                      : "请先为助手准备可用的知识资料"
                  }
                  autoSize={{ minRows: 2, maxRows: 5 }}
                  maxLength={2000}
                  disabled={!isAssistantEnabled || !ready}
                />
                <div className="composer-bottom">
                  <span>
                    <BookOutlined aria-hidden="true" />
                    仅依据所选知识库回答
                  </span>
                  {generating ? (
                    <Button
                      aria-label="停止生成"
                      icon={<StopOutlined aria-hidden="true" />}
                      onClick={() => activeId && stopAnswer(activeId)}
                    >
                      停止生成
                    </Button>
                  ) : (
                    <Button
                      type="primary"
                      aria-label="发送问题"
                      icon={<SendOutlined aria-hidden="true" />}
                      loading={sending}
                      disabled={
                        !question.trim() || !ready || !isAssistantEnabled
                      }
                      onClick={() => ask()}
                    >
                      发送
                    </Button>
                  )}
                </div>
              </div>
              <p className="chat-disclaimer">
                {mode === "live"
                  ? "回答来自模型生成，请核对来源。"
                  : "原型使用示例资料和模拟回答，请核对来源。"}
                Enter 发送，Shift + Enter 换行。
              </p>
            </div>
          </>
        )}
      </section>
      <SourceDrawer
        citations={sources}
        initialIndex={sourceIndex}
        open={sourceOpen}
        onClose={() => setSourceOpen(false)}
      />
      <Modal
        open={!!rename}
        title="重命名会话"
        onCancel={() => setRename(null)}
        onOk={() => {
          if (!rename?.title.trim()) {
            message.warning("请输入会话名称");
            return;
          }
          update((p) => ({
            ...p,
            conversations: p.conversations.map((c) =>
              c.id === rename.id ? { ...c, title: rename.title.trim() } : c,
            ),
          }));
          setRename(null);
        }}
        okText="保存"
        cancelText="取消"
      >
        <Input
          aria-label="会话名称"
          value={rename?.title || ""}
          onChange={(e) =>
            setRename((p) => (p ? { ...p, title: e.target.value } : p))
          }
          maxLength={40}
        />
      </Modal>
    </div>
  );
}
