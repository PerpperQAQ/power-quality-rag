import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  DatePicker,
  Descriptions,
  Drawer,
  Input,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Timeline,
} from "antd";
import type { GetProps, TableColumnsType } from "antd";
import {
  ArrowRightOutlined,
  ClockCircleOutlined,
  FileSearchOutlined,
  InfoCircleOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useStore } from "../store-context";
import { EvaluationCaseModal } from "../EvaluationCaseModal";
import { AgentTrace } from "../AgentTrace";
import type { RunLog } from "../types";
import { numberText, timeText } from "../demo";
import {
  Blank,
  DemoNote,
  PageHeading,
  RunStatusTag,
  SourceDrawer,
} from "../components";

export function Logs() {
  const { state, mode } = useStore();
  const [params] = useSearchParams();
  const [type, setType] = useState(
    params.get("type") || (state.role === "manager" ? "ingestion" : "chat"),
  );
  const [search, setSearch] = useState(params.get("search") || "");
  const [status, setStatus] = useState("all");
  const [detailId, setDetailId] = useState<string | null>(params.get("runId"));
  const [source, setSource] = useState(false);
  const [collectCase, setCollectCase] = useState(false);
  const detail = state.logs.find((l) => l.id === detailId);
  const matchingMessage = state.conversations.flatMap((c) => c.messages).find((m) => m.runId === detail?.id);
  const output = detail?.output ?? (matchingMessage?.text && matchingMessage.text !== matchingMessage.error && matchingMessage.text !== "已停止生成。" ? matchingMessage.text : undefined);
  const effectiveType = type;
  const [pickerValue, setPickerValue] =
    useState<GetProps<typeof DatePicker.RangePicker>["value"]>(null);
  const [dateRange, setDateRange] = useState<[string, string] | null>(null);
  const filtered = useMemo(
    () =>
      state.logs.filter(
        (l) =>
          l.kind === type &&
          (status === "all" || l.status === status) &&
          `${l.query}${l.assistantName}${l.model}`
            .toLowerCase()
            .includes(search.toLowerCase()) &&
          (!dateRange ||
            (l.createdAt >= dateRange[0] && l.createdAt <= dateRange[1])),
      ),
    [state.logs, state.role, type, status, search, dateRange],
  );
  const total = filtered.reduce((sum, l) => sum + (l.tokens || 0), 0);
  const unknown = filtered.filter((l) => l.tokens == null).length;
  const durations = filtered
    .filter((l) => l.status !== "running")
    .map((l) => l.duration)
    .sort((a, b) => a - b);
  const p95 = durations.length
    ? durations[Math.max(0, Math.ceil(durations.length * 0.95) - 1)]
    : 0;
  const columns: TableColumnsType<RunLog> = [
    {
      title:
        effectiveType === "ingestion"
          ? "文档 / 任务"
          : effectiveType === "model"
            ? "测试内容"
            : "查询内容",
      dataIndex: "query",
      width: "30%",
      render: (_, l) => (
        <button className="log-query" onClick={() => setDetailId(l.id)}>
          <strong>{l.query}</strong>
          <small>{l.assistantName}</small>
        </button>
      ),
    },
    {
      title: "模型",
      dataIndex: "model",
      width: 160,
      render: (value) => <span className="small-text">{value}</span>,
    },
    {
      title: "Token 用量",
      dataIndex: "tokens",
      width: 120,
      render: (value: number | null) =>
        value == null ? (
          <span className="unknown-usage">
            未知 <InfoCircleOutlined aria-hidden="true" />
          </span>
        ) : (
          <span className="tabular">{numberText(value)}</span>
        ),
    },
    {
      title: "使用时长",
      dataIndex: "duration",
      width: 105,
      render: (v: number, l) =>
        l.status === "running" ? "执行中" : `${v.toFixed(1)}s`,
    },
    {
      title: "状态",
      width: 100,
      render: (_, l) => <RunStatusTag status={l.status} />,
    },
    {
      title: "时间",
      width: 135,
      render: (_, l) => (
        <span className="muted small-text">{timeText(l.createdAt)}</span>
      ),
    },
    {
      title: "",
      width: 75,
      render: (_, l) => (
        <Button type="link" size="small" onClick={() => setDetailId(l.id)}>
          详情
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeading
        eyebrow="OBSERVABILITY"
        title="日志监测"
        description="从一次查询到每个处理阶段，看清用量、耗时和异常原因。"
        actions={
          <Space wrap>
            <Tag className="header-neutral-tag">{mode === "live" ? "实际调用日志" : "所有用量均为演示"}</Tag>
            <Link to="/observability"><Button>可观测中心</Button></Link>
          </Space>
        }
      />
      {params.get("runId") && !state.logs.some(l => l.id === params.get("runId")) && <Alert type="info" showIcon title="原始日志不存在或当前角色无法查看。案例快照仍可在评测集中查看。" style={{marginBottom: 18}} />}
      <div className="log-metrics">
        <div>
          <span>匹配记录数</span>
          <strong>
            {filtered.length}
            <small>次</small>
          </strong>
          <p>按当前筛选范围统计</p>
        </div>
        <div>
          <span>
            {mode === "live" ? "已返回的 Token 用量" : "已知演示用量"}
          </span>
          <strong>
            {filtered.length && unknown === filtered.length
              ? "未知"
              : numberText(total)}
            <small>Token</small>
          </strong>
          <p>
            {unknown
              ? `${unknown} 次用量未知，未计入合计`
              : mode === "live"
                ? "仅统计服务商返回的用量"
                : "全部匹配记录已返回演示用量"}
          </p>
        </div>
        <div>
          <span>P95 使用时长</span>
          <strong>
            {durations.length ? p95.toFixed(1) : "—"}
            <small>秒</small>
          </strong>
          <p>不包含正在执行的任务</p>
        </div>
        <div>
          <span>失败与中断</span>
          <strong>
            {
              filtered.filter(
                (l) => l.status === "failed" || l.status === "canceled",
              ).length
            }
            <small>次</small>
          </strong>
          <p>失败、取消也会保留记录</p>
        </div>
      </div>
      <div className="logs-panel">
        <div className="log-tabs">
          <Segmented
            value={type}
            onChange={(v) => {
              setType(String(v));
              setSearch("");
              setStatus("all");
            }}
            options={
              state.role === "manager"
                ? [{ label: "对话查询", value: "chat" }, { label: "入库任务", value: "ingestion" }]
                : [
                    { label: "对话查询", value: "chat" },
                    { label: "入库任务", value: "ingestion" },
                    { label: "模型测试", value: "model" },
                  ]
            }
          />
          <span className="muted small-text">
            用量来源：
            {mode === "live" ? "服务商响应（缺失记为未知）" : "模拟数据"}
          </span>
        </div>
        <div className="log-filters">
          <Space wrap>
            <Input
              prefix={<SearchOutlined aria-hidden="true" />}
              placeholder="搜索问题、文档或模型"
              aria-label="搜索日志"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              allowClear
              style={{ width: 270 }}
            />
            <Select
              aria-label="日志状态"
              value={status}
              onChange={setStatus}
              style={{ width: 130 }}
              options={[
                { value: "all", label: "全部状态" },
                { value: "completed", label: "已完成" },
                { value: "failed", label: "失败" },
                { value: "no_evidence", label: "依据不足" },
                { value: "canceled", label: "已停止" },
                { value: "running", label: "执行中" },
              ]}
            />
            <DatePicker.RangePicker
              aria-label="日志日期范围"
              value={pickerValue}
              onChange={(values) => {
                setPickerValue(values);
                setDateRange(
                  values?.[0] && values?.[1]
                    ? [
                        values[0].startOf("day").toISOString(),
                        values[1].endOf("day").toISOString(),
                      ]
                    : null,
                );
              }}
            />
          </Space>
          {(search || status !== "all" || dateRange) && (
            <Button
              type="text"
              onClick={() => {
                setSearch("");
                setStatus("all");
                setDateRange(null);
                setPickerValue(null);
              }}
            >
              清除筛选
            </Button>
          )}
        </div>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={filtered}
          scroll={{ x: 980 }}
          pagination={{
            pageSize: 8,
            showSizeChanger: false,
            hideOnSinglePage: true,
            showTotal: (count) => `共 ${count} 条记录`,
          }}
          locale={{
            emptyText: (
              <Blank
                title="暂无匹配记录"
                description="调整筛选条件，或完成一次对话、入库、模型测试。"
              />
            ),
          }}
        />
      </div>
      <DemoNote />
      <Drawer
        title={
          <span>
            <FileSearchOutlined aria-hidden="true" /> 执行详情
          </span>
        }
        open={!!detail}
        onClose={() => setDetailId(null)}
        size={620}
      >
        {detail && (
          <>
            <div className="run-detail-heading">
              <RunStatusTag status={detail.status} />
              <Tag variant="filled">
                {mode === "live" ? "实际执行" : "演示记录"}
              </Tag>
              <h2>{detail.query}</h2>
              <p>
                {detail.applicationName || detail.assistantName} · {timeText(detail.createdAt)}
              </p>
            </div>
            {detail.error && (
              <Alert type="error" showIcon title={detail.error} description={detail.errorCode ? `错误代码：${detail.errorCode}` : undefined} />
            )}
            <Descriptions
              column={2}
              size="small"
              items={[
                {
                  key: "model",
                  label: "模型",
                  children: detail.model,
                  span: 2,
                },
                {
                  key: "input",
                  label: "输入 Token",
                  children:
                    detail.inputTokens == null
                      ? "未知"
                      : numberText(detail.inputTokens),
                },
                {
                  key: "output",
                  label: "输出 Token",
                  children:
                    detail.outputTokens == null
                      ? "未知"
                      : numberText(detail.outputTokens),
                },
                {
                  key: "tokens",
                  label: "总 Token",
                  children:
                    detail.tokens == null
                      ? "未知（不视为 0）"
                      : numberText(detail.tokens),
                },
                {
                  key: "duration",
                  label: "总耗时",
                  children: `${detail.duration.toFixed(1)}s`,
                },
                {
                  key: "version",
                  label: "配置版本",
                  children: `v${detail.configVersion}`,
                },
                {
                  key: "usage-source",
                  label: "用量来源",
                  children: mode === "live" ? "服务商响应" : "前端模拟",
                },
                ...(detail.attempt ? [{ key: "attempt", label: "任务尝试", children: `第 ${detail.attempt} 次` }] : []),
                ...(detail.kind === "ingestion" ? [{ key: "index", label: "索引发布", children: detail.indexPublished === true ? "本次已发布" : detail.indexPublished === false ? "本次未发布" : "历史记录未采集" }, { key: "previous", label: "任务开始时的已发布索引块", children: detail.previousIndexAvailable === true ? "存在" : detail.previousIndexAvailable === false ? "不存在" : "历史记录未采集" }] : []),
                {
                  key: "run",
                  label: "运行编号",
                  children: <code className="run-id">{detail.id}</code>,
                  span: 2,
                },
              ]}
            />
            <div className="detail-section-title">
              <ClockCircleOutlined aria-hidden="true" />
              <h3>执行阶段</h3>
            </div>
            {detail.stages.length ? (
              <Timeline
                items={detail.stages.map((s) => ({
                  color:
                    s.status === "failed"
                      ? "red"
                      : s.status === "running" ? "blue" : s.status === "ok" ? "green" : "gray",
                  content: (
                    <div className="stage-row">
                      <strong>{s.name}</strong>
                      <span>
                        {s.status === "running" ? "执行中" : s.status === "skipped" ? "未执行" : s.status === "canceled" || s.status === "interrupted" ? "耗时未记录" : `${s.ms} ms`}{s.attempt ? ` · 第 ${s.attempt} 次尝试` : ""}
                      </span>
                      {(s.status === "canceled" || s.status === "interrupted") && <Tag>{s.status === "canceled" ? "已取消" : "执行中断"}</Tag>}
                      {s.status === "failed" && (
                        <Tag color="error" variant="filled">
                          失败
                        </Tag>
                      )}
                    </div>
                  ),
                }))}
              />
            ) : (
              <p className="muted">任务尚在执行，结束后记录阶段信息。</p>
            )}
            {(detail.executionType === "agent" || detail.executionType === "workflow") && <AgentTrace events={detail.traceEvents || []} />}
            {detail.kind === "chat" && <>
              <div className="detail-section-title"><FileSearchOutlined aria-hidden="true" /><h3>当次输入与输出</h3></div>
              <p className="muted">输入</p><pre className="actual-source-content">{detail.query || "未记录"}</pre>
              <p className="muted">{detail.status === "completed" || detail.status === "no_evidence" ? "输出" : "已收到的输出"}</p>
              <pre className="actual-source-content">{output || "未记录输出，历史记录可能未保存快照。"}</pre>
            </>}
            <div className="detail-section-title"><ThunderboltOutlined aria-hidden="true" /><h3>调用用量明细</h3></div>
            {detail.usage?.length ? <div className="log-usage-list">{detail.usage.map((u, i) => <div key={i}><strong>{u.kind || "调用"} · {u.model || "模型未记录"}</strong><span>输入 {u.inputTokens == null ? "未知" : numberText(u.inputTokens)} / 输出 {u.outputTokens == null ? "未知" : numberText(u.outputTokens)} / 总计 {u.tokens == null ? "未知" : numberText(u.tokens)} Token</span>{u.cachedTokens != null && <small>缓存命中：{numberText(u.cachedTokens)} Token（属于输入量）</small>}{u.requestId && <small>请求 ID：{u.requestId}</small>}</div>)}</div> : <p className="muted">没有可用的逐次用量明细；历史记录可能未采集。</p>}
            <p className="muted small-text">费用估算与导入账单请查看可观测中心；估算不等于实际账单，未知用量不按零计算。</p>
            <div className="detail-section-title">
              <FileSearchOutlined aria-hidden="true" />
              <h3>检索依据</h3>
            </div>
            {detail.citations.length ? (
              <>
                <p className="muted">
                  命中 {detail.citations.length}{" "}
                  条检索片段，已保留当次内容快照。
                </p>
                <Button
                  icon={<ArrowRightOutlined aria-hidden="true" />}
                  onClick={() => setSource(true)}
                >
                  查看当次来源
                </Button>
              </>
            ) : (
              <p className="muted">此记录没有可查看的引用快照。</p>
            )}
            {mode === "live" && detail.kind === "chat" && detail.purpose !== "evaluation" && detail.status !== "running" && <Button style={{margin: "18px 0"}} onClick={() => setCollectCase(true)}>收录到评测集</Button>}
            <div className="usage-callout">
              <ThunderboltOutlined aria-hidden="true" />
              <p>
                {mode === "live" ? "用量来自服务商响应；未返回的项目标记为未知，已发生的失败或取消调用也会保留记录。" : "当前为演示用量，真实后端模式记录服务商返回的实际用量。"}
              </p>
            </div>
          </>
        )}
      </Drawer>
      <EvaluationCaseModal log={detail} open={collectCase} onClose={() => setCollectCase(false)} />
      <SourceDrawer
        citations={detail?.citations || []}
        open={source}
        onClose={() => setSource(false)}
      />
    </>
  );
}
