import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Descriptions, Drawer, Input, Segmented, Select, Space, Table, Tag } from "antd";
import { ArrowLeftOutlined, SearchOutlined } from "@ant-design/icons";
import { PageHeading } from "../components";

type Domain = "agent" | "build" | "chat" | "cost";
type Status = "成功" | "失败" | "运行中" | "已取消";
type Event = { id: string; kind: string; name: string; status: Status; duration: string; input: string; output: string; context: string; usage: string; error?: string };
type Sample = { id: string; name: string; subject: string; status: Status; time: string; duration: string; usage: string; detail: string; events: Event[] };
const agents: Sample[] = [
  {id:"tr_demo_001",name:"政策问答 Agent",subject:"出差深圳的住宿标准是多少？",status:"成功",time:"10-28 10:24:18",duration:"4.2 s",usage:"2,480 Token",detail:"会话 conv_demo_01 · 配置 v3 · 入口：工作台",events:[
    {id:"s1",kind:"Agent",name:"接收请求",status:"成功",duration:"8 ms",input:"出差深圳的住宿标准是多少？",output:"进入政策问答流程",context:"会话 conv_demo_01；Agent 配置 v3；用户身份：演示用户",usage:"不适用"},
    {id:"s2",kind:"Skill",name:"差旅政策查询",status:"成功",duration:"42 ms",input:"问题：出差深圳的住宿标准是多少？",output:"调用知识库检索工具",context:"Skill 版本 v1.2；指令模板已脱敏；关联父节点 s1",usage:"未单独计量"},
    {id:"s3",kind:"工具",name:"知识库检索",status:"成功",duration:"682 ms",input:"关键词：深圳 住宿标准",output:"召回 3 条，重排后保留 1 条；差旅制度 §4.2",context:"知识库：员工制度；索引版本 idx_demo_02；Top K = 3",usage:"Embedding 160 Token；重排用量未知"},
    {id:"s4",kind:"模型",name:"生成回答",status:"成功",duration:"3.4 s",input:"问题 + 已选历史消息 + 检索片段（演示快照）",output:"深圳住宿标准以差旅制度中的职级标准为准。",context:"模型 qwen-demo；提示词 v3；上下文由系统指令、2 条历史消息和 1 条检索片段组成。",usage:"输入 1,900 / 输出 420 / 总计 2,320 Token；服务商返回"},
  ]},
  {id:"tr_demo_002",name:"资料整理 Agent",subject:"整理合同附件的主要条款",status:"失败",time:"10-28 09:51:02",duration:"12.0 s",usage:"未知",detail:"会话 conv_demo_02 · 配置 v2 · 入口：API",events:[
    {id:"f1",kind:"Agent",name:"接收请求",status:"成功",duration:"10 ms",input:"整理合同附件的主要条款",output:"进入资料整理流程",context:"会话 conv_demo_02；Agent 配置 v2",usage:"不适用"},
    {id:"f2",kind:"工具",name:"读取附件",status:"失败",duration:"12.0 s",input:"附件 doc_demo_08",output:"未获取到附件内容",context:"工具版本 v1；第 2 次尝试；超时阈值 12 秒",usage:"未知：调用中断未返回用量",error:"tool_timeout：工具调用超时。已重试 1 次；是否产生上游费用未知。"},
  ]},
];
const builds: Sample[] = [{id:"job_demo_01",name:"员工制度",subject:"差旅管理办法.pdf",status:"失败",time:"10-28 09:35:00",duration:"18.6 s",usage:"1,120 Token + OCR 用量未知",detail:"文档版本 v2 · 第 2 次尝试 · 旧索引仍可检索",events:[
{id:"b1",kind:"构建",name:"解析与 OCR",status:"成功",duration:"6.1 s",input:"差旅管理办法.pdf · 12 页",output:"解析 12 页；2 页需人工复核",context:"解析规则 v2；OCR 模型 ocr-demo",usage:"OCR 页数与费用未返回"},
{id:"b2",kind:"构建",name:"分块与元数据",status:"成功",duration:"3.2 s",input:"已解析文本 12 页",output:"生成 32 个分块",context:"切分规则：按标题；元数据配置 v2",usage:"LLM 1,120 Token"},
{id:"b3",kind:"构建",name:"向量化与索引发布",status:"失败",duration:"9.3 s",input:"32 个待索引分块",output:"候选索引未发布；保留旧索引",context:"索引代次 idx_demo_03；失败批次 2/4",usage:"未知：部分调用未返回",error:"embedding_timeout：向量化超时，候选索引未发布。"}]}];
const chats: Sample[] = [{id:"chat_demo_01",name:"政策问答助手",subject:"出差深圳的住宿标准是多少？",status:"成功",time:"10-28 10:24:18",duration:"4.2 s",usage:"2,480 Token",detail:"会话 conv_demo_01 · 第 3 轮 · 有引用 · 用户反馈：未反馈",events:agents[0].events}];
const data: Record<Exclude<Domain,"cost">, Sample[]> = { agent: agents, build: builds, chat: chats };
const statusColor: Record<Status,string> = {成功:"success",失败:"error",运行中:"processing",已取消:"default"};
const labels: Record<Domain,string> = {agent:"Agent 调用",build:"知识库构建",chat:"对话日志",cost:"用量与费用"};

export function ObservabilityDesign() {
 const [domain,setDomain]=useState<Domain>("agent");
 const [query,setQuery]=useState("");
 const [status,setStatus]=useState("全部状态");
 const [sample,setSample]=useState<Sample | null>(null);
 const [eventId,setEventId]=useState("");
 const [detailTab,setDetailTab]=useState("输入输出");
 const currentEvent=sample?.events.find(e=>e.id===eventId) || sample?.events[0];
 const rows=domain === "cost" ? [] : data[domain].filter(r => (status === "全部状态" || r.status === status) && `${r.id} ${r.name} ${r.subject}`.toLowerCase().includes(query.toLowerCase()));
 const open=(row:Sample)=>{setSample(row);setEventId(row.events[0]?.id || "");setDetailTab("输入输出")};
 const switchDomain=(value:string)=>{setDomain(value as Domain);setQuery("");setStatus("全部状态")};
 return <div className="obs-design">
  <PageHeading eyebrow="OBSERVABILITY · DESIGN" title="可观测中心" description="从构建、调用到消耗，定位一次执行发生了什么。" actions={<Link to="/logs"><Button icon={<ArrowLeftOutlined />}>返回现有日志</Button></Link>} />
  <Alert showIcon type="info" title="设计原型 · 以下均为模拟数据" description="用于评审信息架构和排障流程，尚未接入真实 Agent、Skill、费用或链路数据。现有真实日志请通过右上角入口查看。" />
  <div className="obs-header"><Segmented aria-label="观测类型" value={domain} onChange={switchDomain} options={Object.entries(labels).map(([value,label])=>({value,label}))} /></div>
  {domain !== "cost" ? <>
   <div className="obs-summary"><div><span>示例记录</span><strong>{rows.length}</strong><small>随筛选条件变化</small></div><div><span>失败记录</span><strong>{rows.filter(r=>r.status==="失败").length}</strong><small>可查看失败节点</small></div><div><span>用量未知</span><strong>{rows.filter(r=>r.usage.includes("未知")).length}</strong><small>未知不按 0 计</small></div></div>
   <div className="obs-panel"><div className="obs-panel-head"><div><h2>{labels[domain]}</h2><p>{domain === "agent" ? "查看单次运行、节点调用和上下文快照。" : domain === "build" ? "按文档和任务查看构建阶段及索引发布结果。" : "从对话轮次关联执行链路与检索依据。"}</p></div></div>
    <Space wrap className="obs-filters"><Input aria-label="搜索示例记录" prefix={<SearchOutlined />} placeholder="搜索名称、内容或 ID" value={query} onChange={e=>setQuery(e.target.value)} allowClear /><Select aria-label="筛选状态" value={status} onChange={setStatus} style={{width:135}} options={["全部状态","成功","失败","运行中","已取消"].map(v=>({value:v,label:v}))} /></Space>
    <Table<Sample> rowKey="id" dataSource={rows} pagination={false} scroll={{x:830}} locale={{emptyText:"没有匹配的示例记录"}} columns={[
     {title:domain === "build" ? "文档 / 知识库" : domain === "chat" ? "问题 / 助手" : "输入 / Agent",key:"subject",render:(_,r)=><button className="obs-record" onClick={()=>open(r)}><strong>{r.subject}</strong><small>{r.name} · {r.id}</small></button>},
     {title:"状态",dataIndex:"status",render:(v:Status)=><Tag color={statusColor[v]}>{v}</Tag>}, {title:"用量",dataIndex:"usage"}, {title:"耗时",dataIndex:"duration"}, {title:"开始时间",dataIndex:"time"}, {title:"",key:"action",render:(_,r)=><Button type="link" onClick={()=>open(r)}>查看详情</Button>}
    ]} />
   </div>
  </> : <><div className="obs-summary"><div><span>已知用量</span><strong>2,480 <small>Token</small></strong><small>仅含演示中已返回用量</small></div><div><span>预估费用</span><strong>¥ 0.012</strong><small>示例单价计算，非账单</small></div><div><span>费用未知</span><strong>2 <small>项</small></strong><small>未返回用量或缺少价格</small></div></div><div className="obs-panel obs-cost"><h2>按调用类型查看</h2><p>费用、Token 与其他计费单位分别展示，未知项不计入已知合计。</p><Table rowKey="kind" pagination={false} scroll={{x:700}} dataSource={[{kind:"对话 / Agent 模型",usage:"2,320 Token",price:"示例价 ¥5.17 / 百万 Token",cost:"¥0.012（预估）"},{kind:"Embedding",usage:"160 Token",price:"未配置价格",cost:"未知"},{kind:"OCR / 重排",usage:"服务商未返回",price:"按服务商计费单位",cost:"未知"}]} columns={[{title:"类型",dataIndex:"kind"},{title:"已知用量",dataIndex:"usage"},{title:"价格依据",dataIndex:"price"},{title:"费用",dataIndex:"cost"}]} /><div className="obs-notice">设计规则：服务商实际账单、按版本单价的估算值和未知费用需分开；Token、页数、请求数等不可混加。失败、取消及重试产生的调用也需计入。</div></div></>}
  <Drawer className="obs-drawer" title={domain === "build" ? "构建任务详情" : domain === "chat" ? "对话轮次详情" : "Agent 运行详情"} open={!!sample} onClose={()=>setSample(null)} size={850}>
   {sample && <><div className="obs-detail-head"><Space><Tag color={statusColor[sample.status]}>{sample.status}</Tag><Tag>模拟数据</Tag><code>{sample.id}</code></Space><h2>{sample.subject}</h2><p>{sample.name} · {sample.detail}</p></div>
    <Descriptions size="small" column={{xs:1,sm:2}} items={[{key:"time",label:"开始时间",children:sample.time},{key:"duration",label:"总耗时",children:sample.duration},{key:"usage",label:"总用量",children:sample.usage},{key:"source",label:"记录来源",children:"演示快照"}]} />
    <div className="obs-detail-grid"><div><h3>执行链路</h3><p className="obs-hint">选择节点查看输入、输出与异常</p><div className="obs-trace">{sample.events.map((e,i)=><button key={e.id} className={`obs-event ${currentEvent?.id===e.id?"selected":""}`} onClick={()=>{setEventId(e.id);setDetailTab("输入输出")}}><span className={`obs-dot ${e.status === "失败" ? "error" : ""}`}></span><span><small>{String(i+1).padStart(2,"0")} · {e.kind}</small><strong>{e.name}</strong><small>{e.status} · {e.duration}</small></span></button>)}</div></div>
    <div className="obs-node"><h3>{currentEvent?.name}</h3><Segmented size="small" value={detailTab} onChange={v=>setDetailTab(String(v))} options={["输入输出","上下文","用量与异常"]} />{currentEvent && (detailTab === "输入输出" ? <><h4>输入</h4><pre>{currentEvent.input}</pre><h4>输出</h4><pre>{currentEvent.output}</pre></> : detailTab === "上下文" ? <><h4>调用上下文</h4><pre>{currentEvent.context}</pre><p className="obs-hint">正式实现时展示可用的快照、版本、来源和截断标记；权限不足时脱敏。</p></> : <><h4>节点用量</h4><pre>{currentEvent.usage}</pre><h4>异常</h4><pre>{currentEvent.error || "本示例节点未记录异常。"}</pre></>)}</div></div>
    {sample.id === "chat_demo_01" && <Button onClick={()=>{setSample(null);switchDomain("agent");open(agents[0])}}>查看关联 Agent 运行</Button>}
   </>}
  </Drawer>
 </div>;
}
