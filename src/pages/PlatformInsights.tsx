import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Empty, Segmented, Space, Table, Tag } from "antd";
import { useStore } from "../store-context";
import { PageHeading } from "../components";
import { api } from "../live-api";
import { numberText } from "../demo";

type Trend = {date:string; calls:number; failed:number; noEvidence:number; negative:number};
type Application = {id:string; name:string; calls:number; failed:number; noEvidence:number; positive:number; negative:number};
type Issue = {id:string; createdAt:string; query:string; name:string; reasons:string[]; caseLabel?:string};
type Summary = {period:{days:number;start:string;end:string;timezone:string}; scope:"all"|"own"; total:number; conversations:number; statuses:{completed:number;no_evidence:number;failed:number;canceled:number;running:number;other:number}; feedback:{positive:number;negative:number;eligible:number}; usage:{knownTokens:number;knownCalls:number;unknownCalls:number}; averageDuration:number|null; durationCount:number; trend:Trend[]; applications:Application[]; applicationCount:number; issues:Issue[]; issueCount:number; caseCounts:{good:number;bad:number}};
const percent = (n:number, d:number) => d ? `${(n / d * 100).toFixed(1)}%` : "暂无";
export function PlatformInsights() {
 const {state,mode}=useStore();
 const sequence=useRef(0);
 const [days,setDays]=useState(7); const [data,setData]=useState<Summary|null>(null); const [error,setError]=useState(""); const [loading,setLoading]=useState(false);
 const load=useCallback(async(signal?:AbortSignal)=>{
  const request=++sequence.current; setLoading(true);
  try { const next=await api<Summary>(`/insights/summary?days=${days}`,state.role,{signal}); if(!signal?.aborted&&request===sequence.current){setData(next);setError("")} }
  catch(e){if(!signal?.aborted&&request===sequence.current){setData(null);setError(e instanceof Error?e.message:"运营数据加载失败")}}
  finally{if(!signal?.aborted&&request===sequence.current)setLoading(false)}
 },[days,state.role]);
 useEffect(()=>{ if(mode!=="live"){setData(null);return} const controller=new AbortController(); setData(null);void load(controller.signal); const timer=window.setInterval(()=>void load(controller.signal),30000);return()=>{sequence.current++;controller.abort();window.clearInterval(timer)} },[load,mode]);
 const votes=(data?.feedback.positive||0)+(data?.feedback.negative||0);const max=Math.max(1,...(data?.trend.map(x=>x.calls)||[]));
 return <><PageHeading eyebrow="INSIGHTS" title="运营看板" description="了解真实使用、回答反馈和需要排查的问题。" actions={<Space wrap><Segmented aria-label="统计时间" value={days} onChange={v=>setDays(Number(v))} options={[{value:7,label:"近 7 天"},{value:30,label:"近 30 天"},{value:90,label:"近 90 天"}]}/>{mode==="live"&&<Button onClick={()=>void load()} loading={loading}>刷新</Button>}</Space>}/>
 {mode!=="live"?<Alert type="info" showIcon title="切换到真实后端查看运营数据" description="运营看板只统计实际运行记录。"/>:error?<Alert type="error" showIcon title="运营数据加载失败" description={error} action={<Button onClick={()=>void load()}>重试</Button>}/>:!data?<p>正在加载运营数据…</p>:<>
 <Alert type="info" showIcon title={`${data.period.start} 至 ${data.period.end} · 北京时间`} description={`${data.scope==="all"?"管理员可见的全部对话":"仅当前账号或会话可见的对话"}；不含评测、草稿调试和检索调试。历史日志清理后，相应统计也会变化。`}/>
 <div className="insight-metrics">
  <div><span>对话调用</span><strong>{numberText(data.total)}</strong><small>{numberText(data.conversations)} 个会话，非独立用户数</small></div>
  <div><span>用户反馈有帮助</span><strong>{percent(data.feedback.positive,votes)}</strong><small>{data.feedback.positive} 有帮助 / {votes} 条反馈</small></div>
  <div><span>待排查记录</span><strong>{numberText(data.issueCount)}</strong><small>失败、依据不足或用户反馈未解决，重叠只计一次</small></div>
  <div><span>已知 Token</span><strong>{data.usage.knownCalls?numberText(data.usage.knownTokens):"未知"}</strong><small>{data.usage.unknownCalls} 次用量未知，未计入</small></div>
 </div>
 <div className="insight-grid"><section className="obs-panel insight-panel"><div className="insight-panel-head"><div><h2>使用趋势</h2><p>按对话发起日期统计；今天可能尚未结束。</p></div></div>{data.total===0?<Empty description="所选时间内没有对话记录"/>:<div className="insight-chart-scroll"><div className="insight-chart" style={{minWidth:Math.max(420,data.trend.length*24)}}>{data.trend.map(day=><div className="insight-bar-column" key={day.date} title={`${day.date}：${day.calls} 次调用，${day.failed} 次失败，${day.noEvidence} 次依据不足，${day.negative} 条未解决反馈`}><div className="insight-bar-track"><div className="insight-bar" style={{height:`${day.calls/max*100}%`}}/></div><small>{data.period.days===7||day.date.endsWith("-01")||day===data.trend.at(-1)?day.date.slice(5):""}</small></div>)}</div></div>}</section>
 <section className="obs-panel insight-panel"><div className="insight-panel-head"><div><h2>回答情况</h2><p>反馈率按可反馈的已完成回答计算。</p></div></div><dl className="insight-stat-list"><div><dt>正常完成</dt><dd>{data.statuses.completed}</dd></div><div><dt>依据不足</dt><dd>{data.statuses.no_evidence}</dd></div><div><dt>运行失败</dt><dd>{data.statuses.failed}</dd></div><div><dt>已取消 / 运行中 / 其他</dt><dd>{data.statuses.canceled} / {data.statuses.running} / {data.statuses.other}</dd></div><div><dt>反馈覆盖</dt><dd>{votes} / {data.feedback.eligible} · {percent(votes,data.feedback.eligible)}</dd></div><div><dt>平均耗时</dt><dd>{data.averageDuration===null?"暂无":`${data.averageDuration.toFixed(2)} 秒`}</dd></div></dl><p className="insight-note">有帮助率仅代表主动反馈的回答；没有反馈不代表满意。</p></section></div>
 <section className="obs-panel insight-panel"><div className="insight-panel-head"><div><h2>应用与助手使用</h2><p>按调用次数排序，最多展示 20 项；同名历史助手会合并。</p></div><Tag>共 {data.applicationCount} 项</Tag></div><Table<Application> rowKey="id" size="middle" pagination={false} scroll={{x:700}} dataSource={data.applications} locale={{emptyText:"所选时间内暂无使用记录"}} columns={[{title:"应用 / 助手",dataIndex:"name"},{title:"调用",dataIndex:"calls"},{title:"失败",dataIndex:"failed"},{title:"依据不足",dataIndex:"noEvidence"},{title:"有帮助 / 未解决",render:(_,r)=>`${r.positive} / ${r.negative}`}]} /></section>
 <section className="obs-panel insight-panel"><div className="insight-panel-head"><div><h2>待排查问题</h2><p>依据不足、失败或用户反馈未解决的最近 20 条；可以进入日志收录案例。</p></div><Space><Tag>{data.issueCount} 条</Tag><Link to="/evaluation">评测集</Link></Space></div><Table<Issue> rowKey="id" size="middle" pagination={false} scroll={{x:700}} dataSource={data.issues} locale={{emptyText:"所选时间内没有待排查记录"}} columns={[{title:"问题",render:(_,r)=><Link to={`/logs?type=chat&runId=${encodeURIComponent(r.id)}`}>{r.query||"未记录问题"}</Link>},{title:"应用 / 助手",dataIndex:"name"},{title:"排查原因",render:(_,r)=><Space wrap>{r.reasons.map(reason=><Tag key={reason}>{reason}</Tag>)}</Space>},{title:"案例",render:(_,r)=>r.caseLabel==="good"?"Good Case":r.caseLabel==="bad"?"Bad Case":"未收录"}]} /></section>
 <Alert style={{marginTop:16}} type="info" showIcon title="指标口径" description={`案例数按本期对话关联的当前案例分类：Good ${data.caseCounts.good}，Bad ${data.caseCounts.bad}；这些是人工收录，不代表全部问题。费用以可观测中心估算和导入账单为准；真实独立用户数暂不展示。`}/>
 </>}</>;
}
